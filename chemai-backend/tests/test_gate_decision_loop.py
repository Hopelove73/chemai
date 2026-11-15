"""确认门三值决定闭环测试（ADR-0013 最高接缝）。

直接驱动 channel 的 _gate_decision_stream（/resume decision 分支与 /stream
反馈路由共用的组合点）：假 agent 续接 + 真实 service + 内存库，断言
批准（重放发送→草稿 sent→通知→run 推进）、打回（修订→v2 面板→重开门）、
放弃（步取消→草稿保留→运行继续）三条分支的事件序列与落库结果。
"""
import asyncio
import json
from types import SimpleNamespace

import pytest

from app.models.database import (
    AgentGate, Student, StudentNotification, TeacherClassSubject,
)
from app.services.plan_draft_service import plan_draft_service as draft_svc
from app.services.plan_run_service import plan_run_service as run_svc
from app.services.work_unit import work_unit


def _create(env, student_id="s1", created_by="", student_name=""):
    """造一份待确认草稿：单元外算、单元内写（前置一）。

    单元在这里开完就关 —— 这些测试要驱动 channel 的决定流，而那条路自己开单元，
    测试层面再留一个活动单元就会撞上"不可嵌套"。
    """
    computed = draft_svc.compute_draft(env, student_id, student_name)
    if not computed.get("student_id"):
        return computed
    with work_unit(env):
        return draft_svc.create_draft(env, computed, created_by=created_by)


def _approve(env, draft_id, actor="t1"):
    with work_unit(env):
        return draft_svc.record_decision(env, draft_id, "approve", actor)


def _send(env, student_id, draft_id="", actor="t1"):
    with work_unit(env):
        return draft_svc.send_draft(env, student_id, draft_id, actor)


def _create_report(env, student_id="s1", created_by=""):
    """家长报告侧同理：聚合不出模型，所以只有"写"这一步需要单元。"""
    from app.services.parent_report_draft_service import parent_report_draft_service as rsvc
    with work_unit(env):
        return rsvc.create_draft(env, student_id, created_by=created_by)


def _approve_report(env, draft_id, actor="t1"):
    from app.services.parent_report_draft_service import parent_report_draft_service as rsvc
    with work_unit(env):
        return rsvc.record_decision(env, draft_id, "approve", actor)


def _open_gate(env, *args, **kw):
    """直接造一扇门实体的场景夹具：写入口，同样须落在单元里。"""
    from app.services.gate_service import gate_service
    with work_unit(env):
        return gate_service.open_gate(env, *args, **kw)


PLAN = {"plan_title": "张三计划", "weekly_goals": ["盐类水解"]}


@pytest.fixture
def env(db_session, monkeypatch):
    """内存库 + get_db 重定向 + 假教师身份 + 假 agent。"""
    db_session.add(Student(student_id="s1", name="张三", class_id="c1", status="approved"))
    db_session.add(TeacherClassSubject(id=1, teacher_id="t1", class_id="c1", subject="chemistry"))
    db_session.commit()

    import app.models.database as dbmod
    def _fake_get_db():
        yield db_session
    monkeypatch.setattr(dbmod, "get_db", _fake_get_db)
    monkeypatch.setattr("agent.tools.context.current_teacher", lambda: "t1")

    import sys
    mod = sys.modules["app.services.llm_service"]
    monkeypatch.setattr(mod.llm_service, "generate_learning_plan",
                        lambda **kw: {"success": True, "content": json.dumps(PLAN, ensure_ascii=False)})
    monkeypatch.setattr(mod.llm_service, "revise_learning_plan",
                        lambda **kw: {"success": True, "content": json.dumps(
                            {**PLAN, "plan_title": "张三计划v2"}, ensure_ascii=False)})

    return db_session


class FakeAgent:
    """续接回合的假 agent：astream_events 吐一小段文本即结束。"""

    async def aget_state(self, config):
        return SimpleNamespace(values={"messages": []}, next=())

    async def astream_events(self, _input, _config, version="v2"):
        yield {"event": "on_chat_model_stream", "name": "llm",
               "data": {"chunk": SimpleNamespace(content="已按意见修订")}}


def _gate(env, guard, draft_id, student_id="s1", cid="conv1", version=1):
    """开一扇草稿门（等价 guard 拦截 approval_needed 时的登记）——**实体**。

    2.4 收尾：原先往内存 `pending_approval` 里塞一份；那个字段删了，
    载荷真源是门那一行（ADR-0030）。cid 与 `_gate_decision_stream` 用的一致。
    """
    from app.services.gate_service import gate_service
    from app.services.work_unit import work_unit
    guard.conversation_id = cid
    with work_unit(env):
        gate_service.open_gate(env, cid, kind="draft", tool_name="send_learning_plan",
                               kwargs={"student_id": student_id, "draft_id": draft_id},
                               draft_id=draft_id, version=version,
                               action="send_student", message="方案待发")


def _collect(gen):
    async def go():
        return [ev async for ev in gen]
    return asyncio.run(go())


def _events_of_kinds(events, *kinds):
    out = []
    for e in events:
        raw = e[len("data: "):] if e.startswith("data: ") else e
        raw = raw.strip()
        if raw in ("[DONE]", ""):
            continue
        try:
            d = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            continue
        if d.get("type") in kinds:
            out.append(d)
    return out


def _real_adapter():
    from agent.langgraph_sse_v2 import LangGraphSSEAdapterV2
    return LangGraphSSEAdapterV2()


def _make_guard_with_plan(step_status="awaiting_confirmation"):
    from agent.guard import GuardState
    g = GuardState()
    g.plan_steps = [
        {"step": 1, "skill": "generate_learning_plan", "description": "生成", "status": "completed"},
        {"step": 2, "skill": "send_learning_plan", "description": "发送", "status": step_status},
        {"step": 3, "skill": "weekly_report", "description": "周报", "status": "pending"},
    ]
    return g


# ── approve：重放发送，闭环收口 ─────────────────────────────────

def test_approve_replays_send_and_completes_step(env):
    d = _create(env, "s1", created_by="t1")
    run_svc.start_run(env, "conv1", "goal", [
        {"step": 1, "skill": "generate_learning_plan", "status": "completed"},
        {"step": 2, "skill": "send_learning_plan", "status": "awaiting_confirmation"},
    ])
    guard = _make_guard_with_plan()
    _gate(env, guard, d["draft_id"])
    # 2026-10-17 补（gate-lifecycle 2.3）：门行也得建。`_gate` 只写内存那份，
    # 而真实登记（guard 拦下 approval_needed）**同时会开一扇门实体**，重放前
    # 要靠 `claim_gate` 占到它才算拿到"许可"。fail-closed 之后，有会话却没有门
    # 一律拒绝执行 —— 夹具缺口会把这条用例照成假红。
    _open_gate(env, "conv1", kind="draft", tool_name="send_learning_plan",
               kwargs={"student_id": "s1", "draft_id": d["draft_id"]},
               draft_id=d["draft_id"], version=1, action="send_student", message="m")

    from agent.channel.langgraph_channel import _gate_decision_stream
    from agent.tools.filesystem import _consume_elevation
    try:
        events = _collect(_gate_decision_stream(
            "conv1", {"action": "approve", "feedback": "", "draft_id": d["draft_id"]},
            guard, FakeAgent(), _real_adapter(),
            {"configurable": {"thread_id": "conv1"}, "recursion_limit": 24}))
    finally:
        # 重放路径会置进程级 elevation（parent 报告门仍在用），清理防跨文件泄漏
        _consume_elevation()

    tool_results = _events_of_kinds(events, "tool_result")
    assert tool_results and tool_results[0]["success"] is True
    assert any(e.get("status") == "completed" and e.get("current_step") == 2
               for e in _events_of_kinds(events, "plan_progress"))
    assert events[-1] == "data: [DONE]\n\n"
    # 落库：草稿 sent + 学生收件箱通知
    assert draft_svc.get_draft(env, d["draft_id"])["status"] == "sent"
    assert env.query(StudentNotification).count() == 1
    run = run_svc.get_active_run(env, "conv1")
    assert run is None or all(s["status"] != "awaiting_confirmation" for s in run["steps"])
    # 决定账本（T2 后端）：批准这一下必须留下一行。没有它，「谁批的、批的哪一版」
    # 只能从 `agent_gates` 当前态猜——而那行会被下一次重开门覆盖掉。
    from app.models.database import AgentGateDecision
    rows = env.query(AgentGateDecision).all()
    assert len(rows) == 1, f"批准应恰好记一行账，实际 {len(rows)}"
    assert rows[0].decision == "approved"
    assert rows[0].gate_id, "账本必须带 gate_id，否则回放时追不回去"
    assert rows[0].tool_name == "send_learning_plan"
    assert rows[0].kind == "draft"


# ── reject：确定性修订 → v2 面板 → 重开门 ───────────────────────

def test_reject_revises_and_reopens_gate(env):
    d = _create(env, "s1", created_by="t1")
    guard = _make_guard_with_plan()
    _gate(env, guard, d["draft_id"])

    from agent.channel.langgraph_channel import _gate_decision_stream
    events = _collect(_gate_decision_stream(
        "conv1", {"action": "reject", "feedback": "第二周改成盐类水解专题", "draft_id": ""},
        guard, FakeAgent(), _real_adapter(),
        {"configurable": {"thread_id": "conv1"}, "recursion_limit": 24}))

    # 事件序列：revising → component(v2) → 门卡(draft_id) → awaiting_confirmation → 文本 → done
    progresses = _events_of_kinds(events, "plan_progress")
    assert progresses and progresses[0]["status"] == "revising"
    comps = _events_of_kinds(events, "component")
    assert comps and comps[0]["component"] == "learning_plan"
    assert comps[0]["params"]["data"]["plan"]["plan_title"] == "张三计划v2"
    gates = _events_of_kinds(events, "gate_open")
    gate = [g for g in gates if g.get("draft_id") == d["draft_id"]]
    assert gate and gate[-1]["version"] == 2
    assert gate[-1]["gate_id"] == f"g-draft-{d['draft_id']}"
    assert gate[-1]["recipient"] == "student"
    assert progresses[-1]["status"] == "awaiting_confirmation"
    texts = _events_of_kinds(events, "text")
    assert texts, "打回后 LLM 应说明修改点"
    # 落库：v2 带反馈；门重新挂上同一草稿
    v2 = draft_svc.get_draft(env, d["draft_id"])
    assert v2["version"] == 2 and v2["feedback"] == "第二周改成盐类水解专题"
    assert v2["teacher_approved"] is False
    from app.services.gate_service import gate_service
    _og = gate_service.get_open_gate(env, "conv1")
    assert _og is not None and _og["draft_id"] == d["draft_id"], "门应重开在同一草稿上"
    assert events[-1] == "data: [DONE]\n\n"
    # 打回也要记账，且 detail 里留着老师写的修改意见（这是回放时唯一能追的原文）
    from app.models.database import AgentGateDecision
    rows = env.query(AgentGateDecision).all()
    assert len(rows) == 1
    assert rows[0].decision == "rejected"
    assert rows[0].detail == "第二周改成盐类水解专题"
    assert rows[0].gate_id == f"g-draft-{d['draft_id']}"


def test_reject_without_feedback_errors(env):
    d = _create(env, "s1")
    guard = _make_guard_with_plan()
    _gate(env, guard, d["draft_id"])
    from agent.channel.langgraph_channel import _gate_decision_stream
    events = _collect(_gate_decision_stream(
        "conv1", {"action": "reject", "feedback": "", "draft_id": ""},
        guard, FakeAgent(), _real_adapter(),
        {"configurable": {"thread_id": "conv1"}, "recursion_limit": 24}))
    errs = _events_of_kinds(events, "error")
    assert errs and "修改意见" in errs[0]["message"]
    assert draft_svc.get_draft(env, d["draft_id"])["version"] == 1


# ── abandon：步取消、运行继续、草稿保留 ─────────────────────────

def test_abandon_cancels_step_run_continues(env):
    """放弃不是不可逆动作：没门可关（决定不带 `gate_id`、关也关不到行）时仍然成功。

    R8 D9 的判空保护在这一条上：单元里"只有这一次写"，关不到行不构成半截状态，
    所以走留痕而不是报错 —— 把放弃误判成失败会打断运行续接。
    """
    d = _create(env, "s1")
    run_svc.start_run(env, "conv1", "goal", [
        {"step": 2, "skill": "send_learning_plan", "status": "awaiting_confirmation"},
        {"step": 3, "skill": "weekly_report", "status": "pending"},
    ])
    guard = _make_guard_with_plan()
    _gate(env, guard, d["draft_id"])

    from agent.channel.langgraph_channel import _gate_decision_stream
    events = _collect(_gate_decision_stream(
        "conv1", {"action": "abandon", "feedback": "", "draft_id": ""},
        guard, FakeAgent(), _real_adapter(),
        {"configurable": {"thread_id": "conv1"}, "recursion_limit": 24}))

    progresses = _events_of_kinds(events, "plan_progress")
    assert any(e.get("status") == "cancelled" for e in progresses)
    assert _events_of_kinds(events, "text"), "放弃后 LLM 应说明并继续"
    assert events[-1] == "data: [DONE]\n\n"
    # 草稿保留待确认；待批已释放；run 继续走
    assert draft_svc.get_draft(env, d["draft_id"])["status"] == "awaiting_confirmation"
    from app.services.gate_service import gate_service
    assert gate_service.get_open_gate(env, "conv1") is None
    # 放弃也要记账：这扇门被关掉过一次，回放时要能回答「谁关的、关的是哪扇」
    from app.models.database import AgentGateDecision
    _rows = env.query(AgentGateDecision).all()
    assert len(_rows) == 1, f"放弃应恰好记一行账，实际 {len(_rows)}"
    assert _rows[0].decision == "cancelled"
    assert _rows[0].gate_id, "放弃也要带上被关的那扇门的 id"
    assert _rows[0].kind in ("draft", "boolean")
    run = run_svc.get_active_run(env, "conv1")
    assert run and run["status"] == "running"


def test_abandon_cancels_the_gate_bound_generate_step(env):
    """真实计划形状：只有 `generate_learning_plan`，没有同名发送步。

    门绑在这条生成步上（plan_step=4）；放弃必须取消它 —— 按 tool_name
    （`send_learning_plan`）找同名步会一个也找不到，把步永远留在等待确认（F5）。
    """
    from agent.guard import GuardState
    from app.services.gate_service import draft_gate_id, gate_service

    d = _create(env, "s1")
    run_svc.start_run(env, "conv1", "goal", [
        {"step": 1, "skill": "show_students", "status": "completed"},
        {"step": 4, "skill": "generate_learning_plan", "status": "awaiting_confirmation"},
    ])
    guard = GuardState()
    guard.conversation_id = "conv1"
    guard.plan_steps = [dict(s) for s in run_svc.get_active_run(env, "conv1")["steps"]]
    _gate(env, guard, d["draft_id"])
    assert gate_service.bind_step(env, draft_gate_id(d["draft_id"]), 4)

    from agent.channel.langgraph_channel import _gate_decision_stream
    events = _collect(_gate_decision_stream(
        "conv1", {"action": "abandon", "feedback": "", "draft_id": ""},
        guard, FakeAgent(), _real_adapter(),
        {"configurable": {"thread_id": "conv1"}, "recursion_limit": 24}))

    progresses = _events_of_kinds(events, "plan_progress")
    assert any(e.get("current_step") == 4 and e.get("status") == "cancelled"
               for e in progresses), f"绑定步没被取消：{progresses}"
    from app.models.database import PlanRun
    row = (env.query(PlanRun).filter(PlanRun.conversation_id == "conv1")
           .order_by(PlanRun.id.desc()).first())
    assert row and any(s["step"] == 4 and s["status"] == "cancelled"
                       for s in row.steps)


# ── 布尔门 abandon/reject（S2：三值保留，走决定流的布尔门分支） ──────

def _boolean_gate_scene(env):
    """布尔门场景：开门（绑定第 5 步）+ 同步好 plan_steps 的 guard。"""
    from agent.guard import GuardState
    gid = _open_gate(env, "conv1", kind="boolean",
                     tool_name="assign_adaptive_practice",
                     kwargs={"student_id": "s1", "quantity": 5}, action="",
                     message="此操作需要老师确认后执行")
    run_svc.start_run(env, "conv1", "goal", [
        {"step": 5, "skill": "assign_adaptive_practice",
         "status": "awaiting_confirmation"},
    ])
    from app.services.gate_service import gate_service
    assert gate_service.bind_step(env, gid, 5)
    guard = GuardState()
    guard.conversation_id = "conv1"
    guard.plan_steps = [dict(s) for s in run_svc.get_active_run(env, "conv1")["steps"]]
    return gid, guard


def test_boolean_abandon_closes_gate_ledgers_and_cancels_step(env):
    """S2 根治（审查 C1）：布尔门点「放弃」不再是空操作。

    旧行为：分类时被丢成 legacy → Command(resume="cancelled") 对无挂起的图
    静默无操作（LangGraph 实测不抛错）→ 门不关、账不记、步停在等待确认，
    而前端乐观翻卡已显示「已放弃」。现在必须：门落 cancelled、账本恰好一行
    cancelled（kind=boolean）、绑定步取消、续接说明照发。
    """
    from app.services.gate_service import gate_service

    gid, guard = _boolean_gate_scene(env)
    from agent.channel.langgraph_channel import _gate_decision_stream
    events = _collect(_gate_decision_stream(
        "conv1", {"action": "abandon", "feedback": "", "draft_id": "",
                  "gate_id": gid},
        guard, FakeAgent(), _real_adapter(),
        {"configurable": {"thread_id": "conv1"}, "recursion_limit": 24}))

    assert gate_service.get_open_gate(env, "conv1") is None, "门必须真的关上"
    from app.models.database import AgentGateDecision
    rows = env.query(AgentGateDecision).all()
    assert len(rows) == 1 and rows[0].decision == "cancelled" \
        and rows[0].kind == "boolean" and rows[0].gate_id == gid
    progresses = _events_of_kinds(events, "plan_progress")
    assert any(e.get("current_step") == 5 and e.get("status") == "cancelled"
               for e in progresses), f"绑定步没被取消：{progresses}"
    assert _events_of_kinds(events, "text"), "放弃后 LLM 应说明并继续"
    assert events[-1] == "data: [DONE]\n\n"


def test_boolean_reject_closes_gate_with_feedback_ledger(env):
    """布尔门 reject（ADR-0031 / S2）：不执行、说一句为什么 —— 不修订不重开，
    反馈进账本 detail；与 LangChain 1.x HITL 的 reject-with-feedback 同构。"""
    from app.services.gate_service import gate_service

    gid, guard = _boolean_gate_scene(env)
    from agent.channel.langgraph_channel import _gate_decision_stream
    events = _collect(_gate_decision_stream(
        "conv1", {"action": "reject", "feedback": "题量太多了", "draft_id": "",
                  "gate_id": gid},
        guard, FakeAgent(), _real_adapter(),
        {"configurable": {"thread_id": "conv1"}, "recursion_limit": 24}))

    assert gate_service.get_open_gate(env, "conv1") is None
    from app.models.database import AgentGateDecision
    rows = env.query(AgentGateDecision).all()
    assert len(rows) == 1 and rows[0].decision == "rejected" \
        and rows[0].kind == "boolean" and "题量太多了" in (rows[0].detail or "")
    progresses = _events_of_kinds(events, "plan_progress")
    assert any(e.get("status") == "cancelled" for e in progresses)
    assert _events_of_kinds(events, "text"), "打回后 LLM 应带着反馈说明并继续"
    assert events[-1] == "data: [DONE]\n\n"


def test_rejected_by_teacher_cancels_not_fails():
    """pilot 路径的工具错误码 rejected_by_teacher → 步取消而非失败。

    2026-10-24 带开关实测抓偏：pilot 下打回/放弃后第 4 步落 failed，老师看到
    「失败 ✗」而非「已放弃」。legacy 路径写 cancelled，两机制必须同终态。
    """
    from app.services.plan_run_service import compute_tool_transition
    steps = [{"step": 4, "skill": "assign_adaptive_practice",
              "status": "awaiting_confirmation"}]
    transitions = compute_tool_transition(
        steps, "assign_adaptive_practice",
        {"error": "rejected_by_teacher", "rejected": True})
    assert transitions == [(4, "cancelled")]
    assert steps[0]["status"] == "cancelled"


# ── 协议归一：decision 对象优先，旧字符串向后兼容 ────────────────

def test_normalize_decision_compat():
    from app.services.decision_intake import normalize_decision as _normalize_decision

    class R:
        def __init__(self, message="", decision=None):
            self.message = message
            self.decision = decision

    assert _normalize_decision(R(decision={"action": "reject", "feedback": "改"})) == {
        "action": "reject", "feedback": "改", "draft_id": "", "gate_id": ""}
    assert _normalize_decision(R(message="approved"))["action"] == "approve"
    assert _normalize_decision(R(message="cancelled"))["action"] == "abandon"
    # ADR-0015：中文确认词白名单已废止——打字永不构成批准，协议值只剩
    # approved/cancelled；「批准」不再映射 approve。
    assert _normalize_decision(R(message="批准")) is None
    # decision 对象携带 gate_id（强绑定键）原样透传
    d = _normalize_decision(R(decision={"action": "approve", "gate_id": "g-draft-x"}))
    assert d["gate_id"] == "g-draft-x"
    assert _normalize_decision(R(message="随便聊聊")) is None
    # decision 对象优先于 message 字符串
    assert _normalize_decision(R(message="approved", decision={"action": "abandon"}))["action"] == "abandon"


def test_normalize_decision_rejects_unknown_action():
    """C1 入口收口：三值之外的 action 一律判「非决定」，绝不透传到重放路径。"""
    from app.services.decision_intake import normalize_decision as _normalize_decision

    class R:
        def __init__(self, decision=None):
            self.message = ""
            self.decision = decision

    assert _normalize_decision(R(decision={"action": "modify", "gate_id": "g"})) is None
    assert _normalize_decision(R(decision={"action": "aprove"})) is None  # 拼错
    assert _normalize_decision(R(decision={"action": ""})) is None
    # 合法 action 大小写归一
    assert _normalize_decision(R(decision={"action": "APPROVE"}))["action"] == "approve"


def test_resolve_resume_intent_malformed_decision_no_ghost_approval():
    """C1(a) fail-closed：畸形决定对象绝不回退用 message 当批准（幽灵发送入口封堵）。"""
    from app.services.decision_intake import resume_intent as _resolve_resume_intent

    class Req:
        def __init__(self, message="", decision=None):
            self.message = message
            self.decision = decision

    # 决定对象带非法 action，即便夹带 message="approved" → 不得复活成批准
    dec, resp = _resolve_resume_intent(
        Req(message="approved", decision={"action": "modify"}), "send_learning_plan")
    assert dec is None
    assert resp != "approved", "畸形决定不得回退用 message 当批准"

    # 合法三值决定：正常归一，批准意图保住
    dec2, resp2 = _resolve_resume_intent(
        Req(message="", decision={"action": "approve", "draft_id": "d1"}),
        "send_learning_plan")
    assert dec2 and dec2["action"] == "approve"
    assert resp2 == "approved"

    # 旧客户端兼容路径（无决定对象，仅协议字符串 message）保持不变
    dec3, resp3 = _resolve_resume_intent(Req(message="approved"), "send_learning_plan")
    assert resp3 == "approved"


def test_unknown_action_fails_closed_drives_no_send(env):
    """C1 幽灵批准回归：陌生 action 绝不落到默认「批准重放」真发送。"""
    d = _create(env, "s1", created_by="t1")
    guard = _make_guard_with_plan()
    _gate(env, guard, d["draft_id"])
    from agent.channel.langgraph_channel import _gate_decision_stream
    events = _collect(_gate_decision_stream(
        "conv1", {"action": "modify", "feedback": "", "draft_id": d["draft_id"]},
        guard, FakeAgent(), _real_adapter(),
        {"configurable": {"thread_id": "conv1"}, "recursion_limit": 24}))
    errs = _events_of_kinds(events, "error")
    assert errs, "未知决定应显式报错"
    assert not _events_of_kinds(events, "tool_result"), "未知 action 不得重放发送"
    assert draft_svc.get_draft(env, d["draft_id"])["status"] != "sent"
    assert draft_svc.get_draft(env, d["draft_id"])["teacher_approved"] is False
    from app.services.gate_service import gate_service
    assert gate_service.get_open_gate(env, "conv1") is not None,         "报错不得清空当前门（待老师重新决定）"


def test_gate_draft_id_resolves_active_draft_after_unpack_bug(env):
    """C2 回归：decision 未带 draft_id 时，_gate_draft_id 能从待批 kwargs 的
    student_id 兜底解析出该生活跃草稿（旧代码三值按两值解包 → 每次静默返空）。"""
    d = _create(env, "s1", created_by="t1")
    guard = _make_guard_with_plan()
    # 2.4 收尾：待批那件事问实体。这里要的是"门行 kwargs 里没有 draft_id"的形状，
    # 所以开一扇 draft_id 为空的门（判据 R23 允许这种行）。
    _open_gate(env, "conv1", kind="draft", tool_name="send_learning_plan",
               kwargs={"student_id": "s1", "draft_id": ""}, action="send_student")
    guard.conversation_id = "conv1"
    from agent.channel.langgraph_channel import _gate_draft_id
    got = _gate_draft_id(guard, {"draft_id": ""})
    assert got == d["draft_id"], f"应解析出活跃草稿，实得 {got!r}"


# ── fail-closed：身份与 Data Scope（ADR-0009）──────────────────

def test_approve_without_identity_denied(env, monkeypatch):
    d = _create(env, "s1")
    guard = _make_guard_with_plan()
    _gate(env, guard, d["draft_id"])
    monkeypatch.setattr("agent.tools.context.current_teacher", lambda: "")
    from agent.channel.langgraph_channel import _gate_decision_stream
    events = _collect(_gate_decision_stream(
        "conv1", {"action": "approve", "feedback": "", "draft_id": d["draft_id"]},
        guard, FakeAgent(), _real_adapter(),
        {"configurable": {"thread_id": "conv1"}, "recursion_limit": 24}))
    errs = _events_of_kinds(events, "error")
    assert errs and "缺少用户身份" in errs[0]["message"]  # 批 3：家长门角色感知，文案改用户身份
    assert draft_svc.get_draft(env, d["draft_id"])["teacher_approved"] is False


def test_reject_out_of_scope_teacher_denied(env, monkeypatch):
    """老师不教该生所在班 → 打回被拒（fail-closed）。"""
    d = _create(env, "s1")
    guard = _make_guard_with_plan()
    _gate(env, guard, d["draft_id"])
    monkeypatch.setattr("agent.tools.context.current_teacher", lambda: "ghost")
    from agent.channel.langgraph_channel import _gate_decision_stream
    events = _collect(_gate_decision_stream(
        "conv1", {"action": "reject", "feedback": "改", "draft_id": d["draft_id"]},
        guard, FakeAgent(), _real_adapter(),
        {"configurable": {"thread_id": "conv1"}, "recursion_limit": 24}))
    errs = _events_of_kinds(events, "error")
    assert errs and "Data Scope" in errs[0]["message"]
    assert draft_svc.get_draft(env, d["draft_id"])["version"] == 1


def test_approve_conflict_with_already_sent_draft(env):
    """双通道冲突：草稿已被 REST 侧确认发送 → 聊天门批准显式报错，不重放。

    2026-10-16 补（gate-lifecycle ④）：报错早退之外，**那扇门必须一并判死**。
    它正是被发送那次留下的孤儿。留着它的后果不是多一行脏数据：`guard` 拿
    「库里还有 open 的门」当「有一张确认卡待老师处理」，于是拒掉 agent 的
    **一切**工具调用 —— 计划卡永远停在等待态、run 永远 running（端到端实测的死锁）。

    门行在这里**直接造**（`db.add`）而不是走 `open_gate`：`open_gate` 末尾自带一次
    本会话收敛，草稿已 sent 时新门开出来当场就被收掉，造不出孤儿。直接造行模拟的
    是**存量**——演示库里这么躺着的 open 门有 74 行（2026-10-16 只读盘点）。
    """
    d = _create(env, "s1")
    _approve(env, d["draft_id"])
    _send(env, "s1", d["draft_id"])
    env.add(AgentGate(
        gate_id="g-orphan-sent", conversation_id="conv1", kind="draft",
        tool_name="send_learning_plan", kwargs={}, draft_id=d["draft_id"],
        version=1, action="send_student", message="学习计划等待确认", status="open"))
    env.commit()
    guard = _make_guard_with_plan()
    _gate(env, guard, d["draft_id"])
    from agent.channel.langgraph_channel import _gate_decision_stream
    from agent.tools.filesystem import _consume_elevation
    try:
        events = _collect(_gate_decision_stream(
            "conv1", {"action": "approve", "feedback": "", "draft_id": d["draft_id"]},
            guard, FakeAgent(), _real_adapter(),
            {"configurable": {"thread_id": "conv1"}, "recursion_limit": 24}))
    finally:
        _consume_elevation()
    errs = _events_of_kinds(events, "error")
    assert errs and ("已确认发送给学生" in errs[0]["message"] or "不能处理决定" in errs[0]["message"])
    tool_results = _events_of_kinds(events, "tool_result")
    assert not tool_results, "草稿已 sent 不得重放发送"
    # ④：早退的同时，那扇门必须判死 —— 不能留着它继续全拦 agent
    row = env.query(AgentGate).filter(AgentGate.gate_id == "g-orphan-sent").first()
    assert row.status != "open", (
        "已发送草稿的孤儿门没被收敛：它会让 guard 一直拒掉 agent 的一切工具调用"
    )


# ── 门卡 DB 重同步（重启后纯 DB 依据）─────────────────────────

def test_gate_resync_from_run_steps_after_restart(env, monkeypatch):
    """/chat/history 的门解析不依赖内存 guard：run 步骤的 gate_draft_id 可重建门卡。"""
    run_svc.start_run(env, "conv9", "goal", [
        {"step": 1, "skill": "generate_learning_plan", "status": "completed"},
        {"step": 2, "skill": "send_learning_plan", "status": "awaiting_confirmation"},
    ])
    d = _create(env, "s1")
    run_svc.mark_step(env, "conv9", 2, "awaiting_confirmation", gate_draft_id=d["draft_id"])

    # 模拟重启：内存 guard 缓存为空
    monkeypatch.setattr("agent.channel.langgraph_channel._guard_state_cache", {})

    from agent.channel.conversation import _active_run_payload
    payload = _active_run_payload(env, "conv9")
    assert payload["gate"], "重启后门卡必须能从 DB 重建"
    assert payload["gate"]["draft_id"] == d["draft_id"]
    assert payload["gate"]["version"] == 1


# ── 家长报告门（issue #12：第二个工件走同一三值协议）─────────────

def _gate_report(env, guard, draft_id, student_id="s1", cid="conv1"):
    """开一扇家长报告草稿门（**实体**，2.4 收尾）。"""
    from app.services.gate_service import gate_service
    from app.services.work_unit import work_unit
    guard.conversation_id = cid
    with work_unit(env):
        gate_service.open_gate(env, cid, kind="draft", tool_name="send_report_to_parent",
                               kwargs={"student_id": student_id, "draft_id": draft_id},
                               draft_id=draft_id, version=1,
                               action="send_parent", message="报告待发")


def test_report_gate_approve_replays_send(env):
    from app.models.database import Parent, StudentParentBinding
    env.add(Parent(parent_id="p1", name="家长一", password_hash="x"))
    env.add(StudentParentBinding(binding_id=1, student_id="s1", parent_id="p1",
                                 status="active", bind_code="bc1"))
    env.commit()
    from app.services.parent_report_draft_service import parent_report_draft_service as rsvc
    d = _create_report(env, created_by="t1")
    _approve_report(env, d["draft_id"])
    guard = _make_guard_with_plan()
    _gate_report(env, guard, d["draft_id"])
    # 同上（gate-lifecycle 2.3）：真实登记会同时开一扇门实体，夹具补齐
    _open_gate(env, "conv1", kind="draft", tool_name="send_report_to_parent",
               kwargs={"student_id": "s1", "draft_id": d["draft_id"]},
               draft_id=d["draft_id"], version=1, action="send_parent", message="m")
    from agent.channel.langgraph_channel import _gate_decision_stream
    from agent.tools.filesystem import _consume_elevation
    try:
        events = _collect(_gate_decision_stream(
            "conv1", {"action": "approve", "feedback": "", "draft_id": d["draft_id"]},
            guard, FakeAgent(), _real_adapter(),
            {"configurable": {"thread_id": "conv1"}, "recursion_limit": 24}))
    finally:
        _consume_elevation()
    tool_results = _events_of_kinds(events, "tool_result")
    assert tool_results and tool_results[0]["name"] == "send_report_to_parent"
    assert tool_results[0]["success"] is True
    assert rsvc.get_draft(env, d["draft_id"])["status"] == "sent"


def test_report_gate_reject_revises_and_reopens(env, monkeypatch):
    import sys
    mod = sys.modules["app.services.llm_service"]
    monkeypatch.setattr(mod.llm_service, "revise_parent_report",
                        lambda **kw: {"success": True, "report": {"suggestion": "已按意见改写"}})
    from app.services.parent_report_draft_service import parent_report_draft_service as rsvc
    d = _create_report(env, created_by="t1")
    guard = _make_guard_with_plan()
    _gate_report(env, guard, d["draft_id"])
    from agent.channel.langgraph_channel import _gate_decision_stream
    events = _collect(_gate_decision_stream(
        "conv1", {"action": "reject", "feedback": "弱化排名表述", "draft_id": ""},
        guard, FakeAgent(), _real_adapter(),
        {"configurable": {"thread_id": "conv1"}, "recursion_limit": 24}))
    comps = _events_of_kinds(events, "component")
    assert comps and comps[0]["component"] == "parent_report"
    assert comps[0]["params"]["data"]["report_data"]["suggestion"] == "已按意见改写"
    gates = _events_of_kinds(events, "gate_open")
    gate = [g for g in gates if g.get("draft_id") == d["draft_id"]]
    assert gate and gate[0]["action"] == "send_parent"
    assert gate[0]["recipient"] == "parent"
    v2 = rsvc.get_draft(env, d["draft_id"])
    assert v2["version"] == 2 and v2["feedback"] == "弱化排名表述"
    from app.services.gate_service import gate_service
    _og = gate_service.get_open_gate(env, "conv1")
    assert _og is not None and _og["draft_id"] == d["draft_id"], "门应重开在同一草稿上"


# ── 会话门映射：裸生成门重启后从 DB 重建（#13 冒烟补丁 2）────────

def test_gate_resync_without_run_via_conversation_gate(env, monkeypatch):
    """无 run 的裸生成门：gate 实体记录后，重启（缓存清空）仍能重建。"""
    d = _create(env, "s1")
    _open_gate(env, "conv-bare", kind="draft",
                           tool_name="send_learning_plan",
                           kwargs={"student_id": "s1", "draft_id": d["draft_id"]},
                           draft_id=d["draft_id"], version=d["version"],
                           action="send_student", message="等待确认")

    monkeypatch.setattr("agent.channel.langgraph_channel._guard_state_cache", {})
    from agent.channel.conversation import _active_run_payload
    payload = _active_run_payload(env, "conv-bare")
    assert payload["gate"], "裸生成门重启后必须能从 gate 实体重建"
    assert payload["gate"]["draft_id"] == d["draft_id"]
    assert payload["gate"]["action"] == "send_student"


def test_gate_mapping_self_heals_when_sent(env, monkeypatch):
    """草稿已 sent → 门映射读时自愈清除，不再重开门卡。"""
    d = _create(env, "s1")
    from app.services.plan_run_service import plan_run_service
    plan_run_service.record_gate(env, "conv-heal", d["draft_id"], "learning_plan")
    _approve(env, d["draft_id"])
    _send(env, "s1", d["draft_id"])

    monkeypatch.setattr("agent.channel.langgraph_channel._guard_state_cache", {})
    from agent.channel.conversation import _active_run_payload
    payload = _active_run_payload(env, "conv-heal")
    assert payload["gate"] is None, "已发送的草稿不得再开卡"


def test_report_gate_mapping_artifact_aware(env, monkeypatch):
    """报告门的映射重建带 send_parent 动作与报告文案。"""
    d = _create_report(env)
    _open_gate(env, "conv-rep", kind="draft",
                           tool_name="send_report_to_parent",
                           kwargs={"student_id": "s1", "draft_id": d["draft_id"]},
                           draft_id=d["draft_id"], version=d["version"],
                           action="send_parent", message="学习报告等待确认")

    monkeypatch.setattr("agent.channel.langgraph_channel._guard_state_cache", {})
    from agent.channel.conversation import _active_run_payload
    payload = _active_run_payload(env, "conv-rep")
    assert payload["gate"] and payload["gate"]["action"] == "send_parent"
    assert "学习报告" in payload["gate"]["message"]


# ── 双执行的落地面：第二次批准到底回什么（eng 三轮 F5/D7）────────────────

def test_second_approval_does_not_execute_and_answers_in_plain_words(env):
    """两个标签页/快点一下：第二次批准不许再跑动作，要给人话，不许凭空造一张卡。

    `claim_gate` 的一次性已有断言（test_gate_unit_of_work），那条只管"抢不到"；
    这一条管的是抢不到的那次**老师看见什么** —— 页面靠它把卡置灰。"""
    import asyncio

    from agent.channel.langgraph_channel import _direct_replay_events
    from agent.guard import GuardState

    # 重放目标**只**来自门实体（gate-lifecycle 2.4），所以这里不再往内存里塞假工具：
    # 塞了也不作数 —— 要执行的是门那行记着的 `delete_bank` + 它记着的 kwargs。
    # 于是"跑了几次"改成看**真实效果**（题库还在不在），比数一个假工具的调用次数更硬：
    # 假工具只证明"我的假函数被调了"，真效果证明"那件事真的发生了 / 没发生第二次"。
    from app.services.question_bank import question_bank_service
    sid = question_bank_service.create_set(env, name="待删题库", teacher_id="t1")["set_id"]

    gid = _open_gate(env, "conv-twice", kind="boolean", tool_name="delete_bank",
                     kwargs={"set_id": sid}, message="删这套题库")

    def approve():
        return asyncio.run(_direct_replay_events(GuardState(), True, "conv-twice", gid))

    _, info = approve()
    assert info is not None, "第一次批准就该把动作跑掉"
    assert question_bank_service.get_set(env, sid) is None, "题库没被删掉"
    assert env.query(AgentGate).filter_by(gate_id=gid).one().status == "executed", \
        "做成了要收口成 executed"

    events2, info2 = approve()
    assert info2 is None, "第二次的破坏性动作绝不能跑第二遍"
    payload = [json.loads(e) for e in events2]
    assert payload[0]["type"] == "error" and payload[0]["recoverable"] is True
    assert "已经处理过" in payload[0]["message"]
    # 那扇门已被第一次占到 executed：没有开放门就不许重发一张卡出来
    assert not [p for p in payload if p.get("type") == "gate_open"], payload


def test_taken_events_re_emit_the_current_open_gate(env):
    """占不到门时若该会话还有别的开放门，要把**那一扇**重新发回页面对齐。"""
    from agent.channel.langgraph_channel import _gate_taken_events

    gid = _open_gate(env, "conv-reopen", kind="boolean", tool_name="bash",
                     kwargs={"command": "ls"}, message="当前这扇")

    out = [json.loads(e) for e in _gate_taken_events("conv-reopen")]

    assert out[0]["type"] == "error" and out[0]["recoverable"] is True
    assert out[1]["type"] == "gate_open"
    assert out[1]["gate_id"] == gid
    assert out[1]["message"] == "当前这扇"
