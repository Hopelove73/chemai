"""步骤产物落库（C2：上下文依赖 → 实体依赖）。

问题：`${step_N.field}` 跨步引用的是**对话历史里的一条消息**，而
`trim_context`（agent/context_manager.py:20-73）超过 30 条消息就只留最近 6 条
+ ≤10 条关键词命中，其余压成 ≤200 字摘要。第 5 步想引用第 1 步的产物时，
它已经不在模型视野里了 —— 只剩 plan_runs 里那串状态。

本文件钉三件事（下面的用例**一条对话消息都不构造**，这正是「上下文里已经没有
那条消息」的情形：解析只看库）：
  1. 产物落库 + 读回；
  2. depends_on 引用能从库解析成真实参数；
  3. 解析不到时优雅降级 —— 丢参数、留痕、绝不抛异常炸掉整轮。
"""
import json
import logging

import pytest

import app.models.database as db_mod
from app.models.database import Base
from app.services.plan_run_service import (
    extract_step_artifact,
    plan_run_service,
    record_transition_artifacts,
)
from agent.planner import PLAN_PROMPT, referenced_steps, resolve_args


_PLAN = [
    {"step": 1, "skill": "generate_questions", "description": "按薄弱点出题",
     "args": {"topic": "氧化还原"}, "depends_on": []},
    {"step": 2, "skill": "generate_learning_plan", "description": "据题目生成复习计划",
     "args": {"question_ids": "${step_1.question_ids}", "class_name": "高一(3)班"},
     "depends_on": [1]},
]


def _start(db, cid="conv-c2", steps=None):
    return plan_run_service.start_run(db, cid, "出本单元整套复习资料", steps or _PLAN)


# ── ① 产物落库 + 读回 ────────────────────────────────────────────────

def test_artifact_written_and_read_back(db_session):
    _start(db_session)
    plan_run_service.record_step_artifact(
        db_session, "conv-c2", 1,
        {"question_ids": ["q1", "q2"], "total": 2})

    arts = plan_run_service.get_step_artifacts(db_session, "conv-c2")
    assert arts == {1: {"question_ids": ["q1", "q2"], "total": 2}}


def test_artifact_upsert_is_idempotent(db_session):
    """同一步重复落库只更新那一行 —— 否则重试/续跑会给同一步留下一串历史版本。"""
    _start(db_session)
    plan_run_service.record_step_artifact(db_session, "conv-c2", 1, {"v": 1})
    plan_run_service.record_step_artifact(db_session, "conv-c2", 1, {"v": 2})

    from app.models.database import PlanStepArtifact
    rows = db_session.query(PlanStepArtifact).all()
    assert len(rows) == 1 and rows[0].artifact == {"v": 2}


def test_no_active_run_writes_nothing(db_session):
    """没有活跃 run 时静默 no-op（与 mark_step 同口径）。"""
    plan_run_service.record_step_artifact(db_session, "conv-none", 1, {"v": 1})
    assert plan_run_service.get_step_artifacts(db_session, "conv-none") == {}


# ── ② depends_on 引用从库解析（上下文里早已没有那条消息）────────────────

def test_depends_on_ref_resolved_from_db(db_session):
    _start(db_session)
    plan_run_service.mark_step(db_session, "conv-c2", 1, "completed")
    plan_run_service.record_step_artifact(
        db_session, "conv-c2", 1, {"question_ids": ["q1", "q2", "q3"]})

    # 这里没有任何 messages / 对话历史：第 1 步的那条输出被 trim_context 挤掉也无所谓
    args = plan_run_service.resolve_tool_args(
        db_session, "conv-c2", "generate_learning_plan")

    assert args == {"question_ids": "q1,q2,q3", "class_name": "高一(3)班"}


def test_model_copied_template_is_resolved_too(db_session):
    """模型把模板原样抄进调用参数（提示词就这么教它写的）时也要解析。"""
    _start(db_session)
    plan_run_service.record_step_artifact(
        db_session, "conv-c2", 1, {"question_ids": ["q7"]})

    args = plan_run_service.resolve_tool_args(
        db_session, "conv-c2", "generate_learning_plan",
        {"question_ids": "${step_1.question_ids}"})

    assert args["question_ids"] == "q7"


def test_model_supplied_value_wins_over_plan_default(db_session):
    """模型这一轮给了值就以它为准（可能刚按老师的补充改了主意）。"""
    _start(db_session)
    plan_run_service.record_step_artifact(
        db_session, "conv-c2", 1, {"question_ids": ["q1"]})

    args = plan_run_service.resolve_tool_args(
        db_session, "conv-c2", "generate_learning_plan",
        {"class_name": "高二(1)班"})

    assert args["class_name"] == "高二(1)班"
    assert args["question_ids"] == "q1"


def test_superseded_run_artifacts_do_not_leak(db_session):
    """新 plan_task 作废旧 run：旧产物不得漏进新一轮的参数表。"""
    _start(db_session)
    plan_run_service.record_step_artifact(db_session, "conv-c2", 1,
                                          {"question_ids": ["old"]})
    _start(db_session, steps=_PLAN)  # 同一会话再出一版计划 → 旧 run superseded

    args = plan_run_service.resolve_tool_args(
        db_session, "conv-c2", "generate_learning_plan")
    assert "question_ids" not in args, "旧 run 的产物漏进了新 run"
    assert args == {"class_name": "高一(3)班"}


# ── ③ 解析不到时优雅降级 ────────────────────────────────────────────

def test_missing_artifact_drops_arg_and_traces(db_session, caplog):
    """产物没落库（例如那一次工具调用失败）→ 丢参数、留痕，不抛、不留占位符。"""
    _start(db_session)
    with caplog.at_level(logging.WARNING, logger="app.services.plan_run_service"):
        args = plan_run_service.resolve_tool_args(
            db_session, "conv-c2", "generate_learning_plan")

    assert args == {"class_name": "高一(3)班"}, "解析不到的引用必须整条丢掉"
    assert any("unresolved" in r.getMessage() for r in caplog.records), "降级必须留痕"


def test_db_failure_does_not_raise(db_session):
    """查库炸了只降级成「没有产物」—— 一次库抖动不能让整轮对话崩掉。"""
    class _Boom:
        def query(self, *a, **k):
            raise RuntimeError("database is locked")

    args = plan_run_service.resolve_tool_args(
        _Boom(), "conv-c2", "generate_learning_plan", {"class_name": "高一(3)班"})
    assert args == {"class_name": "高一(3)班"}


def test_no_active_run_returns_given_kwargs(db_session):
    assert plan_run_service.resolve_tool_args(
        db_session, "conv-none", "generate_learning_plan",
        {"class_name": "高一(3)班"}) == {"class_name": "高一(3)班"}


# ── 纯函数：解析规则 ────────────────────────────────────────────────

def test_resolve_args_joins_list_and_scalar():
    resolved, missing = resolve_args(
        {"a": "${step_1.ids}", "b": "$x-", "c": 3},
        {1: {"ids": ["q1", "q2"]}})
    assert resolved == {"a": "q1,q2", "b": "$x-", "c": 3}
    assert missing == []


def test_resolve_args_drops_partially_resolvable_value():
    """一个 value 里两个引用、缺一个 → 整条丢掉：半截拼出来的参数比没有更坏。"""
    resolved, missing = resolve_args(
        {"k": "${step_1.a}-${step_2.b}"},
        {1: {"a": "X"}})
    assert resolved == {}
    assert missing == [(2, "b")]


def test_referenced_steps():
    assert referenced_steps({"a": "${step_1.x}", "b": ["${step_3.y}"], "c": 2}) == {1}


def test_empty_artifact_field_not_resolved():
    """产物里同名字段是空串 → 视同没有（空串灌进工具就是"用户说了空值"）。"""
    resolved, missing = resolve_args({"k": "${step_1.a}"}, {1: {"a": ""}})
    assert resolved == {} and missing == [(1, "a")]


def test_prompt_skeleton_is_valid_json_with_single_braces():
    """B2：PLAN_PROMPT 是给模型照抄的 JSON 骨架，必须本身就是合法 JSON。

    它按 `.format()` 的写法把花括号转义成了 `{{`/`}}`，可代码从头到尾只
    `.replace()`、从不 format —— 模型看到的就是一份坏骨架（实测 `{{`×5），
    照抄回来 `_parse` 抛错 → 空计划 → 单步兜底把参数换成 `{"keyword": ...}`
    → 工具报错 → 老师先看到一张「失败」红卡，重试才成功。
    """
    prompt = (PLAN_PROMPT.replace("{skills}", "generate_learning_plan")
              .replace("{students}", "241914 = 陈湘旻")
              .replace("{goal}", "给陈湘旻做一份学习计划"))
    assert "{{" not in prompt and "}}" not in prompt, "骨架里还留着 format 转义"
    skeleton = prompt.split("返回 JSON", 1)[1].split("规则", 1)[0]
    data = json.loads(skeleton[skeleton.index("{"):skeleton.rindex("}") + 1])
    assert data["steps"][0]["skill"] == "技能名"
    assert "${step_N.field}" in prompt and "${{" not in prompt, \
        "跨步引用示例必须是单花括号，否则 _REF_RE 认不出来"


def test_doubled_brace_reference_still_resolved():
    """模型若仍按旧模板输出 `${{step_1.x}}`，也不能把占位符原样灌进工具参数。"""
    resolved, missing = resolve_args({"k": "${{step_1.ids}}"}, {1: {"ids": ["a", "b"]}})
    assert resolved == {"k": "a,b"} and missing == []
    resolved, missing = resolve_args({"k": "${{step_1.ids}}"}, {1: {}})
    assert resolved == {} and missing == [(1, "ids")]


# ── 落库入口（调用面一行即完成，见报告里的 channel/适配器待改点）──────────

def test_record_transition_artifacts_only_for_completed(patch_get_db):
    db = patch_get_db
    _start(db, "conv-hook")
    plan_run_service.mark_step(db, "conv-hook", 1, "completed")

    record_transition_artifacts(
        "conv-hook",
        [(1, "completed"), (2, "awaiting_confirmation")],
        {"question_ids": ["q1"], "_route": {"path": "/x"}},
    )

    arts = plan_run_service.get_step_artifacts(db, "conv-hook")
    assert arts == {1: {"question_ids": ["q1"]}}, "只落 completed 的步，且滤掉 UI 字段"


def test_record_transition_artifacts_ignores_non_dict_result(patch_get_db):
    db = patch_get_db
    _start(db, "conv-hook2")
    record_transition_artifacts("conv-hook2", [(1, "completed")], "纯文本结果")
    assert plan_run_service.get_step_artifacts(db, "conv-hook2") == {}


def test_extract_step_artifact_filters_underscore_keys():
    assert extract_step_artifact({"a": 1, "_route": {"p": "/"}, "_reminder": "x"}) == {"a": 1}
    assert extract_step_artifact(["not", "a", "dict"]) == {}
