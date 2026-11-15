"""D+ 切片 2：`_GatePausedModel` 的中间件形态 —— `GateTurnEndMiddleware`。

原实现把短路塞在**模型包装层**（RunnableSerializable 包住 ChatModel，bind_tools
还得原样透传）。`create_agent` 的正规扩展点是 AgentMiddleware：`wrap_model_call`
在模型调用边界拦截，门开着时不打模型、直接回一条**无 tool_calls** 的空回执，
ReAct 循环没有下一步可走，本轮收尾。语义与原实现一比一（guard.py `_GatePausedModel`
的测试钉迁移到本文件，原类随后删除）。
"""
import json

from langchain_core.messages import AIMessage, HumanMessage

from agent.guard import GuardState, GateTurnEndMiddleware


def _mw(paused: bool):
    gs = GuardState()
    gs.paused = paused
    return GateTurnEndMiddleware(guard_state=gs), gs


def test_paused_short_circuits_without_calling_model():
    """门开着 → 不打模型，回执无 tool_calls、无正文。"""
    mw, _ = _mw(paused=True)
    called = {"n": 0}

    def handler(request):
        called["n"] += 1
        return AIMessage(content="模型不该被叫到")

    out = mw.wrap_model_call(None, handler)
    assert called["n"] == 0, "门开着不许打模型"
    assert isinstance(out, AIMessage)
    assert not getattr(out, "tool_calls", None), "无 tool_calls 才能收尾本轮"
    assert out.content == "", "回执不带正文（要说的话都在确认卡上）"
    assert out.additional_kwargs.get("_gate_paused") is True


def test_not_paused_delegates_to_model():
    mw, gs = _mw(paused=False)
    gs.begin_turn()  # 常态：每轮输入先复位

    def handler(request):
        return AIMessage(content="正常回答")

    out = mw.wrap_model_call(None, handler)
    assert out.content == "正常回答"


def test_begin_turn_clears_the_pause_and_model_runs_again():
    """老师决定之后继续执行时模型照常工作 —— 与原 _GatePausedModel 同一条钉。"""
    mw, gs = _mw(paused=True)
    out1 = mw.wrap_model_call(None, lambda r: AIMessage(content="x"))
    assert out1.additional_kwargs.get("_gate_paused") is True
    gs.begin_turn()
    out2 = mw.wrap_model_call(None, lambda r: AIMessage(content="y"))
    assert out2.content == "y"


def test_middleware_end_to_end_with_create_agent():
    """骨架级钉：create_agent + 本中间件，门开着的那一轮图自然收尾、不再打模型。"""
    import asyncio

    from langchain.agents import create_agent
    langgraph_check = None
    from langchain_core.callbacks import CallbackManagerForLLMRun
    from langchain_core.language_models import BaseChatModel
    from langchain_core.outputs import ChatGeneration, ChatResult
    from langchain_core.tools import tool
    from langgraph.checkpoint.memory import MemorySaver

    calls = {"model": 0}

    class CountingModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "counting"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None,
                      run_manager=None, **kwargs):
            calls["model"] += 1
            return ChatResult(generations=[ChatGeneration(
                message=AIMessage(content="答完了"))])

    @tool
    def harmless(student_id: str) -> str:
        """无害工具"""
        return f"ok {student_id}"

    gs = GuardState()
    gs.paused = True  # 门开着（模拟草稿门开出后）

    async def run():
        agent = create_agent(
            model=CountingModel(),
            tools=[harmless],
            system_prompt="你是助教",
            checkpointer=MemorySaver(),
            middleware=[GateTurnEndMiddleware(guard_state=gs)],
        )
        events = []
        async for ev in agent.astream_events(
                {"messages": [("user", "继续布置")]},
                {"configurable": {"thread_id": "mw-1"}}, version="v2"):
            events.append(ev["event"])
        return events

    events = asyncio.run(run())
    assert calls["model"] == 0, "门开着的那一轮不该打模型"
    assert "on_tool_start" not in events, "无工具执行"
    assert "on_chain_end" in events, "图要正常收尾"

    # 门关了（begin_turn）之后，下一轮模型照常
    gs.begin_turn()
    events2 = asyncio.run(run())
    assert calls["model"] == 1, "门关后模型恢复工作"


def test_gate_open_sets_pause_for_middleware_path():
    """门开出 → gs.paused 置位（中间件读的同一个标志，GuardState 侧契约不变）。"""
    gs = GuardState()
    gs.conversation_id = "c-mw"
    assert gs.paused is False
    gs.paused = True  # guard._waiting_out / acquire_gate 门开出路径置位
    mw = GateTurnEndMiddleware(guard_state=gs)
    out = mw.wrap_model_call(None, lambda r: AIMessage(content="x"))
    assert out.additional_kwargs.get("_gate_paused") is True
