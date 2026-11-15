"""门开出即收尾（2026-10 真机）：确认门打开后本轮必须停下，而不是继续往下一步撞。

实况：周翔那条链上，第 4 步的门开后模型仍按计划调第 5~8 步，四条全是
`waiting_for_gate`。计划步状态机不因此前进（`compute_tool_transition` 对
`gate_open_blocked` 直接 return），但「回合没停」在老师眼里就是「还在往下推进」。

停的地方（D+ 切片 2 更新）：`create_agent` 时代不再包模型，短路移到中间件层
——`GateTurnEndMiddleware.wrap_model_call`：门一开，下一次模型调用不打 LLM，
直接回一条不含 tool_calls 的收尾消息，ReAct 循环自然结束。
中间件本体的钉在 `tests/test_gate_turn_end_middleware.py`；本文件保留
GuardState 侧语义（begin_turn 复位、被拦即置 paused）。
"""
import asyncio
import json

from agent.guard import GuardState, _make_guarded_tool


def test_begin_turn_clears_the_pause():
    gs = GuardState()
    gs.paused = True
    gs.begin_turn()
    assert gs.paused is False, "老师决定之后的下一轮必须能继续干活"


def test_waiting_out_marks_the_turn_paused(db_session, patch_get_db):
    """被已开的门拦下 → 本轮收尾（这是第 5~8 步那四条的形态）。"""
    from app.services.gate_service import gate_service
    from app.services.work_unit import work_unit

    cid = "t-pause-waiting"
    with work_unit(db_session):
        gate_service.open_gate(db_session, cid, kind="boolean",
                               tool_name="delete_bank", kwargs={"set_id": "b1"},
                               message="删题库待确认")

    async def delete_bank(set_id: str = ""):
        """假破坏性工具（真工具名：guard 按名字判定门）。"""
        return json.dumps({"ok": True})

    gs = GuardState()
    gs.conversation_id = cid
    guarded = _make_guarded_tool(delete_bank, gs, requires_approval=True)
    out = json.loads(asyncio.run(guarded.ainvoke({"set_id": "b1"})))

    assert out.get("error") == "waiting_for_gate", out
    assert gs.paused is True, "被门拦下后本轮必须收尾，否则模型会继续撞下一步"
