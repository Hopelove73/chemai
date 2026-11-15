"""任务 A —— review_deck 的 context_data 自动补全（清单 §1.3 第 9 条）。

过去缺 context_data 就当场 fail-closed，等于让系统向老师要它自己已经握着的批改数据。
两条路径都钉住：①库里有批改数据 → 自动补全，且回复里写明取自哪次考试；②库里也没有
→ 仍然 fail-closed，并给出可选路径（指明考试 / 先批改），不猜一场考试出来。
"""
import asyncio
import json
from datetime import datetime

import pytest


@pytest.fixture
def graded_db(db_session):
    """已保存成绩的考试：本班 2 人 4 行作答 + 别班一场（不得被取用）。"""
    from app.models.database import (
        Account, AnswerSource, AuditStatus, Class, Difficulty, ExamRecord, Question,
        QuestionSource, RecordType, StudentAnswer, TeacherClassSubject,
    )

    db_session.add(Class(class_id="c1", name="高一(1)班", grade="高一", subject="化学"))
    db_session.add(Class(class_id="c2", name="高一(2)班", grade="高一", subject="化学"))
    db_session.add(ExamRecord(record_id="ex_mine", class_id="c1", name="10月月考化学",
                              type=RecordType.EXAM, exam_date=datetime(2026, 10, 8)))
    db_session.add(ExamRecord(record_id="ex_other", class_id="c2", name="别班考试",
                              type=RecordType.EXAM, exam_date=datetime(2026, 10, 20)))
    for qid, rec, kps in (("ex_mine_1", "ex_mine", ["盐类水解"]),
                          ("ex_mine_2", "ex_mine", ["电离平衡"]),
                          ("ex_other_1", "ex_other", ["原电池"])):
        db_session.add(Question(
            question_id=qid, record_id=rec, content=f"{qid} 的题干",
            question_type="single_choice", answer="A", options=["A", "B", "C", "D"],
            analysis="", knowledge_points=kps, difficulty=Difficulty.MEDIUM,
            source=QuestionSource.MANUAL_SELECTED, audit_status=AuditStatus.PASSED))
    for aid, sid, qid, rec, ans, ok in (
        ("a1", "s1", "ex_mine_1", "ex_mine", "B", False),
        ("a2", "s1", "ex_mine_2", "ex_mine", "A", True),
        ("a3", "s2", "ex_mine_1", "ex_mine", "A", True),
        ("a4", "s2", "ex_mine_2", "ex_mine", "A", True),
        ("a5", "s3", "ex_other_1", "ex_other", "B", False),
    ):
        db_session.add(StudentAnswer(
            answer_id=aid, student_id=sid, question_id=qid, exam_record_id=rec,
            student_answer=ans, is_correct=ok, confirmed=True, source=AnswerSource.OCR))
    db_session.add(TeacherClassSubject(id="tcs1", teacher_id="T1", class_id="c1",
                                       subject="化学"))
    db_session.add(Account(account_id="acc_T1", username="t1", role="teacher",
                           teacher_id="T1", password_hash="x"))
    db_session.commit()
    return db_session


def test_context_payload_uses_own_class_exam(graded_db):
    """取数口径：只取本班考试，字段对齐 REVIEW_DECK_PROMPT（概览/错题/知识点）。"""
    from app.services.grading_center import CallerScope
    from app.services.review_deck_context import latest_graded_context

    got = latest_graded_context(graded_db, CallerScope.from_user_id(graded_db, "T1"))
    assert got is not None
    assert "10月月考化学" in got["source"]          # 来源说明里带考试名

    payload = json.loads(got["context_data"])
    assert payload["exam"]["exam_record_id"] == "ex_mine"   # 别班考试不取
    assert payload["overview"]["students"] == 2
    assert payload["overview"]["answer_rows"] == 4
    assert set(payload["overview"]["score_bands"])          # 正确率带
    worst = payload["error_questions"][0]
    assert worst["question_id"] == "ex_mine_1" and worst["wrong"] == 1
    assert worst["wrong_answers"] == [{"answer": "B", "count": 1}]   # 高频错选
    assert payload["knowledge_point_errors"][0]["knowledge_point"] == "盐类水解"


def test_empty_db_returns_none(db_session):
    """库里没有批改数据 → None（调用方据此保持 fail-closed，不许编）。"""
    from app.services.grading_center import CallerScope
    from app.services.review_deck_context import latest_graded_context

    assert latest_graded_context(db_session, CallerScope.from_user_id(db_session, "T1")) is None


def test_newer_ocr_batch_beats_older_saved_exam(graded_db):
    """刚批完还没保存的答题卡比已保存考试更新时用批次，并如实标注数据缺口。"""
    from app.models.ocr_task import OCRTask
    from app.services.grading_center import CallerScope
    from app.services.review_deck_context import latest_graded_context

    graded_db.add(OCRTask(
        task_id="t1", teacher_id="T1", batch_id="b_new", title="答题卡_01",
        image_path="/x.jpg", status="done", student_id="s9", student_name="张三",
        created_at=datetime(2026, 10, 20),
        grading_result={"questions": [
            {"q_number": 1, "student_answer": "B", "correct_answer": "A", "is_correct": False},
            # 未判行不算错题（ADR-0018）：混进统计会把讲评重点带偏
            {"q_number": 2, "student_answer": "C", "correct_answer": "",
             "is_correct": False, "reason": "缺少标准答案（未判）"},
        ]}))
    graded_db.commit()

    got = latest_graded_context(graded_db, CallerScope.from_user_id(graded_db, "T1"))
    assert "b_new" in got["source"]
    payload = json.loads(got["context_data"])
    assert payload["exam"]["batch_id"] == "b_new"
    assert payload["overview"]["answer_rows"] == 1            # 未判那行被排除
    assert payload["error_questions"][0]["question_id"] == "1"
    assert payload["data_gaps"]                              # 缺题干/知识点要明说


@pytest.fixture
def tool_env(graded_db, monkeypatch, tmp_path):
    """讲评稿工具：内存库 + 假 provider（不碰网络）+ tmp 工作区，身份 T1。"""
    import app.models.database as db_mod
    from agent.tools import context as ctx

    monkeypatch.setattr(db_mod, "get_db", lambda: iter([graded_db]))
    monkeypatch.setenv("CHEMAI_WORKSPACE", str(tmp_path / "ws"))
    ctx.set_current_identity("T1", "teacher")
    seen = {}

    async def fake_chat(self, messages, temperature=0.7, max_tokens=2048, tools=None):
        seen["user"] = messages[1]["content"]

        class R:
            content = "<!DOCTYPE html><html><body>讲评稿</body></html>"
            tool_calls = []
            usage = {}
            model = "fake"
        return R()

    monkeypatch.setattr("agent.provider.deepseek.DeepSeekProvider.chat", fake_chat)
    yield seen
    ctx.set_current_identity("", "")


def test_missing_context_data_autofills_and_reports_source(tool_env):
    """验收 ①：缺参但库里有数据 → 自动补全 + 结果里有数据来源说明。"""
    from agent.tools.teaching import invoke_teaching_skill

    out = json.loads(asyncio.run(invoke_teaching_skill(
        skill="review_deck", topic="10月月考讲评")))

    assert "error" not in out
    assert "10月月考化学" in out["data_source"]
    assert "10月月考化学" in out["text"]          # 回复里说明取自哪次考试
    assert "10月月考化学" in tool_env["user"]      # 补全的数据真进了 prompt
    assert out["artifact"]["kind"] == "doc"


def test_no_data_still_fail_closed_with_options(db_session, monkeypatch):
    """验收 ②：库里也没有 → 仍旧 fail-closed，并提示老师可选路径。"""
    import app.models.database as db_mod
    from agent.tools import context as ctx
    from agent.tools.teaching import invoke_teaching_skill

    monkeypatch.setattr(db_mod, "get_db", lambda: iter([db_session]))
    ctx.set_current_identity("T1", "teacher")      # 有身份、库里没批改数据
    try:
        out = json.loads(asyncio.run(invoke_teaching_skill(
            skill="review_deck", topic="期中讲评")))
    finally:
        ctx.set_current_identity("", "")

    assert "error" in out
    assert "批改数据" in out["error"] and "context_data" in out["error"]
    assert "list_teacher_exams" in out["error"]    # 问老师这条通路没堵死
