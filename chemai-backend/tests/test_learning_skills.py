"""学习技能注册表与入口工具（ADR-0029 批②，spec #20）。

契约真源：docs/designs/learning-skills-contract.md——本测试按契约逐分支钉：
注册表契约 / persona 面（会话类 student 专属）/ 接棒载荷 / 归属校验 / 降级分支。
"""
import json
from datetime import datetime, timedelta

import pytest

from app.models.database import (
    MemoryCard, Question, QuestionSource, AuditStatus, Difficulty,
    StudentAnswer, StudentParentBinding,
)
from app.services.learning_skills import REGISTRY, dispatch, skill_menu


# ── 注册表契约 ──────────────────────────────────────────────────────────

def test_registry_has_four_skills_with_docs():
    assert set(REGISTRY) == {"review_today", "smart_practice",
                             "variant_training", "mastery_report"}
    for s in REGISTRY.values():
        assert s.title and s.params_doc and s.kind in ("session", "read")
    assert skill_menu().count("（") == 4


def test_tool_description_enumerates_registry():
    from agent.tools.learning import invoke_learning_skill
    doc = invoke_learning_skill.__doc__ or ""
    for name in REGISTRY:
        assert name in doc
    assert "精确名" in doc


# ── persona 面 ──────────────────────────────────────────────────────────

def test_session_skill_rejects_parent(db_session, sample_student, parent_binding):
    out = dispatch(db_session, "smart_practice", {"student_name": "test_student"},
                   {"user_id": "p-1", "role": "parent"})
    assert "error" in out and "不面向" in out["error"]


def test_unknown_role_rejected(db_session, sample_student):
    out = dispatch(db_session, "mastery_report", {},
                   {"user_id": "t-1", "role": "teacher"})
    assert "error" in out and "不面向" in out["error"]


def test_unknown_skill_lists_available():
    from agent.tools.learning import invoke_learning_skill
    import asyncio
    out = json.loads(asyncio.run(invoke_learning_skill(skill="刷题")))
    assert "error" in out and "mastery_report" in out["available"]


# ── review_today 分支 ───────────────────────────────────────────────────

def test_review_today_no_due_no_route(db_session, sample_student):
    out = dispatch(db_session, "review_today", {},
                   {"user_id": sample_student.student_id, "role": "student"})
    assert out["due_count"] == 0
    assert "_route" not in out
    assert "已清空" in out["text"]


def test_review_today_with_due_has_handoff(db_session, sample_student, due_card):
    out = dispatch(db_session, "review_today", {},
                   {"user_id": sample_student.student_id, "role": "student"})
    assert out["due_count"] >= 1
    route = out["_route"]
    assert route["page"] == "/m/review" and route["navigate"] is True
    assert route["state"]["fromChat"] == "review"


def test_review_today_parent_status_no_route(db_session, sample_student,
                                             due_card, parent_binding):
    out = dispatch(db_session, "review_today", {"action": "status"},
                   {"user_id": "p-1", "role": "parent",
                    "student_name": "test_student"})
    assert out["due_count"] >= 1 and "_route" not in out


def test_review_today_debrief_reads_real_counts(db_session, sample_student, due_card):
    out = dispatch(db_session, "review_today", {"action": "debrief"},
                   {"user_id": sample_student.student_id, "role": "student"})
    assert out["cleared"] is False and out["due_count"] >= 1


# ── smart_practice 接棒 ─────────────────────────────────────────────────

def test_smart_practice_starts_session_and_hands_off(db_session, sample_student,
                                                     seeded_questions):
    out = dispatch(db_session, "smart_practice",
                   {"kps": ["化学平衡"]},
                   {"user_id": sample_student.student_id, "role": "student"})
    assert "error" not in out, out.get("error")
    assert out["session_id"].startswith("smart_")
    # 接棒带整会话（首题已在）：页面续接，不自调 /smart/start 留孤儿会话
    route = out["_route"]
    assert route["page"] == "/m/practice"
    ss = route["state"]["smartSession"]
    assert ss["practice_id"] == out["session_id"]
    assert ss["question"]["question_id"].startswith("sq_")


def test_smart_practice_no_data_error(db_session, sample_student):
    out = dispatch(db_session, "smart_practice", {},
                   {"user_id": sample_student.student_id, "role": "student"})
    assert "error" in out and "知识点" in out["error"]


# ── variant_training 归属 ───────────────────────────────────────────────

def test_variant_training_rejects_not_my_question(db_session, sample_student,
                                                  other_wrong_question):
    out = dispatch(db_session, "variant_training",
                   {"question_id": other_wrong_question.question_id},
                   {"user_id": sample_student.student_id, "role": "student"})
    assert "error" in out and "自己的错题" in out["error"]


def test_variant_training_requires_question_id(db_session, sample_student):
    out = dispatch(db_session, "variant_training", {},
                   {"user_id": sample_student.student_id, "role": "student"})
    assert "error" in out and "question_id" in out["error"]


# ── mastery_report ──────────────────────────────────────────────────────

def test_mastery_report_no_data_message(db_session, sample_student):
    out = dispatch(db_session, "mastery_report", {},
                   {"user_id": sample_student.student_id, "role": "student"})
    assert "还没有掌握度数据" in out["text"]


def test_mastery_report_parent_needs_binding(db_session, sample_student,
                                             seeded_answers):
    # 未绑定 → 拒绝
    out = dispatch(db_session, "mastery_report", {},
                   {"user_id": "p-none", "role": "parent"})
    assert "error" in out


# ── fixtures（自足：家长/绑定/到期卡/题池/错题归属）─────────────────────

@pytest.fixture
def parent_binding(db_session):
    from app.models.database import Parent
    db_session.add(Parent(parent_id="p-1", name="测试家长", password_hash="x"))
    db_session.add(StudentParentBinding(
        binding_id="b-1", student_id="202610101", parent_id="p-1",
        status="active", bind_code="123456"))
    db_session.commit()
    return "p-1"


@pytest.fixture
def due_card(db_session, sample_student):
    card = MemoryCard(student_id=sample_student.student_id, question_id="lq_q1",
                      due=datetime.utcnow() - timedelta(hours=1))
    db_session.add(card)
    db_session.commit()
    return card


def _mk_question(db_session, qid, kp="化学平衡", answer="A", options=None):
    q = Question(question_id=qid, record_id="rec_lq", content=f"题目{qid}",
                 options=options or ["A", "B"], answer=answer,
                 knowledge_points=[kp], difficulty=Difficulty.MEDIUM,
                 source=QuestionSource.MANUAL_SELECTED, audit_status=AuditStatus.PASSED)
    db_session.add(q)
    return q


@pytest.fixture
def seeded_questions(db_session):
    from app.models.database import ExamRecord, RecordType
    db_session.add(ExamRecord(record_id="rec_lq", class_id="class_2025_1",
                              name="题源", type=RecordType.PRACTICE,
                              exam_date=datetime.utcnow()))
    qs = [_mk_question(db_session, f"lq_q{i}", answer="A" if i % 2 else "B")
          for i in range(5)]
    db_session.commit()
    return qs


@pytest.fixture
def seeded_answers(db_session, sample_student, seeded_questions):
    for i, q in enumerate(seeded_questions[:3]):
        db_session.add(StudentAnswer(
            answer_id=f"la_{i}", student_id=sample_student.student_id,
            question_id=q.question_id, student_answer="A",
            is_correct=(i % 2 == 0), confirmed=True))
    db_session.commit()


@pytest.fixture
def other_wrong_question(db_session):
    """另一学生的错题（归属校验的拒绝面）。"""
    from app.models.database import ExamRecord, RecordType
    db_session.add(ExamRecord(record_id="rec_other", class_id="class_2025_1",
                              name="他人卷", type=RecordType.PRACTICE,
                              exam_date=datetime.utcnow()))
    q = _mk_question(db_session, "other_q1", kp="氧化还原")
    db_session.add(StudentAnswer(
        answer_id="oa_1", student_id="other_student", question_id="other_q1",
        student_answer="B", is_correct=False, confirmed=True))
    db_session.commit()
    return q
