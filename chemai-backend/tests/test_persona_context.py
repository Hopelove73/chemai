"""请求 persona 注入工具层（grilling 2026-09-22 · v1 退役的一环）。

删 v1 前必须先解决 tutoring.py 的隐性依赖：它在 persona 为空/auto/tutor 时读
`agent.langgraph_agent._current_persona`（v1 模块级全局）。v2 路径从不调用 v1
工厂 → 该全局恒为默认 "tutor" → **教师**请求里模型省略 persona 调 chemistry_tutor
会静默掉回**学生**引导提示（教研分析丢失），且被 `except: pass` 掩盖。

改法：v2 请求把 persona 落到 contextvar（与身份/学生同一处 context.py），
工具读 current_persona()。本文件在旧代码上是红的（读 v1 全局拿不到 teacher）。
"""
import asyncio

from agent.tools.context import set_current_persona


def _run(coro):
    async def go():
        return await coro
    return asyncio.run(go())


def _capture_tutor_prompt(monkeypatch):
    from agent.provider import deepseek as dsp

    captured = {}

    class FakeResult:
        content = "ok"
        model = "fake"

    async def fake_chat(self, messages, **kw):
        captured["system"] = messages[0]["content"]
        return FakeResult()

    async def fake_close(self):
        pass

    monkeypatch.setattr(dsp.DeepSeekProvider, "chat", fake_chat)
    monkeypatch.setattr(dsp.DeepSeekProvider, "close", fake_close)
    return captured


def test_teacher_request_uses_research_prompt(monkeypatch):
    """当前请求是教师 → chemistry_tutor（省略 persona）必须走教研提示。"""
    captured = _capture_tutor_prompt(monkeypatch)
    from agent.tools.tutoring import chemistry_tutor

    set_current_persona("teacher")
    try:
        _run(chemistry_tutor(question="氧化还原高考怎么考"))
    finally:
        set_current_persona("")
    assert "教研" in captured["system"], \
        f"教师请求却拿到学生提示：{captured['system'][:40]!r}"


def test_student_request_keeps_guided_prompt(monkeypatch):
    """当前请求是学生 → 仍走引导式助教提示（contextvar 不误伤学生侧）。"""
    captured = _capture_tutor_prompt(monkeypatch)
    from agent.tools.tutoring import chemistry_tutor

    set_current_persona("student")
    try:
        _run(chemistry_tutor(question="怎么配平"))
    finally:
        set_current_persona("")
    assert "AI助教" in captured["system"], captured["system"][:40]


def test_explicit_persona_arg_wins(monkeypatch):
    """模型显式传 persona 优先于请求 contextvar（不夺走模型的显式意图）。"""
    captured = _capture_tutor_prompt(monkeypatch)
    from agent.tools.tutoring import chemistry_tutor

    set_current_persona("student")
    try:
        _run(chemistry_tutor(question="考点分布", persona="teacher"))
    finally:
        set_current_persona("")
    assert "教研" in captured["system"]
