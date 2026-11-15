"""计划确认闭环 service 接缝测试（spec: GitHub issue #9 / ADR-0013）。

覆盖 PlanDraftService 生命周期（生成/版本链/三值决定/数据强制门/发送效果/幂等通知/
Data Scope）与 PlanRunService 流转（作废/步状态机/跨回合恢复）。LLM 一律 monkeypatch，
只测 service interface 的外部行为（ADR-0012：service 接口即测试面）。
"""
import pytest

from app.models.database import (
    LearningPlanDraft, LearningPlanHistory, PlanRun, Student,
    StudentNotification, TeacherClassSubject,
)
from app.services.plan_draft_service import plan_draft_service as svc
from app.services.plan_run_service import plan_run_service as run_svc
from app.services.work_unit import work_unit


@pytest.fixture(autouse=True)
def _unit(db_session):
    """这一档测试验的是草稿语义，写入口的工作单元统一在这里开。

    "单元外调用必抛""一次决定一次提交"归 `tests/test_gate_unit_of_work.py`。
    """
    with work_unit(db_session):
        yield


def _create(db, student_id="", student_name="", created_by=""):
    """新契约下"生成一份草稿"是两步：单元外算内容、单元内落库。测试里合成一步。

    算不出来（学生不存在、同名多个、模型没配）时直接把那一步的结果返回 ——
    `multiple_matches` 载荷里没有 `error` 键，所以按"有没有 student_id"判。
    """
    computed = svc.compute_draft(db, student_id=student_id, student_name=student_name)
    if not computed.get("student_id"):
        return computed
    return svc.create_draft(db, computed, created_by=created_by)


def _revise(db, draft_id, feedback, revised_by=""):
    computed = svc.compute_revision(db, draft_id, feedback)
    if "error" in computed:
        return computed
    return svc.revise_draft(db, computed, revised_by)


def _decide(db, draft_id, action, actor_id="", feedback=""):
    """打回要先在单元外算出修订版，另两个决定不需要。"""
    computed = None
    if action == "reject" and (feedback or "").strip():
        computed = svc.compute_revision(db, draft_id, feedback)
        if "error" in computed:
            return computed
    return svc.record_decision(db, draft_id=draft_id, action=action,
                               actor_id=actor_id, feedback=feedback,
                               computed=computed)


PLAN_V1 = {"plan_title": "张三计划", "weekly_goals": ["盐类水解"], "daily_tasks": [{"day": 1, "task": "复习"}]}
PLAN_V2 = {"plan_title": "张三计划v2", "weekly_goals": ["盐类水解专题"], "daily_tasks": [{"day": 1, "task": "专题复习"}]}


@pytest.fixture
def teacher_student(db_session):
    """一个学生 + 一位教他所在班的老师。"""
    db_session.add(Student(student_id="s1", name="张三", class_id="c1", status="approved"))
    db_session.add(Student(student_id="s2", name="李四", class_id="c2", status="approved"))
    db_session.add(TeacherClassSubject(id=1, teacher_id="t1", class_id="c1", subject="chemistry"))
    db_session.flush()
    return db_session


@pytest.fixture
def fake_llm(monkeypatch):
    """LLM 生成/修订打桩：可数的假计划流。"""
    import sys
    mod = sys.modules["app.services.llm_service"]

    calls = {"generate": 0, "revise": []}

    def fake_generate(**kwargs):
        calls["generate"] += 1
        return {"success": True, "content": _plan_json(PLAN_V1)}

    def fake_revise(**kwargs):
        calls["revise"].append(kwargs.get("feedback", ""))
        return {"success": True, "content": _plan_json(PLAN_V2)}

    monkeypatch.setattr(mod.llm_service, "generate_learning_plan", fake_generate)
    monkeypatch.setattr(mod.llm_service, "revise_learning_plan", fake_revise)
    return calls


def _plan_json(plan):
    import json
    return json.dumps(plan, ensure_ascii=False)


# ── 生成与 regenerate ─────────────────────────────────────────

def test_create_draft_v1(fake_llm, teacher_student):
    out = _create(teacher_student, "s1", created_by="t1")
    assert "error" not in out
    assert out["version"] == 1
    assert out["status"] == "awaiting_confirmation"
    assert out["draft_id"] and out["content"]["plan_title"] == "张三计划"


def test_create_draft_resolves_by_name(fake_llm, teacher_student):
    out = _create(teacher_student, "", created_by="t1", student_name="张三")
    assert out.get("student_id") == "s1"


def test_create_draft_multiple_name_matches(fake_llm, teacher_student):
    teacher_student.add(Student(student_id="s3", name="张三丰", class_id="c1", status="approved"))
    teacher_student.flush()
    out = _create(teacher_student, "", student_name="张三")
    assert "multiple_matches" in out


def test_regenerate_supersedes_old_draft(fake_llm, teacher_student):
    first = _create(teacher_student, "s1")
    second = _create(teacher_student, "s1")
    assert first["draft_id"] != second["draft_id"]
    assert svc.get_draft(teacher_student, first["draft_id"])["status"] == "superseded"
    active = svc.get_active_draft(teacher_student, "s1")
    assert active["draft_id"] == second["draft_id"]


# ── 修订（revise 同链）────────────────────────────────────────

def test_revise_appends_version_with_feedback(fake_llm, teacher_student):
    d = _create(teacher_student, "s1")
    v2 = _revise(teacher_student, d["draft_id"], "第二周改成盐类水解专题", "t1")
    assert v2["version"] == 2
    assert v2["feedback"] == "第二周改成盐类水解专题"
    assert v2["draft_id"] == d["draft_id"]
    assert fake_llm["revise"] == ["第二周改成盐类水解专题"]
    versions = svc.list_versions(teacher_student, d["draft_id"])
    assert [v["version"] for v in versions] == [1, 2]


def test_revise_requires_feedback(fake_llm, teacher_student):
    d = _create(teacher_student, "s1")
    assert "error" in _revise(teacher_student, d["draft_id"], "  ")


def test_revise_resets_approval(fake_llm, teacher_student):
    d = _create(teacher_student, "s1")
    _decide(teacher_student, d["draft_id"], "approve", "t1")
    v2 = _revise(teacher_student, d["draft_id"], "改任务量", "t1")
    assert v2["teacher_approved"] is False, "修订后旧批准必须归零（再确认门）"


def test_revise_unknown_draft(fake_llm, teacher_student):
    assert "error" in _revise(teacher_student, "nope", "改")


# ── 三值决定 ──────────────────────────────────────────────────

def test_decision_approve_sets_key(fake_llm, teacher_student):
    d = _create(teacher_student, "s1")
    out = _decide(teacher_student, d["draft_id"], "approve", "t1")
    assert out["teacher_approved"] is True


def test_decision_reject_requires_feedback_and_revises(fake_llm, teacher_student):
    d = _create(teacher_student, "s1")
    assert "error" in _decide(teacher_student, d["draft_id"], "reject", "t1", "")
    out = _decide(teacher_student, d["draft_id"], "reject", "t1", "Day3 练习改 2 道")
    assert out["version"] == 2 and out["feedback"] == "Day3 练习改 2 道"


def test_decision_abandon_keeps_draft(fake_llm, teacher_student):
    d = _create(teacher_student, "s1")
    out = _decide(teacher_student, d["draft_id"], "abandon", "t1")
    assert out["status"] == "awaiting_confirmation"
    assert svc.get_active_draft(teacher_student, "s1") is not None


def test_decision_unknown_action(fake_llm, teacher_student):
    d = _create(teacher_student, "s1")
    assert "error" in _decide(teacher_student, d["draft_id"], "maybe", "t1")


# ── 发送：门由数据强制 ─────────────────────────────────────────

def test_send_without_approval_is_gated(fake_llm, teacher_student):
    d = _create(teacher_student, "s1")
    out = svc.send_draft(teacher_student, "s1")
    assert out["approval_needed"] is True
    assert out["draft_id"] == d["draft_id"]
    row = teacher_student.query(Student).filter(Student.student_id == "s1").first()
    assert row.current_plan is None, "门未过不得发送"


def test_send_after_approval_delivers(fake_llm, teacher_student):
    d = _create(teacher_student, "s1")
    _decide(teacher_student, d["draft_id"], "approve", "t1")
    out = svc.send_draft(teacher_student, "s1", d["draft_id"], "t1")
    assert "result" in out
    row = teacher_student.query(Student).filter(Student.student_id == "s1").first()
    assert row.current_plan["plan_title"] == "张三计划"
    assert teacher_student.query(LearningPlanHistory).count() == 1
    assert svc.get_draft(teacher_student, d["draft_id"])["status"] == "sent"
    # 学生收件箱投递
    assert teacher_student.query(StudentNotification).count() == 1


def test_send_notification_idempotent_per_version(fake_llm, teacher_student, monkeypatch):
    d = _create(teacher_student, "s1")
    _decide(teacher_student, d["draft_id"], "approve", "t1")
    svc.send_draft(teacher_student, "s1", d["draft_id"], "t1")
    # 重放同一版本（at-least-once 投递）→ 幂等键挡住重复通知
    row = (teacher_student.query(LearningPlanDraft)
           .filter(LearningPlanDraft.draft_id == d["draft_id"])
           .order_by(LearningPlanDraft.version.desc()).first())
    row.status = "awaiting_confirmation"
    row.teacher_approved = True
    teacher_student.flush()
    svc.send_draft(teacher_student, "s1", d["draft_id"], "t1")
    assert teacher_student.query(StudentNotification).count() == 1


def test_send_approved_without_identity_denied(fake_llm, teacher_student):
    """门已过但缺教师身份 → 拒绝（不可逆发送必须有人对身份负责，ADR-0009）。"""
    d = _create(teacher_student, "s1")
    _decide(teacher_student, d["draft_id"], "approve", "t1")
    out = svc.send_draft(teacher_student, "s1", d["draft_id"], "")
    assert "缺少发送者身份" in out.get("error", ""), out  # 批 3：家长/教师双通道，文案改发送者
    row = teacher_student.query(Student).filter(Student.student_id == "s1").first()
    assert row.current_plan is None, "未发送"


def test_send_denied_without_data_scope(fake_llm, teacher_student):
    d = _create(teacher_student, "s2")  # s2 所在班 c2 无人教
    out = svc.send_draft(teacher_student, "s2", d["draft_id"], "t1")
    assert "Data Scope" in out.get("error", ""), f"应 deny: {out}"
    assert "approval_needed" not in out


def test_send_no_draft_at_all_still_gates(fake_llm, teacher_student):
    out = svc.send_draft(teacher_student, "s1", "", "t1")
    assert out["approval_needed"] is True
    assert "draft_id" not in out


# ── Plan Run ─────────────────────────────────────────────────

STEPS = [
    {"step": 1, "skill": "generate_learning_plan", "description": "生成计划"},
    {"step": 2, "skill": "send_learning_plan", "description": "发送计划"},
    {"step": 3, "skill": "weekly_report", "description": "发周报"},
]


def test_run_start_and_mark(db_session):
    run_svc.start_run(db_session, "conv1", "给张三做计划", STEPS)
    run_svc.mark_step(db_session, "conv1", 1, "completed")
    out = run_svc.mark_step(db_session, "conv1", 2, "awaiting_confirmation")
    assert out["steps"][1]["status"] == "awaiting_confirmation"
    assert out["status"] == "running"


def test_run_gate_loop_then_complete(db_session):
    """门循环用况：等待确认 → 修订中 → 再等待 → 完成 → run 收尾。"""
    run_svc.start_run(db_session, "conv1", "goal", STEPS)
    run_svc.mark_step(db_session, "conv1", 1, "completed")
    run_svc.mark_step(db_session, "conv1", 2, "awaiting_confirmation")
    run_svc.mark_step(db_session, "conv1", 2, "revising")
    run_svc.mark_step(db_session, "conv1", 2, "awaiting_confirmation")
    run_svc.mark_step(db_session, "conv1", 2, "completed")
    out = run_svc.mark_step(db_session, "conv1", 3, "completed")
    assert out["status"] == "completed"


def test_run_abandon_marks_step_cancelled_and_continues(db_session):
    run_svc.start_run(db_session, "conv1", "goal", STEPS)
    run_svc.mark_step(db_session, "conv1", 1, "completed")
    run_svc.mark_step(db_session, "conv1", 2, "cancelled")
    out = run_svc.mark_step(db_session, "conv1", 3, "completed")
    assert out["steps"][1]["status"] == "cancelled"
    assert out["status"] == "completed", "放弃一步不报废整个运行"


def test_run_supersede_and_resync(db_session):
    run_svc.start_run(db_session, "conv1", "old", STEPS)
    run_svc.start_run(db_session, "conv1", "new", STEPS[:1])
    active = run_svc.get_active_run(db_session, "conv1")
    assert active["goal"] == "new"
    assert db_session.query(PlanRun).filter(PlanRun.conversation_id == "conv1").count() == 2


def test_run_mark_without_run_is_noop(db_session):
    assert run_svc.mark_step(db_session, "ghost", 1, "completed") is None
