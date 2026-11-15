"""外发通道中国化（ADR-0029 批⑦ / 用户反馈「Telegram 在中国不可用」）。

钉三件事：① 三平台 webhook 载荷分形与送达判定（飞书 code/企微钉钉 errcode）；
② host 白名单识别（防学业数据 POST 到任意地址）；③ 绑定端点持久化 + 非法
地址 400 友好文案（不再静默失败）。"""
import json

import pytest
import requests

from app.services.notification_service import (
    send_webhook_text, webhook_channel_of,
)


# ── webhook 载荷分形（monkeypatch requests.post）────────────────────────

class _FakeResp:
    def __init__(self, status=200, body=None):
        self.status_code = status
        self._body = body or {}

    def json(self):
        return self._body


@pytest.fixture
def fake_post(monkeypatch):
    calls = []

    def _install(status=200, body=None):
        def post(url, json=None, timeout=None):
            calls.append({"url": url, "json": json})
            return _FakeResp(status, body)
        monkeypatch.setattr(requests, "post", post)
        return calls

    return _install


def test_feishu_payload_shape(fake_post):
    calls = fake_post(body={"code": 0})
    ok, err = send_webhook_text("feishu", "https://open.feishu.cn/hook/x", "测试")
    assert ok and err == ""
    assert calls[0]["json"] == {"msg_type": "text", "content": {"text": "测试"}}


def test_wecom_and_dingtalk_payload_shape(fake_post):
    calls = fake_post(body={"errcode": 0})
    assert send_webhook_text("wecom", "https://qyapi.weixin.qq.com/hook/x", "测试")[0]
    assert send_webhook_text("dingtalk", "https://oapi.dingtalk.com/hook/x", "测试")[0]
    assert calls[0]["json"] == {"msgtype": "text", "text": {"content": "测试"}}
    assert calls[1]["json"] == {"msgtype": "text", "text": {"content": "测试"}}


def test_platform_error_code_is_failure(fake_post):
    fake_post(body={"errcode": 93000, "errmsg": "invalid webhook"})
    ok, err = send_webhook_text("wecom", "https://qyapi.weixin.qq.com/hook/x", "测试")
    assert not ok and "93000" in err


def test_http_error_and_network_error(fake_post, monkeypatch):
    fake_post(status=502)
    ok, err = send_webhook_text("feishu", "https://open.feishu.cn/x", "t")
    assert not ok and "502" in err

    def boom(url, json=None, timeout=None):
        raise ConnectionError("refused")
    monkeypatch.setattr(requests, "post", boom)
    ok, err = send_webhook_text("feishu", "https://open.feishu.cn/x", "t")
    assert not ok and "失败" in err


# ── host 白名单 ─────────────────────────────────────────────────────────

def test_webhook_channel_whitelist():
    assert webhook_channel_of("https://open.feishu.cn/open-apis/bot/v2/hook/x") == "feishu"
    assert webhook_channel_of("https://qyapi.weixin.qq.com/cgi-bin/webhook/send") == "wecom"
    assert webhook_channel_of("https://oapi.dingtalk.com/robot/send") == "dingtalk"
    # 非白名单 host 一律 None——学业数据不外送到任意地址
    assert webhook_channel_of("https://evil.example.com/hook") is None
    assert webhook_channel_of("not a url") is None
    assert webhook_channel_of("") is None


# ── 绑定端点（HTTP 层）─────────────────────────────────────────────────

@pytest.fixture
def parent_row(db_session):
    from app.models.database import Parent
    db_session.add(Parent(parent_id="p-1", name="周建国", password_hash="x"))
    db_session.commit()
    return "p-1"


def test_bind_endpoint_persists_and_validates(db_session, patch_get_db,
                                              sample_student, parent_row):
    import asyncio
    from agent.tools.context import set_current_identity
    from app.api.parent import (
        NotifyChannelRequest, get_notify_channel, set_notify_channel,
    )

    set_current_identity("p-1", "parent")
    try:
        # 非法地址 → 400
        with pytest.raises(Exception) as ei:
            asyncio.run(set_notify_channel(
                NotifyChannelRequest(webhook="https://evil.example.com/hook"),
                db=db_session, parent_id="p-1"))
        assert "飞书" in str(ei.value.detail if hasattr(ei.value, "detail") else ei.value)

        # 合法飞书地址 → 持久化
        asyncio.run(set_notify_channel(
            NotifyChannelRequest(webhook="https://open.feishu.cn/open-apis/bot/v2/hook/abc"),
            db=db_session, parent_id="p-1"))
        got = asyncio.run(get_notify_channel(db=db_session, parent_id="p-1"))
        assert got["channel"] == "feishu"
        assert got["webhook_masked"].startswith("https://open.feishu.cn")
    finally:
        set_current_identity(None)
