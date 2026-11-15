"""确认门死锁（2026-10 真机）：replay-send 工具在「没有待确认草稿」时另开布尔门。

实况（zhou 账号，周翔 7403ddb776174c7d）：计划门已 executed，模型又调
send_report_to_parent → guard 按 `else` 分支登记了一扇 `g-tool-send_report_to_parent-`
布尔门，action 为空。这扇门算「有一张待确认卡」→ 后面每一次写操作都被 `waiting_for_gate`
挡住；而卡上的发送按钮在流式期间是禁用的 → 老师点不了 → 门关不掉 → 越重试越死。

正确形态：replay-send 工具的许可只来自草稿门；没有草稿门就不建门，回一句可继续的话。
"""
import asyncio
import json

from agent.guard import GuardState, _make_guarded_tool
from app.services.gate_service import gate_service


def _run(coro):
    async def go():
        return await coro
    return asyncio.run(go())


def _send_returning_approval_needed():
    async def send_report_to_parent(student_id: str = "", draft_id: str = ""):
        """真工具名（guard 按名字判定 replay-send）。没有草稿时这是它的真实返回。"""
        return json.dumps({"approval_needed": True,
                           "message": "即将把学习报告同步给家长，但没有待确认的报告草稿（请先生成）",
                           "target": student_id, "action": "send_parent",
                           "student_id": student_id}, ensure_ascii=False)
    return send_report_to_parent


def test_send_tool_without_draft_gate_does_not_open_a_boolean_gate(db_session, patch_get_db):
    cid = "t-send-nodraft"
    gs = GuardState()
    gs.conversation_id = cid
    guarded = _make_guarded_tool(_send_returning_approval_needed(), gs)

    out = json.loads(_run(guarded.ainvoke({"student_id": "241901"})))

    assert out.get("error") == "no_draft_to_send", out
    assert "不要重复调用" in out.get("llm_brief", ""), out
    assert gate_service.get_open_gate(db_session, cid) is None, (
        "不得为 replay-send 工具另开布尔门——这扇门会把老师钉死在流式里"
    )


def test_send_tool_defers_when_a_gate_is_already_open(db_session, patch_get_db):
    """已有门时仍是「先等」（旧行为不变），只是不再新建第二扇。"""
    from app.services.work_unit import work_unit

    cid = "t-send-hasgate"
    with work_unit(db_session):
        gate_service.open_gate(db_session, cid, kind="boolean",
                               tool_name="delete_bank", kwargs={"set_id": "b1"},
                               message="删题库待确认")

    gs = GuardState()
    gs.conversation_id = cid
    guarded = _make_guarded_tool(_send_returning_approval_needed(), gs)
    out = json.loads(_run(guarded.ainvoke({"student_id": "241901"})))

    assert out.get("error") == "waiting_for_gate", out
    og = gate_service.get_open_gate(db_session, cid)
    assert og["tool_name"] == "delete_bank", "原有那扇门不能被换成发送门"