"""T4：「计划停在确认门上时不许收尾对账」这道栅栏的测试。

`_plan_tail_events` 会把「没执行的步」标成 cancelled 并明说哪几步没做 —— 但它必须先
判断本轮到底是「没做完」还是「暂停中」。判错的形态是实打实的线上事故（2026-10 三端
联动）：首轮流在 step4 等确认处结束，对账把 step5/6/7 全标 cancelled；老师随后批准，
三步都真的执行了，计划卡却写着「已放弃」——上面「已送达 2 位家长」，下面「已放弃」。

纯函数 `compute_tail_transition` 另有测试（tests/test_plan_run_tail.py）；这里钉的是
**调用前那道闸**：有步在等门 ⇒ 一个事件都不发，且一步都不许被改写。
"""
from agent.channel.langgraph_channel import _plan_tail_events
from agent.guard import GuardState


def _guard(steps):
    g = GuardState()
    g.plan_steps = steps
    return g


def test_gate_waiting_suppresses_reconciliation_entirely():
    """有步在等确认 ⇒ 对账整个跳过，且不碰任何步状态。"""
    g = _guard([
        {"step": 1, "skill": "generate_learning_plan", "status": "completed"},
        {"step": 2, "skill": "send_learning_plan", "status": "awaiting_confirmation"},
        {"step": 3, "skill": "generate_parent_report", "status": "pending"},
        {"step": 4, "skill": "send_report_to_parent", "status": "pending"},
    ])
    assert _plan_tail_events(g, "conv-gate") == []
    # 关键：待执行的步不能被就地标成 cancelled（否则批准后卡片自相矛盾）
    assert [s["status"] for s in g.plan_steps] == [
        "completed", "awaiting_confirmation", "pending", "pending",
    ]


def test_no_leftover_emits_nothing():
    """全跑完了 ⇒ 无话可说（防的是"每轮都补一句本轮有 0 步没执行"）。"""
    g = _guard([
        {"step": 1, "skill": "generate_learning_plan", "status": "completed"},
        {"step": 2, "skill": "send_learning_plan", "status": "completed"},
    ])
    assert _plan_tail_events(g, "conv-done") == []


def test_no_plan_emits_nothing():
    """没有计划 ⇒ 对账不适用（普通对话不该冒出一句"哪几步没做"）。"""
    assert _plan_tail_events(GuardState(), "conv-plain") == []
