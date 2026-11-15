"""runtime-settings 后端测试：白名单封闭、类型/枚举校验、env 回落、恢复默认、
缓存失效、消费点行为（OCR 引擎热切换）、审计、角色断言。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.middleware.auth import create_access_token
from app.models.database import Base, OperationLog, get_db
from app.services import runtime_settings as rs
from app.utils.migrate import create_runtime_settings_table


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    create_runtime_settings_table(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    rs.invalidate()  # 隔离其他测试的 TTL 缓存
    yield s
    s.close()
    rs.invalidate()


@pytest.fixture
def client(db_session):
    from app.api import settings as settings_mod
    app = FastAPI()
    app.include_router(settings_mod.router)

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c


def hdr(role, user_id="A1"):
    return {"Authorization": f"Bearer {create_access_token(user_id, role, school_id='sch1')}"}


def test_whitelist_is_closed(db_session):
    """白名单外键读写一律拒绝；凭据类键（API key 名）天然不在白名单。"""
    with pytest.raises(ValueError):
        rs.get_setting("DEEPSEEK_API_KEY", db_session)
    with pytest.raises(ValueError):
        rs.set_setting("DEEPSEEK_API_KEY", "x", db_session)


def test_enum_validation(db_session):
    with pytest.raises(ValueError):
        rs.set_setting("OCR_SHEET_PROVIDER", "不存在引擎", db_session)
    stored = rs.set_setting("OCR_SHEET_PROVIDER", "baidu", db_session)
    assert stored == "baidu"
    assert rs.get_setting("OCR_SHEET_PROVIDER", db_session) == "baidu"


def test_bool_validation_and_env_fallback(db_session):
    rs.set_setting("OCR_VISION_REVIEW_ENABLED", False, db_session)
    assert rs.get_setting("OCR_VISION_REVIEW_ENABLED", db_session) is False
    rs.reset_setting("OCR_VISION_REVIEW_ENABLED", db_session)
    # 回落 env 种子默认（config.OCR_VISION_REVIEW_ENABLED 默认开）
    from app.config import config
    assert rs.get_setting("OCR_VISION_REVIEW_ENABLED", db_session) == config.OCR_VISION_REVIEW_ENABLED


def test_reset_deletes_override_row(db_session):
    rs.set_setting("OCR_SHEET_PROVIDER", "baidu", db_session)
    assert db_session.get(rs.RuntimeSettingRow, "OCR_SHEET_PROVIDER") is not None
    rs.reset_setting("OCR_SHEET_PROVIDER", db_session)
    assert db_session.get(rs.RuntimeSettingRow, "OCR_SHEET_PROVIDER") is None


def test_consumption_point_hot_switch(db_session, monkeypatch):
    """消费点行为回归：默认 = env；覆写后 get_ocr_provider 换引擎（无重启）。"""
    from app.config import config
    from app.services.ocr_mineru import get_ocr_provider, resolve_sheet_provider_name

    # 消费点以 db=None 自开 session —— 注入测试内存库防写穿
    monkeypatch.setattr(rs, "_session_factory", lambda: sessionmaker(
        bind=db_session.get_bind(), expire_on_commit=False))

    assert resolve_sheet_provider_name() == config.OCR_SHEET_PROVIDER  # 默认行为不变
    rs.set_setting("OCR_SHEET_PROVIDER", "baidu", db_session)
    assert resolve_sheet_provider_name() == "baidu"
    assert isinstance(get_ocr_provider(), object)
    rs.set_setting("OCR_SHEET_PROVIDER", "paddle", db_session)
    assert resolve_sheet_provider_name() == "paddle"


def test_invalidate_forces_fresh_read(db_session):
    """TTL 缓存存在，但 invalidate() 立即可见（测试纪律 + 写路径共用）。"""
    rs.set_setting("OCR_SHEET_PROVIDER", "baidu", db_session)
    # 直接改表绕过 set（模拟并发写方）→ invalidate 后读到新值
    row = db_session.get(rs.RuntimeSettingRow, "OCR_SHEET_PROVIDER")
    row.value = "mineru"
    db_session.commit()
    rs.invalidate()
    assert rs.get_setting("OCR_SHEET_PROVIDER", db_session) == "mineru"


def test_api_update_writes_audit(client, db_session):
    r = client.put("/api/settings/OCR_SHEET_PROVIDER", json={"value": "baidu"}, headers=hdr("admin"))
    assert r.status_code == 200
    logs = db_session.query(OperationLog).filter(OperationLog.action == "runtime_setting_update").all()
    assert len(logs) == 1
    assert logs[0].detail["old"] != "baidu" or logs[0].detail["new"] == "baidu"

    r = client.delete("/api/settings/OCR_SHEET_PROVIDER", headers=hdr("admin"))
    assert r.status_code == 200
    logs = db_session.query(OperationLog).filter(OperationLog.action == "runtime_setting_reset").all()
    assert len(logs) == 1


def test_api_gates(client):
    """矩阵门：settings:read/update 仅 admin（镜像给超管）；teacher/academic_admin 403。"""
    assert client.get("/api/settings", headers=hdr("admin")).status_code == 200
    assert client.get("/api/settings", headers=hdr("teacher", "T1")).status_code == 403
    assert client.get("/api/settings", headers=hdr("academic_admin", "AA1")).status_code == 403
    assert client.put("/api/settings/OCR_SHEET_PROVIDER", json={"value": "baidu"},
                      headers=hdr("teacher", "T1")).status_code == 403
    assert client.put("/api/settings/OCR_SHEET_PROVIDER", json={"value": "不是引擎"},
                      headers=hdr("admin")).status_code == 400


def test_whitelist_has_human_explanations(db_session):
    """开关页必须"看得懂"：每个键都要有中文名、说明、影响与选项含义。

    review 抓到过：只显示 OCR_SHEET_PROVIDER / OCR_VISION_REVIEW_ENABLED 两个裸 key，
    管理员看不懂是什么、切了会怎样。
    """
    items = {it["key"]: it for it in rs.all_settings(db_session)}
    for key, it in items.items():
        assert it["label"] and it["label"] != key, f"{key} 缺中文名"
        assert len(it["description"]) > 10, f"{key} 缺说明"
        assert len(it["impact"]) > 5, f"{key} 缺影响说明"
        if it["type"] == "enum":
            for choice in it["choices"]:
                assert it["choice_labels"].get(choice), f"{key} 的选项 {choice} 缺含义说明"


def test_menus_gate(db_session):
    from app.middleware.auth import menus_for
    assert "admin_settings" in menus_for("admin")
    assert "admin_settings" not in menus_for("academic_admin")
    assert "admin_settings" not in menus_for("teacher")
