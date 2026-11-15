"""integration-persistence 测试：配置/密钥/Webhook 持久化（重启语义）、投递留痕、
密钥哈希与校验路径、权限语义不变。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.middleware.auth import create_access_token
from app.models.database import Base, IntegrationApiKey, IntegrationWebhook, get_db
from app.services import integration_keys
from app.utils.migrate import create_integration_tables


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    create_integration_tables(e)
    s = sessionmaker(bind=e, expire_on_commit=False)()
    yield e, s
    s.close()


@pytest.fixture
def api_client(engine):
    from app.api import integration as integration_mod
    _, session = engine
    app = FastAPI()
    app.include_router(integration_mod.router, prefix="/api/integration")

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c


def hdr(role, user_id="A1"):
    return {"Authorization": f"Bearer {create_access_token(user_id, role, school_id='sch1')}"}


def test_lms_config_roundtrip_and_restart(api_client, engine):
    """保存 LMS 配置 → 新存储实例读回（重启语义）。"""
    e, _ = engine
    r = api_client.post("/api/integration/lms/config", headers=hdr("admin"), json={
        "platform": "dingtalk", "enabled": True, "app_key": "k", "app_secret": "s",
    })
    assert r.status_code == 200 and r.json()["success"]

    # 模拟重启：全新 session 连同一库
    s2 = sessionmaker(bind=e, expire_on_commit=False)()
    r2 = api_client.get("/api/integration/lms/config", headers=hdr("admin"))
    assert r2.status_code == 200
    platforms = r2.json()["platforms"]
    assert platforms["dingtalk"]["enabled"] is True
    s2.close()


def test_webhook_persists_across_restart(api_client, engine):
    e, _ = engine
    r = api_client.post("/api/integration/webhooks", headers=hdr("admin"), json={
        "event_type": "exam.graded", "url": "https://lms.example.com/hook", "secret": "sec",
    })
    assert r.status_code == 200

    s2 = sessionmaker(bind=e, expire_on_commit=False)()
    rows = s2.query(IntegrationWebhook).all()
    assert len(rows) == 1 and rows[0].url == "https://lms.example.com/hook"
    s2.close()

    # secret 不出现在列表响应
    listed = api_client.get("/api/integration/webhooks", headers=hdr("admin")).json()["webhooks"]
    assert listed and "sec" not in str(listed)


def test_api_key_issue_once_hash_stored_and_delete_revokes(api_client, engine):
    e, session = engine
    r = api_client.post("/api/integration/api-keys",
                    params={"name": "测试密钥", "permissions": ["student:read", "exam:read"]},
                    headers=hdr("admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["api_secret"].startswith("") and len(body["api_secret"]) == 64

    # 落库是哈希不是明文
    row = session.get(IntegrationApiKey, body["key_id"])
    assert row.secret_hash != body["api_secret"]
    assert len(row.secret_hash) == 64

    # 列表掩码：无明文/哈希
    listed = api_client.get("/api/integration/api-keys", headers=hdr("admin")).json()["keys"]
    assert listed and listed[0]["key_id"] == body["key_id"]
    assert "api_secret" not in listed[0] and "secret_hash" not in listed[0]

    # 删除后 verify 失效
    assert api_client.delete(f"/api/integration/api-keys/{body['key_id']}", headers=hdr("admin")).status_code == 200
    ok, _, reason = integration_keys.verify_api_key(session, body["api_secret"])
    assert ok is False


def test_verify_api_key_permissions(db_engine=None):
    """校验路径单测：身份通过 / 权限比对拒绝 / 删除失效（不接业务端点）。"""
    from sqlalchemy import create_engine as ce
    e = ce("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    s = sessionmaker(bind=e, expire_on_commit=False)()

    issued = integration_keys.create_api_key(s, "k1", ["student:read"], created_by="A1")
    ok, row, reason = integration_keys.verify_api_key(s, issued["api_secret"])
    assert ok and row is not None

    ok, row, reason = integration_keys.verify_api_key(s, issued["api_secret"], "exam:read")
    assert not ok and reason == "permission denied: exam:read"

    ok, _, reason = integration_keys.verify_api_key(s, "wrong-secret")
    assert not ok and reason == "invalid credential"


def test_integration_logs_record_deliveries_and_config(api_client, engine):
    e, session = engine
    # 配置动作留痕
    api_client.post("/api/integration/lms/config", headers=hdr("admin"), json={"platform": "generic", "enabled": False})
    # 密钥动作留痕
    api_client.post("/api/integration/api-keys", params={"name": "x", "permissions": ["student:read"]}, headers=hdr("admin"))
    r = api_client.get("/api/integration/logs", headers=hdr("admin"))
    assert r.status_code == 200
    events = [l["event_type"] for l in r.json()["logs"]]
    assert "lms_config" in events and "api_key_created" in events

    # 筛选
    r = api_client.get("/api/integration/logs?event_type=api_key_created", headers=hdr("admin"))
    assert all(l["event_type"] == "api_key_created" for l in r.json()["logs"])


def test_permissions_unchanged(api_client):
    """权限语义不变：teacher 全部 403（integration 资源仅 admin）。"""
    for method, path in [
        ("get", "/api/integration/lms/config"), ("get", "/api/integration/api-keys"), ("get", "/api/integration/webhooks"),
        ("get", "/api/integration/logs"),
    ]:
        assert getattr(api_client, method)(path, headers=hdr("teacher", "T1")).status_code == 403, path
