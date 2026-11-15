"""任务 B —— grade_answer_sheets 的标准答案自动补全（清单 §1.3 第 6 条 / §4）。

老行为：docstring 写着「判卷前必须先向老师要标准答案」，纯 prompt 约定、无代码强制，
老师不贴答案时每张卡都退回「缺少正确答案」。现在 exam_id 指向真考试且题库有答案时
自动取；取不到才 fail-closed 并给可选项——「问老师」这条通路照样在。
"""
import asyncio
import json
from datetime import datetime

import pytest


@pytest.fixture
def key_db(db_session, monkeypatch):
    """题库：一场有答案的考试 + 一场没填答案的考试 + 别班一场；一批已识别的答题卡。"""
    import app.models.database as db_mod
    from agent.tools.context import set_current_teacher
    from app.models.database import (
        Account, AuditStatus, Class, Difficulty, ExamRecord, Question, QuestionSource,
        RecordType, TeacherClassSubject,
    )
    from app.models.ocr_task import OCRTask

    db_session.add(Class(class_id="c1", name="高一(1)班", grade="高一", subject="化学"))
    db_session.add(Class(class_id="c9", name="别人班", grade="高一", subject="化学"))
    db_session.add(ExamRecord(record_id="ex_ok", class_id="c1", name="10月月考化学",
                              type=RecordType.EXAM, exam_date=datetime(2026, 10, 8)))
    db_session.add(ExamRecord(record_id="ex_nokey", class_id="c1", name="没填答案的考试",
                              type=RecordType.EXAM, exam_date=datetime(2026, 9, 8)))
    db_session.add(ExamRecord(record_id="ex_other", class_id="c9", name="别班考试",
                              type=RecordType.EXAM, exam_date=datetime(2026, 10, 1)))
    for qid, rec, ans in (("ex_ok_1", "ex_ok", "A"), ("ex_ok_2", "ex_ok", "B"),
                          ("ex_nokey_1", "ex_nokey", ""),
                          ("ex_other_1", "ex_other", "A")):
        db_session.add(Question(
            question_id=qid, record_id=rec, content=f"{qid} 题干",
            question_type="single_choice", answer=ans, options=["A", "B", "C", "D"],
            analysis="", knowledge_points=[], difficulty=Difficulty.MEDIUM,
            source=QuestionSource.MANUAL_SELECTED, audit_status=AuditStatus.PASSED))
    db_session.add(OCRTask(
        task_id="t_done", teacher_id="T1", batch_id="b1", title="答题卡_01",
        image_path="/x.jpg", status="done", student_id="s1", student_name="张三",
        created_at=datetime(2026, 10, 9),
        ocr_result={"answers": [{"q_number": 1, "answer": "A"},
                                {"q_number": 2, "answer": "C"}]}))
    db_session.add(TeacherClassSubject(id="tcs1", teacher_id="T1", class_id="c1",
                                       subject="化学"))
    db_session.add(Account(account_id="acc_T1", username="t1", role="teacher",
                           teacher_id="T1", password_hash="x"))
    db_session.commit()

    monkeypatch.setattr(db_mod, "get_db", lambda: iter([db_session]))
    set_current_teacher("T1")
    yield db_session
    set_current_teacher("")


def test_missing_answers_autofilled_from_exam_bank(key_db):
    """验收 ①：缺 correct_answers 但题库有答案 → 自动补全 + 结果标注来源。"""
    from agent.tools.ocr_grading import grade_answer_sheets

    out = json.loads(asyncio.run(grade_answer_sheets(batch_id="b1", exam_id="ex_ok")))

    assert "error" not in out
    assert "标准答案取自题库考试" in out["answer_source"]
    assert "10月月考化学" in out["answer_source"]
    assert "10月月考化学" in out["message"]        # 回给老师的话里也能看见来源
    r = out["results"][0]
    assert r["score"] == 1 and r["total"] == 2     # Q1 A✓ / Q2 B vs C ✗


def test_no_answer_source_fails_closed_with_candidates(key_db):
    """验收 ②：题库也取不到 → fail-closed（不批、不猜），并给可选项。"""
    from agent.tools.ocr_grading import grade_answer_sheets
    from app.models.ocr_task import OCRTask

    out = json.loads(asyncio.run(grade_answer_sheets(batch_id="b1")))

    assert "缺少标准答案" in out["error"]
    assert out["missing"] == ["correct_answers"]
    names = [c["name"] for c in out["candidate_exams"]]
    assert "10月月考化学" in names                 # 候选考试（含可用题数）
    assert "别班考试" not in names                 # 范围外的班不进候选
    # 真没批：库里没有 grading_result，未判题没被固化成错答
    assert key_db.query(OCRTask).filter_by(task_id="t_done").one().grading_result is None


def test_exam_without_answers_still_asks_teacher(key_db):
    """考试存在但题库没填答案 → 仍然要问老师，而不是把整批卡批成未判。"""
    from agent.tools.ocr_grading import grade_answer_sheets

    out = json.loads(asyncio.run(grade_answer_sheets(batch_id="b1", exam_id="ex_nokey")))

    assert "没有标准答案" in out["error"]
    assert "correct_answers" in out["error"]       # 两条通路都点名，别堵死


def test_other_class_exam_key_denied(key_db):
    """别班考试的答案不能用来批自己的卡（与判卷中心同一口径）。"""
    from agent.tools.ocr_grading import grade_answer_sheets

    out = json.loads(asyncio.run(grade_answer_sheets(batch_id="b1", exam_id="ex_other")))

    assert "无权" in out["error"]


def test_teacher_pasted_answers_still_work(key_db):
    """两条通路并存：老师贴答案时照旧优先，且不编一个"题库来源"出来。"""
    from agent.tools.ocr_grading import grade_answer_sheets

    out = json.loads(asyncio.run(grade_answer_sheets(
        batch_id="b1", correct_answers="1.A 2.B 3.C")))

    assert "error" not in out
    assert "answer_source" not in out
    assert out["results"][0]["score"] == 1
