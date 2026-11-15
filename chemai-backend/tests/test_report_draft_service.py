"""家长报告草稿 service 接缝测试（issue #12 / ADR-0013）。

镜像 test_plan_confirmation.py：生成（聚合）/ revise 链 / 三值决定 / 数据强制门 /
发送效果（幂等通知）/ Data Scope / elevation 退役红线。LLM 一律 monkeypatch。
"""
import json

import pytest

from app.models.database import (
    Parent, ParentNotification, ParentReportDraft, Student,
    StudentParentBinding, TeacherClassSubject,
)
from app.services.parent_report_draft_service import parent_report_draft_service as svc
from app.services.work_unit import work_unit


@pytest.fixture(autouse=True)
def _unit(db_session):
    """这一档测试验的是草稿语义，写入口的工作单元统一在这里开。

    "单元外调用必抛""一次决定一次提交"不在这里验，归
    `tests/test_gate_unit_of_work.py`（前置一的运行时契约）。
    """
    with work_unit(db_session):
        yield


def _revise(db, draft_id, feedback, revised_by=""):
    """新契约下打回是两步：单元外算修订版、单元内追加版本行。"""
    computed = svc.compute_revision(db, draft_id, feedback)
    if "error" in computed:
        return computed
    return svc.revise_draft(db, computed, revised_by)


REPORT_V1 = {
    "student_name": "张三", "class_name": "c1", "exercises_completed": 12,
    "week_practice_count": 5, "week_accuracy": 80, "dominant_barrier": "概念理解",
    "barrier_distribution": {"concept": 0.5}, "weak_knowledge_points": [{"name": "水解", "errors": 3}],
    "suggestion": "建议建立知识框架图",
}


@pytest.fixture
def env(db_session, monkeypatch):
    db_session.add(Student(student_id="s1", name="张三", class_id="c1", status="approved"))
    db_session.add(TeacherClassSubject(id=1, teacher_id="t1", class_id="c1", subject="chemistry"))
    db_session.add(Parent(parent_id="p1", name="家长一", password_hash="x"))
    db_session.add(StudentParentBinding(binding_id=1, student_id="s1", parent_id="p1", status="active", bind_code="bc1"))
    db_session.flush()

    # 报告聚合依赖 students/student_answers/questions 表——聚合走真实 SQL，
    # 本测试只需要 create 出草稿，学生已在（无练习数据时聚合得零值报告，可用）
    import sys
    mod = sys.modules["app.services.llm_service"]

    def fake_revise(student_name, previous_report, feedback):
        merged = dict(previous_report)
        merged["suggestion"] = f"已按意见调整：{feedback}"
        merged["teacher_note"] = "感谢配合"
        return {"success": True, "report": merged}

    monkeypatch.setattr(mod.llm_service, "revise_parent_report", fake_revise)
    return db_session


def test_create_draft_aggregates_and_gates(env):
    d = svc.create_draft(env, "s1", created_by="t1")
    assert "draft_id" in d, d
    assert d["version"] == 1 and d["status"] == "awaiting_confirmation"
    assert d["content"]["student_name"] == "张三"
    assert d["teacher_approved"] is False


def test_create_draft_unknown_student(env):
    assert "error" in svc.create_draft(env, "ghost")


def test_regenerate_supersedes(env):
    a = svc.create_draft(env, "s1")
    b = svc.create_draft(env, "s1")
    assert a["draft_id"] != b["draft_id"]
    assert svc.get_draft(env, a["draft_id"])["status"] == "superseded"


def test_revise_appends_version(env):
    d = svc.create_draft(env, "s1")
    v2 = _revise(env, d["draft_id"], "弱化排名表述", "t1")
    assert v2["version"] == 2
    assert v2["content"]["suggestion"].startswith("已按意见调整")
    assert v2["content"]["exercises_completed"] == d["content"]["exercises_completed"], "事实字段不动"
    assert v2["teacher_approved"] is False
    assert [v["version"] for v in svc.list_versions(env, d["draft_id"])] == [1, 2]


def test_revise_requires_feedback(env):
    d = svc.create_draft(env, "s1")
    assert "error" in _revise(env, d["draft_id"], "  ")


def test_decision_trio(env):
    d = svc.create_draft(env, "s1")
    assert "error" in svc.record_decision(env, d["draft_id"], "approve", "", "")  # 缺身份
    out = svc.record_decision(env, d["draft_id"], "approve", "t1")
    assert out["teacher_approved"] is True


def test_send_gated_without_approval(env):
    d = svc.create_draft(env, "s1")
    out = svc.send_draft(env, "s1", d["draft_id"], "t1")
    assert out["approval_needed"] is True
    assert out["draft_id"] == d["draft_id"]
    assert out["action"] == "send_parent"
    assert env.query(ParentNotification).count() == 0


def test_elevation_cannot_bypass_data_gate(env):
    """红线：进程级提权布尔不再放行报告发送（数据门取代 elevation）——
    未确认草稿依旧弹卡（approval_needed），elevation 不产生任何放行效果。"""
    d = svc.create_draft(env, "s1")
    from agent.tools.filesystem import set_elevate_once, _consume_elevation
    set_elevate_once()
    try:
        out = svc.send_draft(env, "s1", d["draft_id"], "t1")
    finally:
        _consume_elevation()
    assert out["approval_needed"] is True, "elevation 不得越过草稿数据门"


def test_send_approved_without_identity_denied(env):
    """门已过但缺教师身份 → 拒绝（fail-closed，ADR-0009）。"""
    d = svc.create_draft(env, "s1")
    svc.record_decision(env, d["draft_id"], "approve", "t1")
    out = svc.send_draft(env, "s1", d["draft_id"], "")
    assert "缺少教师身份" in out.get("error", ""), out
    assert env.query(ParentNotification).count() == 0


def test_send_after_approval_idempotent(env):
    d = svc.create_draft(env, "s1")
    svc.record_decision(env, d["draft_id"], "approve", "t1")
    out = svc.send_draft(env, "s1", d["draft_id"], "t1")
    assert "result" in out
    assert env.query(ParentNotification).count() == 1
    # at-least-once 重放 → 幂等键挡住
    row = (env.query(ParentReportDraft)
           .filter(ParentReportDraft.draft_id == d["draft_id"])
           .order_by(ParentReportDraft.version.desc()).first())
    row.status = "awaiting_confirmation"
    row.teacher_approved = True
    env.flush()
    svc.send_draft(env, "s1", d["draft_id"], "t1")
    assert env.query(ParentNotification).count() == 1
    assert json.dumps(svc.get_draft(env, d["draft_id"]))  # status=sent 可序列化


def test_regenerated_identical_report_not_delivered_twice(env):
    """B1：重新生成一份内容相同的报告再发，家长不该收到第二条一模一样的通知。

    `_aggregate` 是纯查库聚合，数据没变报告就逐字节相同；旧幂等键含 draft_id，
    新草稿换来新键 → 再投一条（2026-10 实测 parent_241914 收到两条同内容的
    「📋 陈湘旻 的学习报告」，title/content 完全一致）。
    """
    first = svc.create_draft(env, "s1")
    svc.record_decision(env, first["draft_id"], "approve", "t1")
    svc.send_draft(env, "s1", first["draft_id"], "t1")
    assert env.query(ParentNotification).count() == 1

    again = svc.create_draft(env, "s1")
    assert again["draft_id"] != first["draft_id"]
    assert again["content"] == first["content"], "前置：数据没变，报告内容相同"
    svc.record_decision(env, again["draft_id"], "approve", "t1")
    out = svc.send_draft(env, "s1", again["draft_id"], "t1")

    assert env.query(ParentNotification).count() == 1, "内容相同的报告被重复投递"
    assert out["sent_parents"] == []
    assert out["already_delivered"] == ["家长一"]
    assert "未重复投递" in out["result"], out["result"]
    assert svc.get_draft(env, again["draft_id"])["status"] == "sent", \
        "没新投递也要落 sent —— 否则确认门永远关不上"


def test_changed_report_content_delivered_again(env):
    """去重按内容：数据变了就是一份新报告，家长照旧收到新通知。"""
    first = svc.create_draft(env, "s1")
    svc.record_decision(env, first["draft_id"], "approve", "t1")
    svc.send_draft(env, "s1", first["draft_id"], "t1")

    env.query(Student).filter(Student.student_id == "s1").update(
        {"exercises_completed": 9})
    env.flush()
    again = svc.create_draft(env, "s1")
    assert again["content"]["exercises_completed"] == 9
    svc.record_decision(env, again["draft_id"], "approve", "t1")
    out = svc.send_draft(env, "s1", again["draft_id"], "t1")

    assert env.query(ParentNotification).count() == 2
    assert out["sent_parents"] == ["家长一"]


def test_send_denied_out_of_scope(env):
    d = svc.create_draft(env, "s1")
    out = svc.send_draft(env, "s1", d["draft_id"], "ghost")
    assert "Data Scope" in out.get("error", "")


def test_send_no_bindings_errors(env):
    d = svc.create_draft(env, "s1")
    env.query(StudentParentBinding).delete()
    env.flush()
    svc.record_decision(env, d["draft_id"], "approve", "t1")
    out = svc.send_draft(env, "s1", d["draft_id"], "t1")
    assert "未绑定家长" in out.get("error", "")
