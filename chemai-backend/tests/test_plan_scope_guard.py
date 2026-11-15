"""C10（§11）：用户没提家长，计划里却凭空多一条家长报告步骤。

真机实测（2026-10-20）：「帮我出一整套高一化学本单元的复习资料」→ 会话末尾调了
generate_parent_report(241901)。PLAN_PROMPT 把「提到家长就必须包含家长步骤」写得很
硬，却没有反向约束；模型对否定式规则记不牢。所以在 `drop_offscope_parent_steps` 里
确定性摘掉，而不是再求一次提示词。
"""
from agent.planner import (
    Plan, PlanGenerator, drop_offscope_parent_steps,
)


def _plan(*skills):
    return Plan(goal="g", steps=[
        __import__("agent.planner", fromlist=["PlanStep"]).PlanStep(
            step=i, skill=s, args={}, depends_on=[i - 1] if i > 1 else [])
        for i, s in enumerate(skills, 1)])


def test_parent_steps_dropped_when_goal_says_nothing_about_parents():
    plan = _plan("generate_questions", "generate_parent_report", "send_report_to_parent")
    kept = drop_offscope_parent_steps(plan, "出一整套本单元复习资料")
    assert [s.skill for s in kept.steps] == ["generate_questions"]
    assert kept.steps[0].step == 1
    assert kept.steps[0].depends_on == [], "被摘掉的前序步不能留成悬空依赖"


def test_renumbering_remaps_dependencies():
    plan = _plan("diagnose_barrier", "generate_parent_report", "generate_questions")
    kept = drop_offscope_parent_steps(plan, "按薄弱点出 10 道题")
    assert [s.skill for s in kept.steps] == ["diagnose_barrier", "generate_questions"]
    assert [s.step for s in kept.steps] == [1, 2]
    # 第三步原本 depends_on=[2]（被摘掉的家长步）→ 悬空依赖清掉，不指向别的东西
    assert kept.steps[1].depends_on == []


def test_parent_steps_kept_when_goal_mentions_parents():
    plan = _plan("generate_parent_report", "send_report_to_parent")
    for goal in ("把这份报告同步给家长", "生成家长报告", "告知家长这次的情况"):
        assert len(drop_offscope_parent_steps(plan, goal).steps) == 2


def test_untouched_plans_are_returned_as_is():
    plan = _plan("generate_questions", "save_to_bank")
    assert drop_offscope_parent_steps(plan, "出 10 道题存题库") is plan


def test_generate_applies_the_filter(monkeypatch):
    """接在 generate() 上，而不是只有调用方记得调。"""
    import asyncio

    payload = (
        '{"goal":"出整套复习资料","steps":['
        '{"step":1,"skill":"generate_questions","args":{},"depends_on":[]},'
        '{"step":2,"skill":"generate_parent_report","args":{"student_id":"241901"},"depends_on":[1]}'
        "]}"
    )

    class _P:
        def __init__(self, *a, **kw):
            pass

        async def chat(self, **kw):
            class _R:
                content = payload
            return _R()

    plan = asyncio.run(PlanGenerator(_P()).generate(
        "出一整套本单元复习资料", ["generate_questions", "generate_parent_report"]))
    assert [s.skill for s in plan.steps] == ["generate_questions"]