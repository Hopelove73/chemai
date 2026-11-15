"""parent_weekly_summary（ADR-0029 批③，spec #21）：三源拼装契约测试。

三源：parent_summary_service.build_cards（三卡）+ mastery_for_student
（消灭率，与 analytics elimination 端点同口径 BKT≥0.9）+ compose_summary_text
（一句话人话）。绑定校验走 parent._resolve_student（ADR-0014）。
"""
import json

import pytest

from app.models.database import (
    Parent, Question, QuestionSource, AuditStatus, Difficulty,
    ExamRecord, RecordType, StudentAnswer, StudentParentBinding, datetime,
)


@pytest.fixture
def parent_with_binding(db_session, sample_student):
    db_session.add(Parent(parent_id="p-1", name="周建国", password_hash="x"))
    db_session.add(StudentParentBinding(
        binding_id="b-1", student_id=sample_student.student_id,
        parent_id="p-1", status="active", bind_code="123456"))
    db_session.commit()
    return "p-1"


@pytest.fixture
def week_data(db_session, sample_student):
    """一份已作答（错）的练习 + 一份未完成的练习 + 过样本门槛的作答面。

    业务语义锚点：作答过（哪怕错答）= submitted，不算未完成（build_cards
    口径）；未完成必须来自无作答练习。mastery 样本门槛 MIN_SAMPLES=3。"""
    from datetime import timedelta
    db_session.add(ExamRecord(record_id="pw_rec", class_id=sample_student.class_id,
                              name="每周练习", type=RecordType.PRACTICE,
                              exam_date=datetime.utcnow()))
    db_session.add(Question(question_id="pw_q1", record_id="pw_rec",
                            content="盐类水解题", options=["A", "B"], answer="B",
                            knowledge_points=["盐类水解"], difficulty=Difficulty.MEDIUM,
                            source=QuestionSource.MANUAL_SELECTED,
                            audit_status=AuditStatus.PASSED))
    db_session.add(StudentAnswer(
        answer_id="pw_a1", student_id=sample_student.student_id,
        question_id="pw_q1", exam_record_id="pw_rec",
        student_answer="A", is_correct=False, confirmed=True))
    # 未完成练习：无任何作答行
    db_session.add(ExamRecord(record_id="pw_open", class_id=sample_student.class_id,
                              name="待做练习", type=RecordType.PRACTICE,
                              exam_date=datetime.utcnow()))
    db_session.add(Question(question_id="pw_q2", record_id="pw_open",
                            content="待做题", options=["A", "B"], answer="A",
                            knowledge_points=["化学平衡"], difficulty=Difficulty.MEDIUM,
                            source=QuestionSource.MANUAL_SELECTED,
                            audit_status=AuditStatus.PASSED))
    # 消灭率数据面：盐类水解 3 条作答（≥MIN_SAMPLES）全错 → 计入未消灭
    for i in range(3):
        db_session.add(Question(question_id=f"pw_m{i}", record_id="pw_rec",
                                content=f"梯度题{i}", options=["A", "B"], answer="B",
                                knowledge_points=["盐类水解"],
                                difficulty=Difficulty.MEDIUM,
                                source=QuestionSource.MANUAL_SELECTED,
                                audit_status=AuditStatus.PASSED))
        db_session.add(StudentAnswer(
            answer_id=f"pw_ma{i}", student_id=sample_student.student_id,
            question_id=f"pw_m{i}", exam_record_id="pw_rec",
            student_answer="A", is_correct=False, confirmed=True))
    db_session.commit()
    return sample_student.student_id


def _call(db_session, parent_id, student_name=""):
    import asyncio
    from agent.tools.context import set_current_identity
    from agent.tools.parent import parent_weekly_summary
    set_current_identity(parent_id, "parent")
    try:
        return json.loads(asyncio.run(parent_weekly_summary(student_name)))
    finally:
        set_current_identity(None)


def test_unbound_parent_rejected(db_session, patch_get_db, sample_student):
    out = _call(db_session, "p-none", "test_student")
    assert "error" in out and "绑定" in out["error"]


def test_summary_shape_three_sources(db_session, patch_get_db, sample_student,
                                     parent_with_binding, week_data):
    out = _call(db_session, "p-1")
    # 三源齐备：cards 三卡 / elimination 计数 / text 人话
    assert out["student"] == "test_student"
    cards = out["cards"]
    assert set(cards) == {"unfinished", "due_soon", "week_activity"}
    assert out["elimination"]["total"] >= 1
    # text 拼齐：三卡数字 + 消灭率 + 一件事
    assert "未完成" in out["text"] and "消灭" in out["text"]
    assert "能帮上的一件事" in out["text"]
    # summary_lines 来自 compose_summary_text（title + 三行）
    assert len(out["summary_lines"]) >= 3


def test_summary_counts_match_cards(db_session, patch_get_db, sample_student,
                                    parent_with_binding, week_data):
    out = _call(db_session, "p-1")
    undone = out["cards"]["unfinished"]
    assert undone["count"] >= 1
    # 错题建了卡（判卷自动建卡语义）→ 消灭率数据面有该知识点
    assert out["elimination"]["total"] >= 1
