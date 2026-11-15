"""C9（§11）：讲评稿首调红卡——`模型没有返回可用的 HTML 内容`。

真机实测（2026-10-20）：同一技能第二轮就成功，说明空产出是一次性失败而不是能力
不足。修法两层：① 工具内部自己重试一次，别把红卡甩给老师；② 抽取放宽到「有头无尾」
（被 max_tokens 截断），并且报错要带形状描述——上一版只说「换个说法重试」，等于让
老师替我们猜。
"""
import json

import pytest

from agent.tools import teaching as T

DOC = "<!DOCTYPE html>\n<html lang=\"zh-CN\"><body><h1>讲评稿</h1></body></html>"


def test_extract_strips_fence_with_reasoning_preamble():
    """真实形态：先一句「以下是您需要的HTML文档」再包 ```html 围栏（实测 29k 字）。"""
    raw = "以下是您需要的HTML文档，可直接投屏使用。\n```html\n" + DOC + "\n```"
    assert T._extract_html(raw) == DOC


def test_extract_accepts_html_tag_without_doctype():
    raw = "前言\n<html><body>ok</body></html>\n后记"
    assert T._extract_html(raw) == "<html><body>ok</body></html>"


def test_extract_keeps_truncated_document():
    """有头无尾 = 被 max_tokens 截断：留半份让面板渲染，别整份丢掉。"""
    raw = "<!DOCTYPE html>\n<html><body>" + "x" * 3000
    out = T._extract_html(raw)
    assert out.startswith("<!DOCTYPE html>") and len(out) > 2000


def test_extract_rejects_short_fragment():
    assert T._extract_html("抱歉，这次数据不足。<html") == ""
    assert T._extract_html("") == ""


def test_miss_reason_is_evidence_not_advice():
    reason = T._html_miss_reason("抱歉，这次只能用文字说明。" * 3)
    assert "字" in reason and "开头是" in reason
    assert T._html_miss_reason("") == "模型这次返回了空内容"


class _FakeProvider:
    """按顺序吐出预设回复，并记下被调用几次。"""

    replies: list = []
    calls: int = 0

    def __init__(self, timeout=0.0):
        pass

    async def chat(self, messages=None, temperature=0.0, max_tokens=0):
        type(self).calls += 1
        text = type(self).replies.pop(0)

        class _R:
            pass

        r = _R()
        r.content = text
        return r

    async def close(self):
        pass


@pytest.fixture
def fake_skill(monkeypatch, db_session):
    import app.models.database as db_mod
    import agent.tools.context as tool_ctx
    from app.services import artifact_service

    monkeypatch.setattr(db_mod, "get_db", lambda: iter([db_session]))
    monkeypatch.setattr(tool_ctx, "current_identity",
                        lambda: {"user_id": "t1", "role": "teacher"})
    monkeypatch.setattr(artifact_service, "save_artifact",
                        lambda *a, **kw: {"artifact_id": "a1", "kind": "doc",
                                          "title": "讲评稿", "version_seq": 1,
                                          "created": True, "url": "/x"})
    monkeypatch.setattr("agent.provider.deepseek.DeepSeekProvider", _FakeProvider)
    _FakeProvider.calls = 0
    return _FakeProvider


@pytest.mark.asyncio
async def test_empty_first_call_is_retried_inside_the_tool(fake_skill):
    """空产出一次 → 工具自己重试 → 老师看到的是工件，不是红卡。"""
    fake_skill.replies = ["抱歉，这次只能用文字说明。", DOC]
    out = json.loads(await T.invoke_teaching_skill(
        skill="review_deck", topic="物质及其变化单元讲评", context_data="{}"))
    assert "error" not in out, out
    assert out["artifact"]["artifact_id"] == "a1"
    assert fake_skill.calls == 2


@pytest.mark.asyncio
async def test_two_empty_calls_report_shape_not_advice(fake_skill):
    fake_skill.replies = ["抱歉，数据不足。", "抱歉，还是不行。"]
    out = json.loads(await T.invoke_teaching_skill(
        skill="review_deck", topic="物质及其变化单元讲评", context_data="{}"))
    assert "error" in out
    assert "开头是" in out["error"] and "连续两次" in out["error"]
    assert fake_skill.calls == 2