"""D1：计划依赖序未强制（§11）。

现场证据（2026-10-20 演示 run 0fce5093）：第 5 步 `generate_questions` 被标
`cancelled`，而依赖它的第 6 步 `assign_adaptive_practice` 却 `completed` ——
卡片上就是「出题被放弃、练习却布置了」。此前只有**断言**（`compute_tool_transition`
对不匹配的工具不转移），执行侧从不拦：一个还没做的前置步不妨碍后面那步直接跑。

设计边界（不做成全锁）：
  - 依赖步**还没终态**（pending/running/awaiting_confirmation）→ 拦住，让模型先做前面那步；
  - 依赖步**已终态但不是 completed**（cancelled/failed/skipped）→ 放行，只留痕。
    老师的计划被放弃一步后，后面的事不该永远做不了 —— 那正是死锁。
"""
import asyncio
import json

from agent.guard import GuardState, _make_guarded_tool, plan_dep_block

STEPS = [
    {"step": 1, "skill": "diagnose_barrier", "status": "completed", "depends_on": []},
    {"step": 2, "skill": "generate_questions", "status": "pending", "depends_on": [1]},
    {"step": 3, "skill": "assign_adaptive_practice", "status": "pending", "depends_on": [2]},
    {"step": 4, "skill": "send_learning_plan", "status": "completed", "depends_on": []},
]


def _guard(steps=None):
    gs = GuardState()
    gs.plan_steps = [dict(s) for s in (steps if steps is not None else STEPS)]
    return gs


def test_blocked_when_dependency_still_pending():
    """第 3 步依赖的第 2 步还没做 → 拦住，并说清是哪一步。"""
    block = plan_dep_block(_guard(), "assign_adaptive_practice")
    assert block is not None
    assert block["blocked_by"] == [2]
    assert "第 2 步" in block["llm_brief"]


def test_allowed_when_dependency_is_terminal_but_cancelled():
    """依赖已终态（哪怕是被放弃）→ 放行：不把放弃变成永久死锁。"""
    steps = [dict(s) for s in STEPS]
    steps[1]["status"] = "cancelled"
    assert plan_dep_block(_guard(steps), "assign_adaptive_practice") is None


def test_allowed_when_step_not_in_plan():
    """不在计划里的工具（闲聊/搜索/读文件）完全不受影响。"""
    assert plan_dep_block(_guard(), "web_search") is None


def test_no_plan_means_no_interference():
    assert plan_dep_block(GuardState(), "assign_adaptive_practice") is None


def test_dependency_on_missing_step_does_not_block():
    """依赖指向一个计划里不存在的步（计划被裁过）→ 不拦，避免无从满足的等待。"""
    steps = [{"step": 9, "skill": "assign_adaptive_practice", "status": "pending",
              "depends_on": [42]}]
    assert plan_dep_block(_guard(steps), "assign_adaptive_practice") is None


def test_guard_refuses_the_call_and_leaves_a_trace():
    """走真包装：被依赖序拦下 → 工具未执行 + 可恢复错误（不在配额里留痕）。"""
    calls = []

    async def assign_adaptive_practice(student_id: str = ""):
        """假工具（真名：按名字对到计划步）。"""
        calls.append(student_id)
        return json.dumps({"ok": True})

    gs = _guard()
    guarded = _make_guarded_tool(assign_adaptive_practice, gs)
    out = json.loads(asyncio.run(guarded.ainvoke({"student_id": "241901"})))

    assert calls == [], "前置步没做就不该执行"
    assert out.get("error") == "plan_dependency_unmet", out
    assert out.get("blocked_by") == [2]
    assert out.get("recoverable") is True, "做对前置步之后要能重来，不能被当作失败卡住"
    assert gs.call_counts.get("assign_adaptive_practice", 0) == 0, "没执行不该占调用配额"