"""kb-review-ui 后端测试：迁移幂等、批准落库与 CLI 对拍、状态机幂等、审计留痕。

对拍口径（spec：界面批准与 CLI 落库同语义）：同一审校包分别经 API approve 与
直接调 `import_proposals`，两边落库后的图谱边/物质行数必须一致。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.pool import StaticPool

from app.middleware.auth import create_access_token
from app.models.database import Base, OperationLog, get_db
from app.utils.migrate import create_kb_review_records_table

REAL_PACKAGE = "专题方法/化学图像信息题方法.proposals.json"


@pytest.fixture
def engine():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    s = sessionmaker(bind=e, expire_on_commit=False)()
    yield e, s
    s.close()


@pytest.fixture
def client(engine):
    """镜像 app：kb_admin router（逐端点声明门，与 main.py 同构）。"""
    from app.api import kb_admin as kb_admin_mod
    _, session = engine
    app = FastAPI()
    app.include_router(kb_admin_mod.router)

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c


def hdr(role, user_id="A1"):
    return {"Authorization": f"Bearer {create_access_token(user_id, role, school_id='sch1')}"}


def test_migration_idempotent():
    """迁移幂等：连跑两次不炸（spec/tasks 1.2）。"""
    e = pytest.importorskip("sqlalchemy").create_engine("sqlite://")
    create_kb_review_records_table(e)
    create_kb_review_records_table(e)  # 第二次必须无副作用
    with e.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(kb_review_records)")).fetchall()]
    assert "source_path" in cols and "status" in cols


def test_approve_matches_cli_semantics(client, engine):
    """对拍：API approve 与 CLI import_proposals 落库结果一致（tasks 4.1）。"""
    engine_api, session = engine
    resp = client.post("/api/kb/review-packages/approve",
                       json={"path": REAL_PACKAGE}, headers=hdr("admin"))
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"], body

    # CLI 同函数直跑到另一套空库
    engine_cli = pytest.importorskip("sqlalchemy").create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine_cli)
    from app.services.kb_graph_import import import_proposals
    from app.services.kb_dictionary import load_all_dictionaries
    from pathlib import Path
    known = {it["code"] for it in load_all_dictionaries(str(Path("content/kb")))["items"]}
    report_cli = import_proposals(engine_cli, str(Path("content/kb") / REAL_PACKAGE), known)

    def count(e, table):
        with e.connect() as conn:
            return conn.execute(text(f"SELECT COUNT(*) FROM {table}")).fetchone()[0]

    assert count(engine_api, "knowledge_edges") == count(engine_cli, "knowledge_edges")
    assert count(engine_api, "kb_substances") == count(engine_cli, "kb_substances")
    assert body["report"]["edges_proposed"] == report_cli["edges_proposed"]


def test_approve_is_idempotent_and_audited(client, engine):
    """状态机幂等（已 approved 再批 → 409）+ 审计留痕。"""
    _, session = engine
    client.post("/api/kb/review-packages/approve", json={"path": REAL_PACKAGE}, headers=hdr("admin"))

    r = client.post("/api/kb/review-packages/approve", json={"path": REAL_PACKAGE}, headers=hdr("admin"))
    assert r.status_code == 409

    logs = session.query(OperationLog).filter(OperationLog.action == "kb_review_approve").all()
    assert len(logs) == 1 and logs[0].target_id == REAL_PACKAGE

    # 列表里该包状态 = approved
    r = client.get("/api/kb/review-packages", headers=hdr("admin"))
    pkg = next(p for p in r.json()["packages"] if p["path"] == REAL_PACKAGE)
    assert pkg["status"] == "approved"
    assert pkg["content_changed"] is False


def test_reject_records_reason_and_blocks_after_approve(client, engine):
    """驳回留痕；已批准不可驳回。"""
    _, session = engine
    r = client.post("/api/kb/review-packages/reject",
                    json={"path": REAL_PACKAGE, "reason": "先修方向存疑"},
                    headers=hdr("admin"))
    assert r.status_code == 200
    rec = client.get("/api/kb/review-packages", headers=hdr("admin")).json()["packages"]
    pkg = next(p for p in rec if p["path"] == REAL_PACKAGE)
    assert pkg["status"] == "rejected" and pkg["reason"] == "先修方向存疑"

    # 驳回后批准被拒（任务 2.3：驳回状态也终结）
    r = client.post("/api/kb/review-packages/approve", json={"path": REAL_PACKAGE}, headers=hdr("admin"))
    assert r.status_code == 409


def test_path_guard_rejects_escape(client):
    """越界防护：.. / 绝对路径 / 非 proposals 文件一律 400。"""
    for bad in ["../chemai.db", "专题方法/../../app/main.py", "kp_dictionary_m1.json", "/etc/passwd"]:
        r = client.get("/api/kb/review-packages/detail", params={"path": bad}, headers=hdr("admin"))
        assert r.status_code == 400, bad


def test_parse_error_is_structured_not_500(client, engine):
    """坏 JSON 审校包 → 结构化 parse_error，不 500（design D3）。"""
    _, session = engine
    target = __import__("pathlib").Path("content/kb/专题方法")
    probe = target / "zzz_test_bad_parse.proposals.json"
    probe.write_text("{不是JSON", encoding="utf-8")
    try:
        r = client.get("/api/kb/review-packages/detail",
                       params={"path": "专题方法/zzz_test_bad_parse.proposals.json"},
                       headers=hdr("admin"))
        assert r.status_code == 200
        assert r.json()["package"]["parse_error"] is not None
    finally:
        probe.unlink(missing_ok=True)
