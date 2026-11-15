"""收尾对账：计划里没执行的步骤不能永远停在 pending（C 类静默截断的根因）。

2026-10 实测：「帮我准备一次月考」计划 5 步，只执行到第 3 步就收尾，
后端既不补完成事件也不提示，用户看到的是「诊断完就没了」。
"""
from app.services.plan_run_service import compute_tail_transition


def test_leftover_steps_become_cancelled():
    steps = [
        {"step": 1, "skill": "show_students", "status": "completed"},
        {"step": 2, "skill": "diagnose_barrier", "status": "completed"},
        {"step": 3, "skill": "show_diagnosis", "status": "completed"},
        {"step": 4, "skill": "generate_questions", "status": "pending"},
        {"step": 5, "skill": "generate_learning_plan", "status": "pending"},
    ]
    assert compute_tail_transition(steps) == [(4, "cancelled"), (5, "cancelled")]
    assert [s["status"] for s in steps] == [
        "completed", "completed", "completed", "cancelled", "cancelled"]


def test_running_step_also_closed():
    steps = [{"step": 1, "skill": "generate_questions", "status": "running"}]
    assert compute_tail_transition(steps) == [(1, "cancelled")]


def test_awaiting_confirmation_untouched():
    # 门挂着的步不算「没执行」——它还等着人点确认卡
    steps = [{"step": 1, "skill": "assign_adaptive_practice", "status": "awaiting_confirmation"}]
    assert compute_tail_transition(steps) == []
    assert steps[0]["status"] == "awaiting_confirmation"


def test_all_done_no_transition():
    steps = [{"step": 1, "skill": "x", "status": "completed"},
             {"step": 2, "skill": "y", "status": "failed"}]
    assert compute_tail_transition(steps) == []


def test_empty_steps():
    assert compute_tail_transition([]) == []

# ── 异常中止路径（F5）：running→failed、pending→cancelled ──
from app.services.plan_run_service import compute_abort_transition


def test_abort_marks_running_failed_and_pending_cancelled():
    steps = [
        {"step": 1, "skill": "show_students", "status": "completed"},
        {"step": 2, "skill": "diagnose_barrier", "status": "completed"},
        {"step": 3, "skill": "show_diagnosis", "status": "running"},
        {"step": 4, "skill": "generate_questions", "status": "pending"},
        {"step": 5, "skill": "generate_learning_plan", "status": "pending"},
    ]
    assert compute_abort_transition(steps) == [(3, "failed"), (4, "cancelled"), (5, "cancelled")]
    assert [s["status"] for s in steps] == ["completed", "completed", "failed", "cancelled", "cancelled"]


def test_abort_does_not_touch_awaiting_confirmation():
    # 门还开着 → 人补一个决定就能继续，标掉等于替老师放弃
    steps = [{"step": 5, "skill": "generate_learning_plan", "status": "awaiting_confirmation"}]
    assert compute_abort_transition(steps) == []
    assert steps[0]["status"] == "awaiting_confirmation"


def test_abort_empty_and_all_terminal():
    assert compute_abort_transition([]) == []
    assert compute_abort_transition([{"step": 1, "skill": "x", "status": "completed"}]) == []


def test_abort_events_shape_and_idempotent():
    """异常兜底产出的必须是 plan_progress 帧，且第二次调用是空（幂等）。"""
    from agent.channel.langgraph_channel import _plan_abort_events
    import json as _json

    class G:
        plan_steps = [
            {"step": 1, "skill": "a", "status": "completed"},
            {"step": 2, "skill": "b", "status": "running"},
            {"step": 3, "skill": "c", "status": "pending"},
        ]

    evs = _plan_abort_events(G(), "")
    assert len(evs) == 2
    parsed = [_json.loads(e) for e in evs]
    assert all(p["type"] == "plan_progress" for p in parsed)
    assert [(p["current_step"], p["status"]) for p in parsed] == [(2, "failed"), (3, "cancelled")]
    assert _plan_abort_events(G(), "") == []
