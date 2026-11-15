"""KB-T14 chemistry_tutor 挂钩（seam）：kb_context 资料拼进 user 消息 + 回合日志。

不调真 LLM：DeepSeekProvider 打桩，抓 messages 断言资料段位置与降级形态。
"""
import json

import pytest

from agent.tools import tutoring


class FakeProvider:
    def __init__(self, *a, **kw):
        self.calls = []

    async def chat(self, messages, **kw):
        self.calls.append(messages)
        r = type("R", (), {"content": "模拟回答", "model": "fake"})()
        return r

    async def close(self):
        pass


@pytest.fixture()
def fake_provider(monkeypatch):
    import agent.provider.deepseek as ds_mod
    fp = FakeProvider()
    monkeypatch.setattr(ds_mod, "DeepSeekProvider", lambda *a, **kw: fp)
    return fp


@pytest.fixture()
def kb_env(monkeypatch):
    """kb_context 打桩：可控的资料/miss/降级三态。"""
    state = {"text": "", "mode": "degraded", "hits": [], "degraded": True,
             "ms": 0, "enhancement": {}}
    calls = []

    def fake_build(question, **kw):
        calls.append({"question": question, **kw})
        return dict(state)

    monkeypatch.setattr(tutoring, "build_kb_context", fake_build)
    monkeypatch.setattr(tutoring, "_kb_enabled", lambda: True)
    return {"state": state, "calls": calls}


@pytest.mark.asyncio
async def test_kb_text_is_injected_into_user_message(fake_provider, kb_env):
    kb_env["state"].update({"text": "【参考资料】…氧化还原…", "mode": "ok",
                            "hits": [{"source_path": "x.md"}], "degraded": False})
    await tutoring.chemistry_tutor(question="什么是氧化还原反应", persona="student")
    msgs = fake_provider.calls[0]
    user = msgs[-1]["content"]
    assert "什么是氧化还原反应" in user, "原问题保留"
    assert "【参考资料】" in user, "kb 资料拼进 user 消息"
    assert user.index("【参考资料】") < user.index("什么是氧化还原反应"), "资料在前、问题在后"


@pytest.mark.asyncio
async def test_miss_and_degraded_inject_nothing(fake_provider, kb_env):
    for mode in ("miss", "degraded"):
        kb_env["state"].update({"text": "【知识库未覆盖】", "mode": mode,
                                "degraded": mode == "degraded"})
        await tutoring.chemistry_tutor(question="q", persona="student")
        user = fake_provider.calls[-1][-1]["content"]
        assert "【参考资料】" not in user, f"{mode} 不得拼资料段"


@pytest.mark.asyncio
async def test_miss_prompt_mentions_kb_boundary(fake_provider, kb_env):
    """miss 时给模型一句边界纪律：语料未覆盖就明说，不硬编。"""
    kb_env["state"].update({"text": "【参考资料】…暂未覆盖…", "mode": "miss", "degraded": False})
    await tutoring.chemistry_tutor(question="q", persona="student")
    user = fake_provider.calls[-1][-1]["content"]
    assert "暂未覆盖" in user, "miss 的边界提示要送达模型"


@pytest.mark.asyncio
async def test_weak_hit_injects_reference_with_coverage_note(fake_provider, kb_env):
    """KB4a：弱命中与 ok 同路注入——资料照常给，text 头部覆盖提示送达模型。"""
    kb_env["state"].update({"text": "【覆盖提示】覆盖较浅…\n【参考资料】…", "mode": "weak",
                            "degraded": False})
    await tutoring.chemistry_tutor(question="q", persona="student")
    user = fake_provider.calls[-1][-1]["content"]
    assert "【参考资料】" in user, "弱命中不得按拒答处理"
    assert "【覆盖提示】" in user, "覆盖提示送达模型"
    assert "暂未覆盖" not in user, "不能误用 miss 的边界措辞"


@pytest.mark.asyncio
async def test_kb_disabled_skips_retrieval(fake_provider, kb_env, monkeypatch):
    monkeypatch.setattr(tutoring, "_kb_enabled", lambda: False)
    await tutoring.chemistry_tutor(question="q", persona="student")
    assert kb_env["calls"] == [], "开关关闭不检索"


@pytest.mark.asyncio
async def test_hit_logged_to_audit(fake_provider, kb_env, monkeypatch):
    logged = {}
    monkeypatch.setattr(tutoring, "_log_kb_audit", lambda cid, q, out: logged.update(
        {"cid": cid, "q": q, "mode": out["mode"], "hits": out["hits"]}))
    kb_env["state"].update({"text": "【参考资料】x", "mode": "ok",
                            "hits": [{"source_path": "氧化还原反应.md"}], "degraded": False})
    await tutoring.chemistry_tutor(question="Fe 转化", persona="teacher")
    assert logged["mode"] == "ok" and logged["hits"], "命中写审计日志"
