"""布尔门批准路径的强绑定回归（grilling 2026-09-22 · 决策 A）。

两道防线，草稿门 `_gate_decision_stream` 早已具备，布尔门 legacy 重放路径缺失：

1. **按 DB kind 分类**（`_resolve_resume_intent`）：三值决定 vs 布尔 legacy 的
   分野原先靠 `pending_name in _GATE_PLAN_TOOLS or draft_id` 猜——工具名集合
   漂移、或草稿门未带 draft_id 时误判。真正权威的是开放门的 `kind`。

2. **check_binding 补到布尔重放**（`_direct_replay_events`）：老师点的是旧卡
   gate_id=G1，当前开着的门已被新门 G2 取代（open_gate 幂等互斥）。无绑定校验
   时 `replay_target` 从**当前**开放门 G2 重建并重放 → 批准门 G1 却执行了门 G2
   的动作（跨发射）。绑定不符必须拒执行、重发当前门让 UI 对齐。
"""
import asyncio
import json

from agent.guard import GuardState


def _run(coro):
    async def go():
        return await coro
    return asyncio.run(go())




def _evt(events, typ):
    for e in events:
        if json.loads(e).get("type") == typ:
            return json.loads(e)
    return None


def test_boolean_replay_binding_blocks_cross_gate(db_session, monkeypatch, patch_get_db, unit):
    """批准旧门 G1（布置），当前门是 G2（删除）→ 一个都不许执行，重发 G2。"""
    import agent.langgraph_agent_v2 as v2
    from app.services.gate_service import gate_service
    from agent.channel.langgraph_channel import _direct_replay_events

    calls = []

    async def fake_assign(student_id: str = ""):
        calls.append(("assign", student_id))
        return json.dumps({"ok": True})

    async def fake_delete(student_id: str = ""):
        calls.append(("delete", student_id))
        return json.dumps({"ok": True})

    monkeypatch.setitem(v2._TOOL_BY_NAME, "assign_adaptive_practice", fake_assign)
    monkeypatch.setitem(v2._TOOL_BY_NAME, "delete_student_data", fake_delete)

    with unit(db_session):                      # 写入口须落在工作单元里（前置一）
        g1 = gate_service.open_gate(
            db_session, conversation_id="conv-x", kind="boolean",
            tool_name="assign_adaptive_practice", kwargs={"student_id": "241914"},
            message="布置练习")
        g2 = gate_service.open_gate(
            db_session, conversation_id="conv-x", kind="boolean",
            tool_name="delete_student_data", kwargs={"student_id": "241914"},
            message="删除数据")
    assert gate_service.get_open_gate(db_session, "conv-x")["gate_id"] == g2

    gs = GuardState()
    gs.conversation_id = "conv-x"
    # 内存 pending 缺失（模拟重启/新流）→ 重建会拿到当前门 G2 的删除调用
    events, replay = _run(_direct_replay_events(gs, True, "conv-x", g1))

    assert calls == [], f"批准旧门 G1 绝不该执行当前门 G2 的动作: {calls}"
    assert replay is None, "绑定不符不得产出续接信息"
    assert _evt(events, "error") is not None, "应回一条人话错误"
    reopen = _evt(events, "gate_open")
    assert reopen and reopen["gate_id"] == g2, "应重发当前门 G2 让 UI 对齐"


def test_boolean_replay_binding_matches_executes(db_session, monkeypatch, patch_get_db, unit):
    """批准的正是当前开放门 → 照常重放（不得误挡正常批准）。"""
    import agent.langgraph_agent_v2 as v2
    from app.services.gate_service import gate_service
    from agent.channel.langgraph_channel import _direct_replay_events

    calls = []

    async def fake_assign(student_id: str = ""):
        calls.append(("assign", student_id))
        return json.dumps({"ok": True})

    monkeypatch.setitem(v2._TOOL_BY_NAME, "assign_adaptive_practice", fake_assign)

    with unit(db_session):
        g1 = gate_service.open_gate(
            db_session, conversation_id="conv-ok", kind="boolean",
            tool_name="assign_adaptive_practice", kwargs={"student_id": "241914"},
            message="布置练习")

    gs = GuardState()
    gs.conversation_id = "conv-ok"
    events, replay = _run(_direct_replay_events(gs, True, "conv-ok", g1))
    assert calls == [("assign", "241914")], "批准匹配的门必须照常执行"
    assert replay and replay["name"] == "assign_adaptive_practice"


def test_boolean_replay_binding_empty_gate_id_passes_through(db_session, monkeypatch, patch_get_db, unit):
    """旧客户端未带 gate_id + 单门开着 → 放行（沿用 check_binding 兼容契约）。"""
    import agent.langgraph_agent_v2 as v2
    from app.services.gate_service import gate_service
    from agent.channel.langgraph_channel import _direct_replay_events

    calls = []

    async def fake_assign(student_id: str = ""):
        calls.append(("assign", student_id))
        return json.dumps({"ok": True})

    monkeypatch.setitem(v2._TOOL_BY_NAME, "assign_adaptive_practice", fake_assign)
    with unit(db_session):
        gate_service.open_gate(
            db_session, conversation_id="conv-legacy", kind="boolean",
            tool_name="assign_adaptive_practice", kwargs={"student_id": "241914"},
            message="布置练习")

    gs = GuardState()
    gs.conversation_id = "conv-legacy"
    events, replay = _run(_direct_replay_events(gs, True, "conv-legacy", ""))
    assert calls == [("assign", "241914")], "空 gate_id 旧客户端单门场景不得被挡"
