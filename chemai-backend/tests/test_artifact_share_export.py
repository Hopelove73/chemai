"""A5 —— 工件分享（确认门）+ 学生取回 + .pptx 导出。

口径：
- 分享的工具体（share_artifact）= 批准后的**实际分发**；门卡由 TOOL_APPROVAL_REQUIRED
  在 guard 层自动挂（这里直接调函数 = 门批准后的重放路径）。
- 学生取数 fail-closed：收件箱里没有 artifact_share 通知 = 404；有 = 原文件。
- 导出是老师自己的文件（R6 只管对外分发，导出不过门）；无文本结构的工件出占位页。
"""
import asyncio
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth import create_access_token
from app.models.database import Base, StudentNotification, get_db
from agent.tools.teaching import share_artifact


@pytest.fixture(autouse=True)
def _shadow_tables():
    """share_artifact 工具走 get_db() 默认引擎（影子库快照没有新表）——补建。
    分享类测试的写与查都落在这一条引擎上，与端点测试的内存库互不相干。"""
    from app.models.database import Base, engine
    Base.metadata.create_all(engine)
    yield


def _seed_student(student="s-1"):
    """send_to_student 会验学生存在——影子库里种一个（class_id 非空，先种班级）。"""
    from app.models.database import Class, Student
    db = _shadow_session()
    try:
        if db.get(Class, "cls-dbg") is None:
            db.add(Class(class_id="cls-dbg", name="调试班", grade="高一", subject="化学"))
            db.commit()
        if db.get(Student, student) is None:
            db.add(Student(student_id=student, name="测试学生", class_id="cls-dbg",
                           status="approved"))
            db.commit()
    finally:
        db.close()


def _shadow_session():
    from sqlalchemy.orm import Session
    from app.models.database import engine
    return Session(bind=engine)


@pytest.fixture(autouse=True)
def _identity():
    """share_artifact 走身份 contextvar（门批准重放时由通道注入）；测试里直接定。"""
    from agent.tools import context as ctx
    ctx.set_current_identity("t-1", "teacher")
    yield
    ctx.set_current_identity("")   # contextvar 跨测试存活，teardown 必须清掉自己设的身份


@pytest.fixture
def db_session(tmp_path, monkeypatch):
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


def _auth(user="t-1", role="teacher"):
    return {"Authorization": f"Bearer {create_access_token(user, role)}"}


def _artifact(db=None, teacher="t-1", title="盐类水解动画",
              html="<!DOCTYPE html><html><body><h1>概述</h1><p>要点一</p></body></html>"):
    """创建一份工件，返回 save_artifact 的引用 dict。db 缺省用影子会话（工具同款引擎）。"""
    from app.services import artifact_service
    owned = db is None
    if owned:
        db = _shadow_session()
    try:
        return artifact_service.save_artifact(
            db, owner_teacher_id=teacher, kind="animation", title=title, content=html,
            source_skill="teaching_animation")
    finally:
        if owned:
            db.close()


def _share_record(db, artifact_id, student="s-1", title="盐类水解动画", version=1):
    owned = db is None
    if owned:
        db = _shadow_session()
    try:
        db.add(StudentNotification(
            notification_id=f"snt-x-{student}-{artifact_id}-v{version}", student_id=student,
            type="artifact_share", title=f"老师分享了教学动画：{title}",
            content=json.dumps({"artifact_id": artifact_id, "kind": "animation",
                                "title": title, "version": version}),
            is_read=False))
        db.commit()
    finally:
        if owned:
            db.close()


# ── share_artifact（门批准后的重放路径） ────────────────────────────────

def test_share_delivers_to_student_inbox(db_session):
    _seed_student()
    art = _artifact()

    async def run():
        return await share_artifact(artifact_id=art["artifact_id"], student_id="s-1")
    out = json.loads(asyncio.run(run()))

    assert "已分享" in out["message"]
    assert out["shared"]["version"] == 1
    db = _shadow_session()
    try:
        notes = (db.query(StudentNotification)
                 .filter(StudentNotification.student_id == "s-1",
                         StudentNotification.type == "artifact_share").all())
        assert len(notes) == 1
        assert json.loads(notes[0].content)["artifact_id"] == art["artifact_id"]
    finally:
        db.close()


def test_share_idempotent_per_version(db_session):
    """门重放/重复分享同一工件同版本 → 只投递一次（断言圈定本测试的工件，
    影子库跨运行持久，不能数全表）。"""
    _seed_student()
    art = _artifact()

    async def run():
        return await share_artifact(artifact_id=art["artifact_id"], student_id="s-1")
    asyncio.run(run())
    asyncio.run(run())  # 门重放/重复分享

    db = _shadow_session()
    try:
        notes = (db.query(StudentNotification)
                 .filter(StudentNotification.type == "artifact_share").all())
        mine = [n for n in notes
                if json.loads(n.content or "{}").get("artifact_id") == art["artifact_id"]]
        assert len(mine) == 1  # 幂等键去重
    finally:
        db.close()


def test_share_other_teachers_artifact_rejected(db_session):
    art = _artifact(db_session, teacher="t-2")

    async def run():
        return await share_artifact(artifact_id=art["artifact_id"], student_id="s-1")
    out = json.loads(asyncio.run(run()))
    assert "不属于你" in out["error"]


def test_share_missing_student_rejected(db_session):
    art = _artifact()  # 不种 ghost 学生，分享应报“学生不存在”

    async def run():
        return await share_artifact(artifact_id=art["artifact_id"], student_id="ghost")
    out = json.loads(asyncio.run(run()))
    assert "学生不存在" in out["error"]


# ── 学生取数端点 ────────────────────────────────────────────────────────

def test_shared_view_requires_share_record(db_session, client):
    art = _artifact(db_session)
    _share_record(db_session, art["artifact_id"])

    ok = client.get(f"/api/artifacts/shared/{art['artifact_id']}", headers=_auth("s-1", "student"))
    assert ok.status_code == 200
    assert ok.json()["title"] == "盐类水解动画"

    # 没被分享的学生 → 404（fail-closed，不是 403：不泄露存在性）
    denied = client.get(f"/api/artifacts/shared/{art['artifact_id']}",
                        headers=_auth("s-2", "student"))
    assert denied.status_code == 404


def test_shared_file_served_to_shared_student(db_session, client):
    art = _artifact(db_session)
    _share_record(db_session, art["artifact_id"])

    meta = client.get(f"/api/artifacts/shared/{art['artifact_id']}",
                      headers=_auth("s-1", "student"))
    seq = meta.json()["current_version"]
    file = client.get(f"/api/artifacts/shared/{art['artifact_id']}/v/{seq}",
                      headers=_auth("s-1", "student"))
    assert file.status_code == 200
    assert "概述" in file.text
    # 未分享学生拿不到文件
    assert client.get(f"/api/artifacts/shared/{art['artifact_id']}/v/{seq}",
                      headers=_auth("s-2", "student")).status_code == 404


def test_teacher_still_uses_owner_path(db_session, client):
    art = _artifact(db_session)
    file = client.get(f"/api/artifacts/{art['artifact_id']}/v/1", headers=_auth("t-1"))
    assert file.status_code == 200


def test_share_artifact_in_gate_registry():
    """门接缝（A5 审查 P2-9）：对外分发必须挂在 TOOL_APPROVAL_REQUIRED 上——
    agent 调它先弹确认卡，批准后才执行本文件测的重放路径。"""
    from agent.guard import TOOL_APPROVAL_REQUIRED
    assert "share_artifact" in TOOL_APPROVAL_REQUIRED


def test_shared_endpoint_other_teacher_404(db_session, client):
    """shared 端点的教师分支走归属校验：别人的工件 404（此前零覆盖）。"""
    art = _artifact(db_session, teacher="t-2")
    assert client.get(f"/api/artifacts/shared/{art['artifact_id']}",
                      headers=_auth("t-1")).status_code == 404


def test_student_pinned_to_shared_version(db_session, client):
    """P2-4：教师批准 v1 后又生成 v2，学生无新门决策——v1 可见、v2 不可见、
    元数据的 current_version 也停在 v1。"""
    art = _artifact(db_session)
    _share_record(db_session, art["artifact_id"], version=1)
    # 教师续出 v2（无新分享）
    from app.services import artifact_service
    artifact_service.save_artifact(db_session, owner_teacher_id="t-1",
                                   kind="animation", title=art["title"],
                                   content="<!DOCTYPE html><html><body>v2</body></html>",
                                   artifact_id=art["artifact_id"], note="v2")

    meta = client.get(f"/api/artifacts/shared/{art['artifact_id']}",
                      headers=_auth("s-1", "student"))
    assert meta.status_code == 200
    assert meta.json()["current_version"] == 1

    assert client.get(f"/api/artifacts/shared/{art['artifact_id']}/v/1",
                      headers=_auth("s-1", "student")).status_code == 200
    assert client.get(f"/api/artifacts/shared/{art['artifact_id']}/v/2",
                      headers=_auth("s-1", "student")).status_code == 404


def test_export_kind_restricted(db_session, client):
    """P2-6：大纲 deck 只对文字型工件有意义——structure 导出 400 而不是 500。"""
    from app.services import artifact_service
    art = artifact_service.save_artifact(
        db_session, owner_teacher_id="t-1", kind="structure", title="苯环",
        content=b"PNG-fake-bytes")
    resp = client.get(f"/api/artifacts/{art['artifact_id']}/export", headers=_auth("t-1"))
    assert resp.status_code == 400


# ── .pptx 导出 ──────────────────────────────────────────────────────────

def test_export_pptx_outline(db_session, client):
    from app.services import artifact_service as _svc
    art = _svc.save_artifact(
        db_session, owner_teacher_id="t-1", kind="doc", title="期中讲评稿",
        content="<!DOCTYPE html><html><body><h1>概览</h1><p>正确率 62%</p></body></html>",
        source_skill="review_deck")
    resp = client.get(f"/api/artifacts/{art['artifact_id']}/export", headers=_auth("t-1"))
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith(
        "application/vnd.openxmlformats-officedocument.presentationml.presentation")
    assert len(resp.content) > 1000  # 真 zip（pptx 是 OOXML 包），不是空壳
    # pptx 是 zip：魔数 PK
    assert resp.content[:2] == b"PK"


def test_export_other_teachers_artifact_404(db_session, client):
    art = _artifact(db_session, teacher="t-2")
    assert client.get(f"/api/artifacts/{art['artifact_id']}/export",
                      headers=_auth("t-1")).status_code == 404
