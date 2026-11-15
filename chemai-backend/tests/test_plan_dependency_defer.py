"""D1 暂缓（plan_dependency_unmet）不是失败 —— 8 步长链实测抓出的状态机 bug。

实测形状（t-1792961685976）：模型在续跑轮里抢跑第 8 步 generate_parent_report，
此时第 7 步（布置练习）还挂在 HITL 门上等批准。D1 依赖守卫正确暂缓
（返回 error=plan_dependency_unmet, recoverable=True），但 compute_tool_transition
把它当普通 error 把第 8 步标成 **failed（终态）**——老师批准第 7 步后模型重试
成功、家长报告真的发出去了，计划卡却永远显示「第 8 步 ✕ 失败」，
「任务已全部完成」也到不了 8/8。

暂缓 = 「现在不做、依赖完成后再来」，步骤该留在 pending，重试成功照常打勾。
与 no_draft_to_send / gate_open_blocked 同一族的「非失败」特判（2026-10 各有实测）。
"""
from app.services.plan_run_service import compute_tool_transition


def _steps8():
    """实测形状：8 步计划，1-6 完成、第 7 步挂 HITL 门（等待确认）、第 8 步 pending。"""
    return [
        {"step": 1, "skill": "query_assignment_progress", "status": "completed"},
        {"step": 2, "skill": "memory_student_get", "status": "completed"},
        {"step": 3, "skill": "diagnose_barrier", "status": "completed"},
        {"step": 4, "skill": "show_diagnosis", "status": "completed"},
        {"step": 5, "skill": "generate_learning_plan", "status": "completed"},
        {"step": 6, "skill": "send_learning_plan", "status": "completed"},
        {"step": 7, "skill": "assign_adaptive_practice", "status": "awaiting_confirmation"},
        {"step": 8, "skill": "generate_parent_report", "status": "pending"},
    ]


DEFERRED = {"error": "plan_dependency_unmet",
            "llm_brief": "计划里第 7 步（assign_adaptive_practice）还没做完，"
                         "本次调用没有执行。",
            "blocked_by": [7], "recoverable": True}


def test_dependency_defer_is_not_failure():
    """暂缓后第 8 步必须还是 pending —— 不是 failed。"""
    steps = _steps8()
    transitions = compute_tool_transition(steps, "generate_parent_report", DEFERRED)
    assert steps[7]["status"] == "pending", \
        "暂缓=稍后再来，不是失败；标 failed 会让重试成功也翻不回「任务全部完成」"
    assert transitions == []


def test_deferred_step_completes_on_real_success():
    """暂缓 → 重试成功（生成即门 approval_needed）→ awaiting_confirmation 正常挂上门。"""
    steps = _steps8()
    compute_tool_transition(steps, "generate_parent_report", DEFERRED)
    # 第 7 步批准执行完，模型重试第 8 步：生成成功、门开（生成即门）
    t2 = compute_tool_transition(steps, "generate_parent_report",
                                 {"approval_needed": True, "draft_id": "d8"})
    assert steps[7]["status"] == "awaiting_confirmation"
    assert (8, "awaiting_confirmation") in t2


def test_deferred_step_survives_multiple_defers():
    """连续两次抢跑两次暂缓，步骤仍是 pending（不是「第二次就死」）。"""
    steps = _steps8()
    compute_tool_transition(steps, "generate_parent_report", DEFERRED)
    compute_tool_transition(steps, "generate_parent_report", DEFERRED)
    assert steps[7]["status"] == "pending"
