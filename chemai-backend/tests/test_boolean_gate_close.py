"""布尔门批准后未关实体 → 卡片复活 + 二次重放双发送。

回归根因（diagnosing-bugs 2026-09-22）：resume 布尔门批准走
`_direct_replay_events`，它只清内存 pending、裸重放工具，从不关 DB 里
status=open 的 AgentGate 行（关门的 close_gate 只在草稿门处理器
`_gate_decision_stream` 里调用）。而 `get_open_gate` 的读时自愈只救
kind=="draft"。于是批准后实体仍 open → /history 重渲染旧确认卡，老师再点
一次批准 → `_rebuild_pending_from_gate` 从 still-open 行重建 → 同一
assign_adaptive_practice 再跑一次 → 练习布置两遍。
"""
import asyncio
import json

from agent.guard import GuardState


def _run(coro):
    async def go():
        return await coro
    return asyncio.run(go())




def test_boolean_gate_close_after_approve_replay(db_session, monkeypatch, patch_get_db, unit):
    import agent.langgraph_agent_v2 as v2
    from app.services.gate_service import gate_service
    from agent.channel.langgraph_channel import _direct_replay_events

    calls: list[str] = []

    async def fake_assign(student_id: str = ""):
        calls.append(student_id)
        return json.dumps({"ok": True})

    monkeypatch.setitem(v2._TOOL_BY_NAME, "assign_adaptive_practice", fake_assign)

    kwargs = {"student_id": "241914"}
    with unit(db_session):   # 写入口须落在工作单元里（前置一）
        gate_service.open_gate(db_session, conversation_id="conv-close", kind="boolean",
                               tool_name="assign_adaptive_practice", kwargs=kwargs,
                               message="布置练习需要确认")

    # ── 第一回合：老师批准（同进程，内存 pending 命中）──
    gs = GuardState()
    gs.conversation_id = "conv-close"
    # 2.4 收尾：这里原先把待批调用塞进内存 `pending_approval`。字段删了，重放读的是
    # 门那一行（fixture 已经开好了），所以那几行是死代码，删掉。
    _run(_direct_replay_events(gs, True, "conv-close"))
    assert calls == ["241914"], "第一次批准应布置一次"
    # 症状 ①：批准后门实体不得仍 open
    assert gate_service.get_open_gate(db_session, "conv-close") is None, \
        "批准后布尔门实体必须关闭（否则卡片复活）"

    # ── 第二回合：卡片复活，老师再点批准（新 guard，无内存 pending）──
    gs2 = GuardState()
    gs2.conversation_id = "conv-close"
    _run(_direct_replay_events(gs2, True, "conv-close"))
    # 症状 ②（真正的用户损害）：同一练习绝不得布置第二遍
    assert calls == ["241914"], f"双发送：{calls}"

# ── 决定账本：布尔门批准也必须记一行 ─────────────────────────────
# 账本原先写死在 `_gate_decision_stream` 的 `if gate_did` 分支里 —— 只有草稿门记账。
# 布尔门（assign_adaptive_practice 这类）批准后走 `_direct_replay_events`：
# claim → 重放 → finish，全程不写 `agent_gate_decisions`。实测 8 步任务点 3 次批准，
# 账本只有 2 行：「谁批的这次布置练习」查无此人。下面三条钉住写入点本身。

def test_boolean_gate_approve_writes_decision_ledger(db_session, monkeypatch, patch_get_db, unit):
    """批准 + 重放成功 ⇒ 账本恰好一行 approved / boolean，actor 是传进来的老师。

    `gate_id` 走**解析当前开门**那条路（旧客户端不带 gate_id），另一条见下一个用例。
    """
    import agent.langgraph_agent_v2 as v2
    from app.models.database import AgentGateDecision
    from app.services.gate_service import gate_service
    from agent.channel.langgraph_channel import _direct_replay_events

    async def fake_assign(student_id: str = ""):
        return json.dumps({"ok": True})

    monkeypatch.setitem(v2._TOOL_BY_NAME, "assign_adaptive_practice", fake_assign)
    with unit(db_session):
        gid = gate_service.open_gate(
            db_session, conversation_id="conv-ledger", kind="boolean",
            tool_name="assign_adaptive_practice",
            kwargs={"student_id": "241914"}, message="布置练习需要确认")

    gs = GuardState()
    gs.conversation_id = "conv-ledger"
    _run(_direct_replay_events(gs, True, "conv-ledger", actor_id="t-777"))

    rows = db_session.query(AgentGateDecision).all()
    assert len(rows) == 1, f"批准应恰好记一行账，实际 {len(rows)}"
    row = rows[0]
    assert row.decision == "approved"
    assert row.kind == "boolean", "布尔门的账不能记成 draft（决定分流靠它）"
    assert row.gate_id == gid, "账要挂在被批准的那扇门上，否则回放追不回去"
    assert row.actor_user_id == "t-777", "账本的意义就是回答『谁批的』，actor 不许丢"
    assert row.tool_name == "assign_adaptive_practice"


def test_boolean_gate_ledger_uses_submitted_gate_id(db_session, monkeypatch, patch_get_db, unit):
    """客户端带了 gate_id（第 4 个位置参数）：账同样挂这扇门，不挂解析出来的别的门。"""
    import agent.langgraph_agent_v2 as v2
    from app.models.database import AgentGateDecision
    from app.services.gate_service import gate_service
    from agent.channel.langgraph_channel import _direct_replay_events

    async def fake_assign(student_id: str = ""):
        return json.dumps({"ok": True})

    monkeypatch.setitem(v2._TOOL_BY_NAME, "assign_adaptive_practice", fake_assign)
    with unit(db_session):
        gid = gate_service.open_gate(
            db_session, conversation_id="conv-ledger2", kind="boolean",
            tool_name="assign_adaptive_practice",
            kwargs={"student_id": "241915"}, message="布置练习需要确认")

    gs = GuardState()
    gs.conversation_id = "conv-ledger2"
    _run(_direct_replay_events(gs, True, "conv-ledger2", gid, actor_id="t-778"))

    rows = db_session.query(AgentGateDecision).all()
    assert len(rows) == 1
    assert rows[0].gate_id == gid
    assert rows[0].actor_user_id == "t-778"
    assert rows[0].decision == "approved"


def test_boolean_gate_replay_failure_writes_no_decision(db_session, monkeypatch, patch_get_db, unit):
    """重放失败 ⇒ 门退回 open、动作一次都没跑成 ⇒ 不许有 decision。

    记了而动作没跑，比不记更糟：下一次重试会拿着一条假凭据。
    """
    import agent.langgraph_agent_v2 as v2
    from app.models.database import AgentGateDecision
    from app.services.gate_service import gate_service
    from agent.channel.langgraph_channel import _direct_replay_events

    async def boom(student_id: str = ""):
        raise RuntimeError("题库写入失败")

    monkeypatch.setitem(v2._TOOL_BY_NAME, "assign_adaptive_practice", boom)
    with unit(db_session):
        gate_service.open_gate(
            db_session, conversation_id="conv-fail", kind="boolean",
            tool_name="assign_adaptive_practice",
            kwargs={"student_id": "241916"}, message="布置练习需要确认")

    gs = GuardState()
    gs.conversation_id = "conv-fail"
    events, replay = _run(
        _direct_replay_events(gs, True, "conv-fail", actor_id="t-779"))

    assert replay is not None and replay["success"] is False
    assert db_session.query(AgentGateDecision).count() == 0, "动作没跑成，账不能记"
    assert gate_service.get_open_gate(db_session, "conv-fail") is not None, \
        "失败必须退回 open，老师才能重试"

