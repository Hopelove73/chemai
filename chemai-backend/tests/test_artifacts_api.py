"""A1 —— 工件实体与服务（capability-upgrade-spec 接缝 1：Artifact API）。

三个真源口径锁定在这里：
1. 文件真源在盘上（默认工作区 AI生成/artifacts/<日期>/），库表只存索引；
2. 每次保存追加不可变版本行，旧版本文件永不覆盖（快照式迭代，定案 R6）；
3. 归属收窄：工件是老师跨会话资产，别人的工件在列表与详情里都不存在（404，
   与 gates 面的「登记在别人名下不出现」同一口吻）。
"""

import re
from datetime import datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth import create_access_token
from app.models.database import Artifact, Base, get_db
from app.services import artifact_service


@pytest.fixture
def db_session(tmp_path, monkeypatch):
    """StaticPool 内存库 + 工作区指到 tmp（不许碰真实 ~/ChemAI-Workspace）。"""
    monkeypatch.setenv("CHEMAI_WORKSPACE", str(tmp_path / "ws"))
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _save(db, teacher="t-1", kind="animation", title="盐类水解教学动画",
          content="<html>动画 v1</html>", **kw):
    return artifact_service.save_artifact(
        db, owner_teacher_id=teacher, kind=kind, title=title, content=content, **kw)


# ── 服务层：版本链与落盘 ────────────────────────────────────────────────

def test_save_creates_artifact_with_v1_file_on_disk(db_session):
    out = _save(db_session)

    assert out["version_seq"] == 1 and out["created"] is True
    assert out["url"].startswith("/api/artifacts/") and out["url"].endswith("/v/1")
    resolved = artifact_service.resolve_version(db_session, out["artifact_id"], 1, "t-1")
    assert resolved is not None
    path, kind = resolved
    assert kind == "animation"
    assert path.read_text(encoding="utf-8") == "<html>动画 v1</html>"
    row = db_session.get(Artifact, out["artifact_id"])
    assert row.current_version == 1 and row.kind == "animation"
    assert row.owner_teacher_id == "t-1"


def test_save_again_appends_version_and_keeps_old_file(db_session):
    v1 = _save(db_session, content="<html>v1</html>")
    v2 = artifact_service.save_artifact(
        db_session, owner_teacher_id="t-1", kind="animation", title="盐类水解教学动画",
        content="<html>v2 更深底</html>", artifact_id=v1["artifact_id"], note="加深底色")

    assert v2["version_seq"] == 2 and v2["created"] is False
    assert v2["artifact_id"] == v1["artifact_id"]
    # 旧版本文件还在（快照不覆盖）
    p1, _ = artifact_service.resolve_version(db_session, v1["artifact_id"], 1, "t-1")
    p2, _ = artifact_service.resolve_version(db_session, v1["artifact_id"], 2, "t-1")
    assert p1.read_text(encoding="utf-8") == "<html>v1</html>"
    assert p2.read_text(encoding="utf-8") == "<html>v2 更深底</html>"
    assert db_session.get(Artifact, v1["artifact_id"]).current_version == 2
    detail = artifact_service.get_artifact(db_session, v1["artifact_id"], "t-1")
    assert [v["seq"] for v in detail["versions"]] == [1, 2]
    assert detail["versions"][1]["note"] == "加深底色"


def test_invalid_kind_rejected(db_session):
    with pytest.raises(ValueError):
        _save(db_session, kind="video")


def test_list_scoped_to_owner_and_filterable_by_kind(db_session):
    anim = _save(db_session, teacher="t-1", kind="animation", title="动画")
    doc = _save(db_session, teacher="t-1", kind="doc", title="教案")
    _save(db_session, teacher="t-2", kind="animation", title="别人的动画")
    # 显式错开 updated_at：本机 datetime.utcnow() 粒度约 15.6ms，同一 tick 内连续保存
    # 两件工件会得到**完全相同**的 updated_at，排序随即落到 Artifact.id 兜底——而 id 含
    # secrets.token_hex(4)（随机），顺序就成了掷硬币（实测：冻结时钟后 20 次里反 4 次）。
    # 让「教案更晚」由用例明说，这条就只测它要测的：归属隔离 / kind 过滤 / 最近更新在前。
    base = datetime(2026, 1, 1, 12, 0, 0)
    for art_id, secs in ((anim["artifact_id"], 0), (doc["artifact_id"], 60)):
        db_session.get(Artifact, art_id).updated_at = base + timedelta(seconds=secs)
    db_session.commit()

    mine = artifact_service.list_artifacts(db_session, "t-1")
    assert [it["title"] for it in mine] == ["教案", "动画"]  # 最近更新在前
    assert artifact_service.list_artifacts(db_session, "t-2")[0]["title"] == "别人的动画"
    only_anim = artifact_service.list_artifacts(db_session, "t-1", kind="animation")
    assert [it["title"] for it in only_anim] == ["动画"]


def test_other_teacher_cannot_read_or_resolve(db_session):
    out = _save(db_session, teacher="t-1")
    assert artifact_service.get_artifact(db_session, out["artifact_id"], "t-2") is None
    assert artifact_service.resolve_version(
        db_session, out["artifact_id"], 1, "t-2") is None


def test_append_version_to_other_teachers_artifact_rejected(db_session):
    """追版本不是读路径：给别人的工件追 v2 必须当场拒绝（code-review P2-8①）。"""
    out = _save(db_session, teacher="t-1")
    with pytest.raises(LookupError):
        artifact_service.save_artifact(
            db_session, owner_teacher_id="t-2", kind="animation", title="抢注",
            content="<html>x</html>", artifact_id=out["artifact_id"])


def test_kind_immutable_on_append(db_session):
    """动画不能追加成文档——渲染器按 kind 分发，类型变了引用就废了。"""
    out = _save(db_session, teacher="t-1", kind="animation")
    with pytest.raises(ValueError):
        artifact_service.save_artifact(
            db_session, owner_teacher_id="t-1", kind="doc", title="改类型",
            content="# x", artifact_id=out["artifact_id"])


def test_empty_content_rejected(db_session):
    with pytest.raises(ValueError):
        _save(db_session, content="")
    with pytest.raises(ValueError):
        _save(db_session, content=None)


def test_long_title_clamped(db_session):
    out = _save(db_session, title="长" * 400)
    assert len(artifact_service.get_artifact(db_session, out["artifact_id"], "t-1")["title"]) <= 255


# ── HTTP：路由挂载、归属收窄、文件本体 ──────────────────────────────────

def _get(client, path, user_id="t-1", role="teacher"):
    return client.get(path, headers={"Authorization": f"Bearer {create_access_token(user_id, role)}"})


def test_http_list_detail_and_version_file(db_session, client):
    out = _save(db_session)

    resp = _get(client, "/api/artifacts")
    assert resp.status_code == 200
    items = resp.json()["items"]
    assert len(items) == 1 and items[0]["artifact_id"] == out["artifact_id"]

    detail = _get(client, f"/api/artifacts/{out['artifact_id']}")
    assert detail.status_code == 200
    assert detail.json()["current_version"] == 1

    file = _get(client, f"/api/artifacts/{out['artifact_id']}/v/1")
    assert file.status_code == 200
    assert file.text == "<html>动画 v1</html>"
    assert file.headers["content-type"].startswith("text/html")


def test_http_other_teacher_gets_404(db_session, client):
    out = _save(db_session, teacher="t-1")
    assert _get(client, f"/api/artifacts/{out['artifact_id']}",
                user_id="t-2").status_code == 404
    assert _get(client, f"/api/artifacts/{out['artifact_id']}/v/1",
                user_id="t-2").status_code == 404


def test_no_static_side_door(db_session, client):
    """A5 审查 P1：/static/artifacts 无鉴权侧门必须不存在——工件真源只从
    /api 面出（归属/分享收窄）。裸 url 无 token 打不开文件。"""
    out = _save(db_session)
    resp = client.get(out["url"])
    assert resp.status_code in (401, 403)
    # 旧 /static 形状无路由，绝不回工件内容
    legacy = client.get("/static/artifacts/2026-01-01/whatever.html")
    assert legacy.status_code != 200 or "动画 v1" not in legacy.text


def test_contract_kind_sets_match_frontend(db_session):
    """两侧契约（spec 批次 A）：后端 ALLOWED_KINDS 与前端渲染器注册表
    ARTIFACT_KIND_META 的键集合必须一致——直接读前端源文件对照，任何一侧
    漂移都在这条上现形。"""
    assert set(artifact_service.ALLOWED_KINDS) == {
        "animation", "slides", "doc", "structure", "table"}
    fe_src = (Path(__file__).resolve().parent.parent
              / "frontend" / "src" / "components" / "chat" / "MessageList.tsx").read_text(encoding="utf-8")
    m = re.search(r"ARTIFACT_KIND_META[^=]*= \{(.*?)\n\}", fe_src, re.S)
    assert m, "前端渲染器注册表 ARTIFACT_KIND_META 不见了"
    fe_kinds = sorted(re.findall(r"(\w+): \{ label", m.group(1)))
    assert fe_kinds == sorted(artifact_service.ALLOWED_KINDS)


def test_http_requires_teacher_identity(db_session, client):
    assert _get(client, "/api/artifacts", user_id="s-1",
                role="student").status_code == 403
