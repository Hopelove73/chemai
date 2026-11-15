"""确定性审批重放（2026-08）：批准后 request_approval 自动重跑被拦截的调用，
不再依赖 LLM 自觉重试——实测批准后模型可能直接收尾，链路断掉。"""
import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.guard import GuardState
from app.models.database import Base
from agent.langgraph_sse_v2 import LangGraphSSEAdapterV2


def _run(coro):
    async def go():
        return await coro

    return asyncio.run(go())


# 这里原有两条用例，**2026-10-17 随 gate-lifecycle 2.4 收尾删掉**（不是丢了覆盖）：
#
# ① `test_guard_stores_pending_call_on_approval_needed` —— 断言护栏拦下
#    `approval_needed` 之后把调用**存进内存** `guard_state.pending_approval`。
#    那个字段就是 2.4 删掉的东西（载荷真源改成门实体，ADR-0030）。
#    现覆盖：`tests/test_phase2_gate_unification.py` 里「拦下即登记门实体」那组
#    （真工具名 + conversation_id，断言 `agent_gates` 那行）。
#
# ② `test_request_approval_replays_pending_call` —— 测的是 `request_approval` 工具
#    （内部 LangGraph `interrupt()`）批准后重放内存里那份 pending。那个工具在
#    **ADR-0015 阶段 2 就退役了**（确认门改由数据层强制，模型侧没有独立确认工具可调），
#    2.4 把它的工厂和它读的字段一起删了。
#    现覆盖：`tests/test_gate_decision_loop.py` 的「批准两次不执行两遍」
#    （真实题库 + `_direct_replay_events`）与 `tests/test_gate_blocks_after_restart.py`。


def test_adapter_emits_replayed_tool_result():
    adapter = LangGraphSSEAdapterV2()
    payload = json.dumps({
        "__replayed_tool": "read",
        "result": {"content": "文件内容", "path": "x.txt"},
    }, ensure_ascii=False)
    events = adapter.feed({
        "event": "on_tool_end",
        "name": "request_approval",
        "data": {"output": f"content='{payload}' name='request_approval' tool_call_id='call_1'"},
    })
    assert len(events) == 1
    evt = json.loads(events[0])
    assert evt["type"] == "tool_result"
    assert evt["name"] == "read"
    assert evt["success"] is True
    assert json.loads(evt["result"])["content"] == "文件内容"


def test_adapter_replayed_error_marks_failure():
    adapter = LangGraphSSEAdapterV2()
    payload = json.dumps({
        "__replayed_tool": "read",
        "result": {"error": "文件不存在"},
    }, ensure_ascii=False)
    events = adapter.feed({
        "event": "on_tool_end",
        "name": "request_approval",
        "data": {"output": f"content='{payload}' name='request_approval' tool_call_id='call_2'"},
    })
    evt = json.loads(events[0])
    assert evt["success"] is False


@pytest.fixture
def gate_env(monkeypatch):
    """内存库；把 `get_db` 指过来（`_entity_db` 与 `work_unit._session` 都是延迟
    绑定它的，所以两处一起就通了）。返回 session。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    import app.models.database as db_mod
    monkeypatch.setattr(db_mod, "get_db", lambda: iter([session]))
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _open_delete_gate(db, cid: str, name: str) -> str:
    """开一扇可重放的布尔门（真工具 `delete_bank` + 一个真题库），返回 set_id。"""
    from app.services.gate_service import gate_service
    from app.services.question_bank import question_bank_service
    from app.services.work_unit import new_work_unit

    sid = question_bank_service.create_set(db, name=name, teacher_id="t1")["set_id"]
    with new_work_unit() as wdb:
        gate_service.open_gate(wdb, cid, kind="boolean",
                               tool_name="delete_bank", kwargs={"set_id": sid})
    return sid


def test_direct_replay_events_returns_tool_result(gate_env):
    """resume 端点无中断分支：批准后**从门实体**重放待批调用。

    2026-10-17（gate-lifecycle 2.4 收尾）改写了驱动方式：原先往内存
    `guard_state.pending_approval` 里塞一个假工具，那条路随字段一起删了。
    现在照生产的形状来 —— 有会话、有门那一行、工具是注册表里的真工具。
    """
    import asyncio as _asyncio
    from agent.channel.langgraph_channel import _direct_replay_events
    from app.services.question_bank import question_bank_service

    cid = "t-replay"
    sid = _open_delete_gate(gate_env, cid, "待删题库-replay")

    gs = GuardState()
    gs.conversation_id = cid

    events, replay_info = _asyncio.run(_direct_replay_events(gs, True, cid))

    assert len(events) == 1
    evt = json.loads(events[0])
    assert evt["type"] == "tool_result" and evt["name"] == "delete_bank"
    assert replay_info is not None and replay_info["name"] == "delete_bank"
    # 真的执行了：题库没了（比"假工具被调了几次"更硬的证据）
    assert question_bank_service.get_set(gate_env, sid) is None


def test_direct_replay_cancel_does_not_replay(gate_env):
    """取消：不重放、门保持开着（老师还能再决定）、`approved` 不被置位。

    这里原来断言的是"清空内存里的待批登记"—— 那个字段没了（2.4 收尾）。
    现在钉的是**行为**：什么都没执行，门还在，老师可以重新决定。
    """
    import asyncio as _asyncio
    from agent.channel.langgraph_channel import _direct_replay_events
    from app.services.gate_service import gate_service
    from app.services.question_bank import question_bank_service

    cid = "t-cancel"
    sid = _open_delete_gate(gate_env, cid, "待删题库-cancel")

    gs = GuardState()
    gs.conversation_id = cid

    events, replay_info = _asyncio.run(_direct_replay_events(gs, False, cid))

    assert events == [] and replay_info is None
    assert gs.approved is False
    assert question_bank_service.get_set(gate_env, sid) is not None, "取消不该执行动作"
    assert gate_service.get_open_gate(gate_env, cid) is not None, "取消不该把门也收了"


# ═══════════════════════════════════════════════════════════════════
# 2026-08 修复「用户发送端显示巨文」：C 层（结果替换进 ToolMessage）+
# A 层（续接后摘除注入消息）
# ═══════════════════════════════════════════════════════════════════

class _FakeState:
    def __init__(self, msgs):
        self.values = {"messages": msgs}


class _FakeAgent:
    """Minimal agent double: aget_state / aupdate_state / astream_events."""

    def __init__(self, msgs):
        self._msgs = msgs
        self.updates = []
        self.stream_inputs = []

    async def aget_state(self, config):
        return _FakeState(self._msgs)

    async def aupdate_state(self, config, values):
        self.updates.append(values)
        # 模拟 LangGraph append 语义：RemoveMessage 删、其余追加
        from langchain_core.messages import RemoveMessage
        for m in values.get("messages", []):
            if isinstance(m, RemoveMessage):
                self._msgs = [x for x in self._msgs if getattr(x, "id", None) != m.id]
            else:
                self._msgs.append(m)

    async def astream_events(self, state_input, config, version="v2"):
        self.stream_inputs.append(state_input)
        # 模拟 LangGraph：输入的 user 消息会追加进 checkpoint
        from langchain_core.messages import HumanMessage
        for b in state_input.get("messages", []):
            if b.get("role") == "user":
                self._msgs.append(HumanMessage(content=b.get("content", ""), id="injected_1"))
        yield {"event": "on_chat_model_stream", "name": "", "data": {"chunk": None}}


def test_replace_pending_tool_result_swaps_content_keeping_call_id():
    """C 层：批准重放后，checkpoint 里 approval_needed 的 ToolMessage 被替换为
    真实结果（保留 tool_call_id，LangGraph 配对校验不受影响）。"""
    import asyncio as _asyncio
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from agent.channel.langgraph_channel import _replace_pending_tool_result

    msgs = [
        HumanMessage(content="读取桌面文件", id="u1"),
        AIMessage(content="我来读取。", tool_calls=[{"name": "read", "args": {"path": "/x"}, "id": "call_1"}], id="a1"),
        ToolMessage(content='{"approval_needed": true}', name="read", tool_call_id="call_1", id="tm_old"),
    ]
    agent = _FakeAgent(msgs)

    async def go():
        return await _replace_pending_tool_result(
            agent, {"configurable": {"thread_id": "t-1"}}, "read",
            '{"content": "文件真实内容", "path": "x.txt"}')

    assert _asyncio.run(go()) is True
    assert len(agent.updates) == 1
    ops = agent.updates[0]["messages"]
    from langchain_core.messages import RemoveMessage, ToolMessage as TM
    assert isinstance(ops[0], RemoveMessage)
    assert ops[0].id == "tm_old"  # 摘掉旧 approval_needed 结果
    assert isinstance(ops[1], TM)
    assert ops[1].tool_call_id == "call_1"  # 配对 id 不变
    assert ops[1].name == "read"
    assert json.loads(ops[1].content)["content"] == "文件真实内容"


def test_replace_pending_tool_result_missing_tool_returns_false():
    """没有对应工具消息时返回 False，调用方回退旧逻辑（不丢结果）。"""
    import asyncio as _asyncio
    from langchain_core.messages import HumanMessage
    from agent.channel.langgraph_channel import _replace_pending_tool_result

    agent = _FakeAgent([HumanMessage(content="你好", id="u1")])

    async def go():
        return await _replace_pending_tool_result(
            agent, {"configurable": {"thread_id": "t-1"}}, "read", "{}")

    assert _asyncio.run(go()) is False
    assert agent.updates == []


def test_remove_injected_replay_message_removes_note():
    """A 层：续接完成后摘除注入的续接提示，历史不残留内部消息。"""
    import asyncio as _asyncio
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from agent.channel.langgraph_channel import _remove_injected_replay_message

    note = ("（系统提示：你刚才请求老师批准的操作已获得批准，并已自动重放完成，"
            "完整结果见上文的工具结果（工具「read」）。请基于该结果继续完成老师的请求。）")
    msgs = [
        HumanMessage(content="读取桌面文件", id="u1"),
        AIMessage(content="我来读取。", tool_calls=[{"name": "read", "args": {}, "id": "call_1"}], id="a1"),
        ToolMessage(content='{"content": "文件真实内容"}', name="read", tool_call_id="call_1", id="tm_1"),
        HumanMessage(content=note, id="note_1"),
        AIMessage(content="已读取完成。", id="a2"),
    ]
    agent = _FakeAgent(msgs)

    async def go():
        return await _remove_injected_replay_message(
            agent, {"configurable": {"thread_id": "t-1"}})

    _asyncio.run(go())
    assert len(agent.updates) == 1
    ops = agent.updates[0]["messages"]
    from langchain_core.messages import RemoveMessage
    assert isinstance(ops[0], RemoveMessage)
    assert ops[0].id == "note_1"  # 只摘注入提示，不碰用户消息


def test_remove_injected_replay_message_keeps_user_messages():
    """没有注入句式时不动 checkpoint（用户消息永不被误删）。"""
    import asyncio as _asyncio
    from langchain_core.messages import HumanMessage
    from agent.channel.langgraph_channel import _remove_injected_replay_message

    msgs = [
        HumanMessage(content="正常提问", id="u1"),
        HumanMessage(content="（系统提示：这行字是用户自己打的，句式不匹配）", id="u2"),
    ]
    agent = _FakeAgent(msgs)

    async def go():
        return await _remove_injected_replay_message(
            agent, {"configurable": {"thread_id": "t-1"}})

    _asyncio.run(go())
    assert agent.updates == []  # 无匹配 → 不动 checkpoint


def test_stream_replay_continuation_slim_message_when_replaced():
    """C 层成功替换时：续接提示不再携带完整重放结果（巨文根因），
    续接完成后摘除注入消息（A 层）。"""
    import asyncio as _asyncio
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from agent.channel.langgraph_channel import _stream_replay_continuation

    msgs = [
        HumanMessage(content="读取桌面文件", id="u1"),
        AIMessage(content="我来读取。", tool_calls=[{"name": "read", "args": {}, "id": "call_1"}], id="a1"),
        ToolMessage(content='{"approval_needed": true}', name="read", tool_call_id="call_1", id="tm_old"),
    ]
    agent = _FakeAgent(msgs)
    adapter = LangGraphSSEAdapterV2()
    replay_info = {
        "name": "read",
        "raw": '{"content": "整份文件内容…" * 1000}',
        "result": '{"content": "截断版"}',
    }

    async def go():
        out = []
        async for ev in _stream_replay_continuation(
                agent, adapter, {"configurable": {"thread_id": "t-1"}}, replay_info):
            out.append(ev)
        return out

    out = _asyncio.run(go())
    assert out == []  # chunk=None → adapter 无输出，流正常结束
    assert len(agent.stream_inputs) == 1
    injected = agent.stream_inputs[0]["messages"][0]["content"]
    # 瘦身版：无完整结果
    assert "整份文件内容" not in injected
    assert "完整结果见上文的工具结果（工具「read」）" in injected
    # C 层：旧 approval_needed ToolMessage 已被替换
    assert any(isinstance(m, ToolMessage) and m.tool_call_id == "call_1"
               and "文件真实" not in str(getattr(m, "content", "")) for m in agent._msgs)
    # A 层：续接后摘除注入消息
    removed_ids = [m.id for u in agent.updates
                   for m in u.get("messages", []) if getattr(m, "id", None) == "injected_1"]
    assert "injected_1" in removed_ids


def test_stream_replay_continuation_fallback_keeps_result():
    """替换失败时回退旧行为：结果仍随消息注入（不丢结果）。"""
    import asyncio as _asyncio
    from langchain_core.messages import HumanMessage
    from agent.channel.langgraph_channel import _stream_replay_continuation

    agent = _FakeAgent([HumanMessage(content="你好", id="u1")])
    adapter = LangGraphSSEAdapterV2()
    replay_info = {"name": "read", "raw": '{"content": "完整结果ABC"}', "result": '{"content": "截断版"}'}

    async def go():
        out = []
        async for ev in _stream_replay_continuation(
                agent, adapter, {"configurable": {"thread_id": "t-1"}}, replay_info):
            out.append(ev)
        return out

    _asyncio.run(go())
    injected = agent.stream_inputs[0]["messages"][0]["content"]
    assert "重放结果如下" in injected
    assert "截断版" in injected  # 回退塞的是 SSE 截断结果
