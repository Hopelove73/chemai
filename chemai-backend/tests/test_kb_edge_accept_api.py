"""KB：边采纳 REST 端点（T12 的 UI 通道）——
PUT /api/knowledge-map/edges/{id}/accept 把 proposed 转 accepted（带环校验），
成环走 409 带环路径说明。

鉴权：真 token 流程（create_access_token + 当前用户依赖覆写），照
test_api_auth_notification 的夹具模式；PermissionChecker 以依赖注入时
读取 current_user，测试用 override 提供。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth import create_access_token


@pytest.fixture()
def client(tmp_path, patch_get_db, monkeypatch):
    from app.api import knowledge_map as km_mod

    e = create_engine(
        f"sqlite:///{tmp_path}/api.db",
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with e.begin() as conn:
        conn.execute(text("""
            CREATE TABLE knowledge_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_kp VARCHAR(128) NOT NULL, to_kp VARCHAR(128) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                reason TEXT, proposed_by VARCHAR(64), created_at TIMESTAMP,
                edge_type VARCHAR(32), confidence FLOAT, source_doc VARCHAR(256)
            )
        """))
        conn.execute(text(
            "INSERT INTO knowledge_edges (from_kp, to_kp, status, edge_type) "
            "VALUES ('A', 'B', 'active', 'prerequisite')"))
        conn.execute(text(
            "INSERT INTO knowledge_edges (from_kp, to_kp, status, edge_type, reason) "
            "VALUES ('B', 'A', 'proposed', 'prerequisite', '反提成环')"))
        conn.execute(text(
            "INSERT INTO knowledge_edges (from_kp, to_kp, status, edge_type, reason) "
            "VALUES ('B', 'C', 'proposed', 'prerequisite', '正常提议')"))

    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=e, expire_on_commit=False)

    def override_get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    # 端点只依赖 PermissionChecker('question','update') 放行；它内部读
    # current_user（Request.state），测试里以依赖覆写直接给出。
    from app.middleware.auth import PermissionChecker
    app.dependency_overrides[km_mod.get_db] = override_get_db
    app.dependency_overrides[
        PermissionChecker("question", "update")] = lambda: {"user_id": "teacher_zhou"}
    token = create_access_token("teacher_zhou", "subject_lead", "school_cs_001")
    with __import__("contextlib").closing(TestClient(app)) as c:
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c, e
    app.dependency_overrides.clear()
    e.dispose()


def test_accept_proposed_edge(client):
    tc, engine = client
    r = tc.put("/api/knowledge-map/edges/3/accept")
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "active"
    with engine.connect() as conn:
        st = conn.execute(text("SELECT status FROM knowledge_edges WHERE id=3")).scalar()
        assert st == "active"


def test_accept_cycle_edge_rejected_409(client):
    tc, engine = client
    r = tc.put("/api/knowledge-map/edges/2/accept")
    assert r.status_code == 409, r.text
    assert "环" in r.json()["detail"]
    with engine.connect() as conn:
        st = conn.execute(text("SELECT status FROM knowledge_edges WHERE id=2")).scalar()
        assert st == "proposed", "成环提议保持 proposed（老师要在 UI 上看到它被拒）"


def test_accept_missing_edge_404(client):
    tc, _ = client
    assert tc.put("/api/knowledge-map/edges/999/accept").status_code == 404
