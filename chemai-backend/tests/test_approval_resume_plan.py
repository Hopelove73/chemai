"""审批门 / 计划执行 的确定性回归（2026-09-14「复杂任务拆解执行」E2E 修出来的）。

背景：计划任务跑到「布置练习」这类步骤时，护栏会拦下并弹确认门。老师点「批准」
之后任务却断在那里 —— 靠 LLM 复跑才能发现问题，而且每次路径还不一样。
这里把两处**真缺陷**钉成确定性断言，与 LLM 无关：

1. `_resolve_resume_intent`：布尔门（assign_adaptive_practice 等 requires_approval
   工具）点「批准」曾被改写成 `"cancelled"` —— 下游确定性重放判定为未批准，
   整个任务停在「等待确认」，**批准等于没批**。
2. `guard._guarded`：被 `requires_approval` 拦下时**没有登记待重放**（只有
   `approval_needed` 那条路登记了）→ 批准后没有东西可重放，链路断。
"""
import asyncio
import json

from agent.guard import GuardState, _make_guarded_tool


def _run(coro):
    async def go():
        return await coro

    return asyncio.run(go())


# ── 1. resume 意图解析 ──────────────────────────────────────────────

def _intent(message: str, pending_name: str):
    from agent.channel.gateway import ChatRequest
    from app.services.decision_intake import resume_intent as _resolve_resume_intent
    req = ChatRequest(message=message, conversation_id="t-x")
    return _resolve_resume_intent(req, pending_name)


def test_boolean_gate_approval_survives_as_approved():
    """布尔门点「批准」→ user_response 必须是 "approved"（曾被改成 cancelled）。"""
    decision, response = _intent("approved", "assign_adaptive_practice")
    assert response == "approved", f"批准被改写成了 {response!r} —— 任务会断在等待确认"
    assert decision is None, "布尔门没有修订原语，三值决定应为 None（走 legacy）"


def test_boolean_gate_cancel_stays_cancelled():
    _, response = _intent("cancelled", "assign_adaptive_practice")
    assert response == "cancelled"


def test_boolean_gate_feedback_is_not_an_approval():
    """布尔门收到修改意见（非确认词）→ 不是批准。"""
    _, response = _intent("把题量减到 3 道", "assign_adaptive_practice")
    assert response == "把题量减到 3 道", "非确认词应原样透传，不能当成批准"


def test_draft_gate_keeps_three_way_decision():
    """草稿门（发送类）仍保留三值决定 —— 修复不能把这条路压平。"""
    from app.services.decision_intake import GATE_PLAN_TOOLS as _GATE_PLAN_TOOLS
    names = sorted(_GATE_PLAN_TOOLS)
    assert names, "草稿门工具集不该为空"
    decision, response = _intent("approved", names[0])
    assert decision["action"] == "approve"
    assert decision["draft_id"] == "" and decision["gate_id"] == ""
    assert response == "approved"

    decision2, response2 = _intent("再改改第三周", names[0])
    assert decision2 is None, "非协议词的自由文本不是三值决定"
    assert response2 == "再改改第三周"


# ── 2. 护栏拦下时必须登记待重放 ──────────────────────────────────────

def test_guard_registers_a_gate_when_blocked_by_requires_approval(db_session, patch_get_db):
    """被 requires_approval 拦下的调用要**登记成门实体** —— 否则批准后无事可做。

    2026-10-17（gate-lifecycle 2.4 收尾）：原先断言"内存里登记了 pending"，那个
    字段删了；载荷的真源本来就是门那一行（ADR-0030）。顺带把工具换成真名
    （`delete_bank`）—— 2.5 之后门开得出来才给开，假工具名会被拒。
    """
    from app.services.gate_service import gate_service

    cid = "t-register"
    calls = []

    async def delete_bank(set_id: str = ""):
        """Fake destructive tool（真工具名）。"""
        calls.append(set_id)
        return json.dumps({"ok": True})

    gs = GuardState()
    gs.conversation_id = cid
    guarded = _make_guarded_tool(delete_bank, gs, requires_approval=True)
    out = json.loads(_run(guarded.ainvoke({"set_id": "b1"})))

    assert out.get("approval_needed") is True, "布尔门拦下返回门卡载荷（ADR-0015 阶段 2）"
    assert out.get("gate_id", "").startswith("g-tool-delete_bank-"), out
    assert calls == [], "未批准时工具不该被执行"
    og = gate_service.get_open_gate(db_session, cid)
    assert og is not None, "拦下时必须登记门实体，否则批准后没有东西可重放（任务断链）"
    assert og["tool_name"] == "delete_bank"
    assert og["kwargs"] == {"set_id": "b1"}


def test_guard_replays_after_approval(db_session, patch_get_db):
    """批准后的执行**只走重放路径**（ADR-0015：不再靠"approved 置位 + 重调工具"）。

    2.4 收尾改写：重放目标从门实体来，所以要有会话 + 门行；执行体用真 `delete_bank`
    （真题库，看得见效果），断言从"假函数被调了几次"换成"题库真的没了"。
    """
    from agent.channel.langgraph_channel import _direct_replay_events
    from app.services.question_bank import question_bank_service

    cid = "t-replay-after-approval"
    sid = question_bank_service.create_set(db_session, name="待删题库", teacher_id="t1")["set_id"]

    async def delete_bank(set_id: str = ""):
        """Fake destructive tool（真工具名）。"""
        return json.dumps({"ok": True})

    gs = GuardState()
    gs.conversation_id = cid
    guarded = _make_guarded_tool(delete_bank, gs, requires_approval=True)
    _run(guarded.ainvoke({"set_id": sid}))

    # 门开着时即使 approved 置位也拦（防未决门被绕过）。
    # 这里换一个**别的**工具来试：`delete_bank` 的调用上限是 1 次，同工具再调会先撞
    # 配额（那是另一道关，2.10 管的是"未执行不占配额"，不是本用例的靶子）。
    # C1-Q3 拦的是**一切**工具，所以用 `delete_bank` 之外的任意工具验都成立。
    async def other_tool(x: str = ""):
        """Any other tool."""
        return json.dumps({"ok": True})

    gs.approved = True
    other = _make_guarded_tool(other_tool, gs)
    out = json.loads(_run(other.ainvoke({"x": "1"})))
    assert out.get("gate_open_blocked") is True, out

    # 批准后的真实执行路径：channel 的 `_direct_replay_events`（从门实体取目标）
    _, info = _run(_direct_replay_events(gs, True, cid))
    assert info is not None and info.get("success") is True
    assert question_bank_service.get_set(db_session, sid) is None, "批准后必须真的执行"
