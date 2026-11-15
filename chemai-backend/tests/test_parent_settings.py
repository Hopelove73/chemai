"""家长设置页扩充（ADR-0029 批⑦）：通知偏好投递门 + 偏好端点 + 改密端点。

三块：① parent_delivery_gate 纯函数（类型开关/免打扰窗口含跨午夜/warning
直通）；② notify-prefs GET/PUT（默认兜底/校验/持久化）；③ change-password
（验旧/最短长度/落 Account 同一凭据面）。"""
import json
from datetime import datetime

import pytest

from app.models.database import Account, Parent
from app.services.notification_service import (
    DEFAULT_DELIVERY_PREFS, parent_delivery_gate,
)


def _gate(prefs=None, etype="practice_completed", h=12, m=0):
    return parent_delivery_gate(prefs, etype, now=datetime(2026, 10, 7, h, m))


# ── 投递门纯函数 ────────────────────────────────────────────────────────

def test_default_prefs_all_allowed_off_hours():
    assert _gate(None, "practice_completed", h=12) == (True, "")


def test_type_mute_blocks_and_warning_bypasses():
    prefs = {"types": {"practice_completed": False}}
    assert _gate(prefs, "practice_completed", h=12)[0] is False
    # warning 不入可关清单——同偏好下照常送达
    assert _gate(prefs, "warning", h=12)[0] is True


def test_quiet_hours_window_and_midnight_wrap():
    qh = {"enabled": True, "start": "22:00", "end": "07:00"}
    assert _gate({"quiet_hours": qh}, "practice_completed", h=23)[1] == "quiet_hours"
    assert _gate({"quiet_hours": qh}, "practice_completed", h=6)[1] == "quiet_hours"
    assert _gate({"quiet_hours": qh}, "practice_completed", h=12)[0] is True
    # 不跨午夜窗口（13:00-15:00）
    qh2 = {"enabled": True, "start": "13:00", "end": "15:00"}
    assert _gate({"quiet_hours": qh2}, "practice_completed", h=14)[1] == "quiet_hours"
    assert _gate({"quiet_hours": qh2}, "practice_completed", h=16)[0] is True


def test_quiet_hours_disabled_and_warning_always():
    qh = {"enabled": False, "start": "22:00", "end": "07:00"}
    assert _gate({"quiet_hours": qh}, "practice_completed", h=23)[0] is True
    assert _gate({"quiet_hours": qh}, "warning", h=23) == (True, "always")


def test_partial_stored_prefs_merge_with_defaults():
    stored = {"types": {"streak_milestone": False}}  # 只存一项，其余走默认
    assert _gate(stored, "streak_milestone", h=12)[0] is False
    assert _gate(stored, "artifact_shared", h=12)[0] is True


# ── notify-prefs 端点 ───────────────────────────────────────────────────

@pytest.fixture
def parent_row(db_session):
    db_session.add(Parent(parent_id="p-1", name="周建国", password_hash="x"))
    db_session.commit()
    return "p-1"


def test_get_prefs_fills_defaults(db_session, patch_get_db, parent_row):
    import asyncio
    from agent.tools.context import set_current_identity
    from app.api.parent import get_notify_prefs
    set_current_identity("p-1", "parent")
    try:
        out = asyncio.run(get_notify_prefs(db=db_session, parent_id="p-1"))
    finally:
        set_current_identity(None)
    assert out["prefs"]["types"]["practice_completed"] is True
    assert out["prefs"]["quiet_hours"] == {"enabled": True, "start": "22:00", "end": "07:00"}


def test_put_prefs_validates_and_persists(db_session, patch_get_db, parent_row):
    import asyncio
    from agent.tools.context import set_current_identity
    from app.api.parent import NotifyPrefsRequest, get_notify_prefs, set_notify_prefs
    set_current_identity("p-1", "parent")
    try:
        # 非法：未知类型 / 非布尔 / 坏时段
        for bad in (NotifyPrefsRequest(types={"nope": True}),
                    NotifyPrefsRequest(types={"practice_completed": "yes"}),
                    NotifyPrefsRequest(quiet_hours={"start": "25:00"})):
            with pytest.raises(Exception):
                asyncio.run(set_notify_prefs(bad, db=db_session, parent_id="p-1"))
        # 合法 → 持久化
        asyncio.run(set_notify_prefs(
            NotifyPrefsRequest(types={"practice_completed": False},
                               quiet_hours={"enabled": False, "start": "21:00", "end": "08:00"}),
            db=db_session, parent_id="p-1"))
        out = asyncio.run(get_notify_prefs(db=db_session, parent_id="p-1"))
        assert out["prefs"]["types"]["practice_completed"] is False
        assert out["prefs"]["quiet_hours"] == {"enabled": False, "start": "21:00", "end": "08:00"}
    finally:
        set_current_identity(None)


# ── change-password 端点 ────────────────────────────────────────────────

@pytest.fixture
def parent_account(db_session, parent_row):
    from app.api.auth import hash_password
    acc = Account(account_id="acc-p1", username="241901家长",
                  password_hash=hash_password("old123"),
                  role="parent", parent_id="p-1", status="active")
    db_session.add(acc)
    db_session.commit()
    return acc


def _change(db_session, old, new):
    import asyncio
    from agent.tools.context import set_current_identity
    from app.api.parent import ChangePasswordRequest, change_password
    set_current_identity("p-1", "parent")
    try:
        return asyncio.run(change_password(
            ChangePasswordRequest(old_password=old, new_password=new),
            db=db_session, parent_id="p-1"))
    finally:
        set_current_identity(None)


def test_change_password_wrong_old(db_session, patch_get_db, parent_row, parent_account):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        _change(db_session, "wrong", "newpass1")
    assert "原密码不正确" in ei.value.detail


def test_change_password_too_short(db_session, patch_get_db, parent_row, parent_account):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        _change(db_session, "old123", "abc")
    assert "至少 6 位" in ei.value.detail


def test_change_password_updates_account_credential(db_session, patch_get_db,
                                                    parent_row, parent_account):
    from app.api.auth import hash_password
    _change(db_session, "old123", "newpass1")
    db_session.expire(parent_account)
    assert parent_account.password_hash == hash_password("newpass1")


# ── dispatch 接线：投递门拦截外发（站内照常落库）────────────────────────

def test_dispatch_respects_delivery_gate(monkeypatch, db_session, patch_get_db,
                                         parent_row, sample_student):
    from app.core.config import settings
    from app.services import notification_service as ns

    prow = db_session.get(Parent, "p-1")  # parent_row 夹具已建
    prow.notify_channel = "feishu"
    prow.notify_webhook = "https://open.feishu.cn/open-apis/bot/v2/hook/abc"
    prow.notify_prefs = {"types": {"practice_completed": False}}
    db_session.commit()
    monkeypatch.setattr(settings, "NOTIFY_EXTERNAL_GATEWAYS", True, raising=False)
    sent = []
    monkeypatch.setattr(ns, "_gateway_send", lambda ev, rec: sent.append(rec) or True)

    # 被静音的类型：投递门移除路由键（_gateway_send 收不到通道 → 降级站内）
    ns.notification_service.dispatch(
        recipient={"role": "parent", "id": "p-1", "student_id": sample_student.student_id},
        event={"type": "practice_completed", "title": "t"}, db=db_session)
    assert len(sent) == 1
    assert "notify_channel" not in sent[0],         "被静音事件的投递路由必须被移除（无通道 = 降级仅站内）"
    # warning 直通：路由保留
    ns.notification_service.dispatch(
        recipient={"role": "parent", "id": "p-1", "student_id": sample_student.student_id},
        event={"type": "warning", "title": "预警"}, db=db_session)
    assert len(sent) == 2 and sent[1].get("notify_channel") == "feishu"
