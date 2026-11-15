"""D+ 切片 3：布尔确认门走 HumanInTheLoopMiddleware（原工具内 interrupt 试点的接棒）。

钉四件事：
  1. 被 HITL 工具集命中时**图在模型→工具边界挂起**（per_call，载荷 type=tool_approval），
     guard 侧不做门拦截（无 approval_needed 卡、无双门）；
  2. **批准之前工具一行都不执行**；批准后 `Command(resume={interrupt_id: approve})`
     原地执行**同一次**调用（无需 channel 确定性重放）；
  3. 拒绝回填 reject（带反馈）：工具不执行，模型拿到拒绝回执继续收尾；
  4. channel 侧挂起检测（_suspended_gate_events）按 boolean_gate_id 登记门行 +
     发卡 —— 待确认队列与聊天卡同一个数据源（ADR-0030）。
"""
import asyncio
import json

from langchain.agents import create_agent
from langchain.agents.middleware import HumanInTheLoopMiddleware
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command

import pytest

from agent.guard import GateTurnEndMiddleware, GuardState, _make_guarded_tool
from app.models.database import AgentGate


class _Scripted(BaseChatModel):
    """最后一轮收尾，其余轮发工具调用。不依赖真实模型。"""

    @property
    def _llm_type(self):
        return "scripted"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        if messages and messages[-1].type == "human":
            msg = AIMessage(content="", tool_calls=[{
                "name": "assign_adaptive_practice",
                "args": {"student_id": "S1", "quantity": 5},
                "id": "call-1",
            }])
        else:
            msg = AIMessage(content="做完了")
        return ChatResult(generations=[ChatGeneration(message=msg)])


def _agent(gs, requires_approval, when=None):
    """HITL 形态：guard 不做门拦截（requires_approval=False）+ 中间件挂起。
    `when` 模拟 create_v2 的合并门谓词接线（生产代码里读 guard 冻结快照）。"""

    async def assign_adaptive_practice(student_id: str, quantity: int = 5) -> str:
        """给学生布置自适应练习。"""
        return json.dumps({"ok": True, "student_id": student_id,
                           "quantity": quantity}, ensure_ascii=False)

    tool = _make_guarded_tool(assign_adaptive_practice, gs, requires_approval)
    cfg = {"allowed_decisions": ["approve", "reject"]}
    if when is not None:
        cfg["when"] = when
    return create_agent(
        model=_Scripted(),
        tools=[tool],
        system_prompt="你是助教",
        checkpointer=InMemorySaver(),
        middleware=[
            GateTurnEndMiddleware(guard_state=gs),
            HumanInTheLoopMiddleware(
                interrupt_on={"assign_adaptive_practice": cfg},
                interrupt_mode="per_call"),
        ],
    )


def _last_tool_text(events):
    outs = [str(ev.get("data", {}).get("output"))
            for ev in events if ev.get("event") == "on_tool_end"]
    return outs[-1] if outs else None


def test_hitl_tool_suspends_before_execution(db_session, patch_get_db):
    gs = GuardState()
    gs.conversation_id = "t-hitl"
    agent = _agent(gs, requires_approval=False)
    cfg = {"configurable": {"thread_id": "t-hitl"}}

    events = []
    async def run():
        async for ev in agent.astream_events(
                {"messages": [HumanMessage(content="布置练习")]}, cfg, version="v2"):
            events.append(ev)
    asyncio.run(run())

    st = asyncio.run(agent.aget_state(cfg))
    assert st.next, "被拦下时图应当挂起（不是跑完）"
    ints = list(getattr(st, "interrupts", ()) or [])
    assert len(ints) == 1, f"应当恰好一个挂起点，实际 {len(ints)}"
    val = ints[0].value
    assert val.get("type") == "tool_approval", val
    assert val.get("name") == "assign_adaptive_practice", val
    assert val.get("args", {}).get("student_id") == "S1", val
    # 挂起前工具没执行（没有 on_tool_end），guard 也没发 approval_needed 卡
    assert not [e for e in events if e.get("event") == "on_tool_end"]
    # 门还没登记：登记是 channel 检测到挂起时做的（见下一条用例）
    assert db_session.query(AgentGate).count() == 0


def test_hitl_approve_executes_in_place(db_session, patch_get_db):
    gs = GuardState()
    gs.conversation_id = "t-hitl2"
    agent = _agent(gs, requires_approval=False)
    cfg = {"configurable": {"thread_id": "t-hitl2"}}

    async def first():
        async for _ in agent.astream_events(
                {"messages": [HumanMessage(content="布置练习")]}, cfg, version="v2"):
            pass
    asyncio.run(first())
    ints = list(asyncio.run(agent.aget_state(cfg)).interrupts or ())

    events2 = []
    async def approve():
        async for ev in agent.astream_events(
                Command(resume={ints[0].id: {"type": "approve"}}), cfg,
                version="v2"):
            events2.append(ev)
    asyncio.run(approve())

    outs = [e for e in events2 if e.get("event") == "on_tool_end"]
    assert len(outs) == 1, "批准后应当恰好执行一次"
    assert '"ok": true' in str(outs[0].get("data", {}).get("output")), outs
    assert not asyncio.run(agent.aget_state(cfg)).next, "恢复后图应当跑完"


def test_hitl_reject_skips_tool_and_feeds_model(db_session, patch_get_db):
    gs = GuardState()
    gs.conversation_id = "t-hitl3"
    agent = _agent(gs, requires_approval=False)
    cfg = {"configurable": {"thread_id": "t-hitl3"}}

    async def first():
        async for _ in agent.astream_events(
                {"messages": [HumanMessage(content="布置练习")]}, cfg, version="v2"):
            pass
    asyncio.run(first())
    ints = list(asyncio.run(agent.aget_state(cfg)).interrupts or ())

    events2 = []
    async def reject():
        async for ev in agent.astream_events(
                Command(resume={ints[0].id: {
                    "type": "reject", "message": "老师不同意"}}), cfg,
                version="v2"):
            events2.append(ev)
    asyncio.run(reject())

    assert not [e for e in events2 if e.get("event") == "on_tool_end"], \
        "拒绝时绝不能执行工具"
    # 拒绝回执以 ToolMessage 进状态（模型看得见、能继续收尾）
    st = asyncio.run(agent.aget_state(cfg))
    tail = [m for m in st.values.get("messages", [])[-3:]]
    tool_msgs = [m for m in tail if m.type == "tool"]
    assert tool_msgs and "老师不同意" in str(tool_msgs[-1].content), tail
    assert not st.next, "拒绝回填后图应当跑完（模型继续收尾）"


def test_suspended_gate_events_registers_row_with_computed_gid(
        db_session, patch_get_db, unit):
    """挂起 = 一次待确认：channel 按中间件载荷算 gid、登记门行、发卡。"""
    from agent.channel.langgraph_channel import _suspended_gate_events
    from app.services.gate_service import boolean_gate_id

    class _It:
        def __init__(self, i, v):
            self.id = i
            self.value = v

    args = {"student_id": "S1", "quantity": 5}
    ints = [_It("int-1", {"type": "tool_approval",
                          "tool_call_id": "call-1",
                          "name": "assign_adaptive_practice", "args": args})]
    events = _suspended_gate_events("t-susp2", ints)
    assert len(events) == 1, "一扇门一张卡"
    payload = json.loads(events[0])
    assert payload.get("type") == "gate_open"
    want_gid = boolean_gate_id("t-susp2", "assign_adaptive_practice", args)
    assert payload.get("gate_id") == want_gid, "gid 必须可由 (cid,tool,args) 复算"
    rows = db_session.query(AgentGate).all()
    assert len(rows) == 1, "挂起时门行必须登记，否则队列里看不到这张卡"
    assert rows[0].status == "open"
    assert rows[0].tool_name == "assign_adaptive_practice"


# ── 合并门承诺兑现（2026-10-26）：when 谓词读冻结快照 ────────────────

def _scope_when(gs):
    """与 create_v2 同形状的 when 谓词：冻结快照说覆盖 → 不弹卡。"""
    def _when(req):
        tc = getattr(req, "tool_call", None) or {}  # ToolCall 是 TypedDict
        name = tc.get("name", "") if isinstance(tc, dict) else ""
        args = tc.get("args") if isinstance(tc, dict) else None
        return not gs.hitl_scope_covers(name, dict(args or {}))
    return _when


def test_scope_covered_call_skips_interrupt_promise_honored(db_session, patch_get_db):
    """卡上写了「本次批准覆盖第 N 步」→ 那一步的工具调用不得再弹卡。"""
    gs = GuardState()
    gs.conversation_id = "t-hitl-scope"
    gs.hitl_scope_freeze = {"run_id": "r-1",
                            "by_object": {"student:S1": {"run_id": "r-1",
                                                         "object": "student:S1"}}}
    agent = _agent(gs, requires_approval=False, when=_scope_when(gs))
    cfg = {"configurable": {"thread_id": "t-hitl-scope"}}

    events = []
    async def run():
        async for ev in agent.astream_events(
                {"messages": [HumanMessage(content="布置练习")]}, cfg, version="v2"):
            events.append(ev)
    asyncio.run(run())

    st = asyncio.run(agent.aget_state(cfg))
    assert not st.next, "已覆盖的调用不该挂起"
    outs = [e for e in events if e.get("event") == "on_tool_end"]
    assert len(outs) == 1 and '"ok": true' in str(outs[0].get("data", {}).get("output")), \
        "放行 = 原地真执行"


def test_scope_freeze_is_deterministic_against_db_drift(db_session, patch_get_db, unit):
    """冻结快照与库漂移隔离：冻结后库里的 scope 行被删，谓词结果不变。

    LangChain 对 when 的确定性要求（挂起与恢复重放间一致）落在这一条上——
    直接查库会因批准写 scope 行而漂移，恢复的 reject 会被静默忽略。"""
    from app.services.gate_service import gate_service, SCOPE_STATUS, scope_gate_id
    from app.services.plan_run_service import plan_run_service
    from app.models.database import AgentGate

    cid = "t-hitl-freeze"
    steps = [
        {"step": 1, "skill": "send_learning_plan", "status": "completed",
         "args": {"student_id": "S1"}},
        {"step": 2, "skill": "assign_adaptive_practice", "status": "pending",
         "args": {"student_id": "S1"}},
    ]
    with unit(db_session):
        run = plan_run_service.start_run(db_session, cid, "计划+练习", steps)
    with unit(db_session):
        db_session.add(AgentGate(
            gate_id=scope_gate_id(cid, "student:S1"), conversation_id=cid,
            kind="scope", tool_name="send_learning_plan",
            kwargs={"run_id": run["run_id"], "object": "student:S1",
                    "steps": [1, 2]},
            action="", message="授权", status=SCOPE_STATUS))
        db_session.commit()

    gs = GuardState()
    gs.conversation_id = cid
    with unit(db_session):
        gs.freeze_hitl_scope()
    before = gs.hitl_scope_covers("assign_adaptive_practice", {"student_id": "S1"})
    assert before is True, "同对象同 run：冻结快照应判已覆盖"

    # 库漂移：scope 行删掉（模拟另一路写库），快照不动
    with unit(db_session):
        db_session.query(AgentGate).filter(
            AgentGate.gate_id == scope_gate_id(cid, "student:S1")).delete()
        db_session.commit()
    assert gs.hitl_scope_covers("assign_adaptive_practice",
                                {"student_id": "S1"}) is True, \
        "谓词只读快照：库变了结果不许变（恢复重放的一致性）"
    # 换学生不在快照里 → 不覆盖
    assert gs.hitl_scope_covers("assign_adaptive_practice",
                                {"student_id": "S2"}) is False


def test_freeze_after_draft_approval_records_new_scope(db_session, patch_get_db, unit,
                                                       sample_student):
    """批准草稿门（claim → record_run_scope）之后重冻 → 布置练习判已覆盖。

    这正是 8 步实测里承诺被违背的那条链：老师批了计划门，续接轮模型调
    assign_adaptive_practice，谓词必须说「这次批准覆盖了它」。"""
    from app.models.database import LearningPlanDraft
    from app.services.gate_service import gate_service
    from app.services.plan_run_service import plan_run_service

    cid = "t-hitl-approve"
    sid = sample_student.student_id
    steps = [
        {"step": 1, "skill": "generate_learning_plan", "status": "completed",
         "args": {"student_id": sid}},
        {"step": 2, "skill": "send_learning_plan", "status": "awaiting_confirmation",
         "args": {"student_id": sid}},
        {"step": 3, "skill": "assign_adaptive_practice", "status": "pending",
         "args": {"student_id": sid}},
    ]
    with unit(db_session):
        plan_run_service.start_run(db_session, cid, "计划+发送+练习", steps)
        db_session.add(LearningPlanDraft(draft_id="d1", student_id=sid, version=1,
                                         status="awaiting_confirmation", content={}))
        db_session.commit()
        gid = gate_service.open_gate(
            db_session, cid, kind="draft", tool_name="send_learning_plan",
            kwargs={"student_id": sid, "draft_id": "d1"},
            draft_id="d1", version=1, action="send_student",
            message="学习计划（第 1 版）等待确认")
        assert gate_service.claim_gate(db_session, gid) is True  # 批准（含记 scope）

    gs = GuardState()
    gs.conversation_id = cid
    gs.freeze_hitl_scope()  # 续接前的重冻（_run_continuation 同款时机）
    assert gs.hitl_scope_covers("assign_adaptive_practice",
                                {"student_id": sid}) is True, \
        "批准后重冻：同 run 同对象的布置练习被这次批准覆盖"
