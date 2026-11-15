"""接棒闭环的读侧（C4）：debrief/status 不依赖学生口述结果。

场景：学生被接棒跳到 /m/practice、/m/review 自己作答，**回到对话一句话都没说
"我做完了"**。本文件的用例先往库里造一条已完成的会话/今天的复习提交，再调
debrief——断言它读的是库里的事实（做了几题/对几题/会话做满没/连击记上没），
而不是复述 due_count 或相信学生的话。

「不动既有分支」也钉在这里：不带 action 调 smart_practice 仍然开一轮会话并带接棒。
"""
import pytest

from app.models.database import (
    ExamRecord, MemoryCard, Question, QuestionSource, AuditStatus, Difficulty,
    Parent, RecordType, StudentParentBinding, StudentStreak,
)
from app.services import smart_practice_service as sps
from app.services.learning_skills import dispatch
from app.services.streak_service import local_today


STUDENT = "202610101"   # conftest.sample_student.student_id


def _identity(role="student", user_id=STUDENT, **extra):
    return {"user_id": user_id, "role": role, **extra}


def _smart_record(db, student_id, answers, theta_start=0.2, session_id=None,
                  created_at=None):
    """造一轮智能练习会话（形状同 smart_practice_service.start_session 的会话态）。

    answers 是 [True/False, ...]——每题一条 responses 记录 + 一条 (a, b) 参数，
    与 /smart/answer 的写入形状一致（未标定题在此用标定参数，专注"结果读取"）。
    """
    responses = [{"question_id": f"sq_q{i}", "is_correct": bool(ok)}
                 for i, ok in enumerate(answers)]
    record = ExamRecord(
        record_id=session_id or f"smart_{student_id}_1759800000",
        class_id="class_2025_1",
        name="智能练习-测试",
        type=RecordType.PRACTICE,
        total_students=1, present_students=1,
        question_stats={"student_id": student_id, "smart": {
            "student_id": student_id,
            "theta_start": theta_start,
            "theta": None,
            "responses": responses,
            "params": [[1.0, 0.0]] * len(answers),
            "uncalibrated": 0,
            "kps": ["化学平衡"],
            "pool": [],
        }},
    )
    if created_at is not None:
        record.created_at = created_at
    db.add(record)
    db.commit()
    return record


# ── 练习接棒：debrief 读已完成会话（核心验收）──────────────────────────

def test_practice_debrief_reads_completed_session_without_any_claim(
        db_session, sample_student):
    # 8 题（= 题数上限，停止规则必停）、对 5 错 3；学生什么都没说
    _smart_record(db_session, STUDENT, [True] * 5 + [False] * 3)

    out = dispatch(db_session, "smart_practice", {"action": "debrief"},
                   _identity())

    assert out["found"] is True
    assert out["answered"] == 8 and out["correct"] == 5
    assert out["done"] is True            # 会话已做满一轮
    assert out["theta_end"] > out["theta_start"]
    assert out["theta_delta"] == pytest.approx(
        out["theta_end"] - out["theta_start"], abs=1e-6)
    assert "做了 8 题" in out["text"] and "答对 5 题" in out["text"]
    assert "做满一轮" in out["text"]


def test_practice_debrief_reports_unfinished_session(db_session, sample_student):
    _smart_record(db_session, STUDENT, [True, False, True])   # 3 题，未达停止规则

    out = dispatch(db_session, "smart_practice", {"action": "debrief"},
                   _identity())

    assert out["found"] is True and out["answered"] == 3
    assert out["done"] is False
    assert "还没做完" in out["text"]


def test_practice_debrief_without_session_says_so(db_session, sample_student):
    out = dispatch(db_session, "smart_practice", {"action": "debrief"},
                   _identity())
    assert out["found"] is False
    assert "没有你的智能练习记录" in out["text"]


def test_practice_debrief_rejects_other_students_session(db_session, sample_student):
    _smart_record(db_session, "other_student", [True, False],
                  session_id="smart_other_student_1759800001")

    out = dispatch(db_session, "smart_practice",
                   {"action": "debrief",
                    "session_id": "smart_other_student_1759800001"},
                   _identity())

    assert out["found"] is False     # 不是本人的会话：不泄漏也不冒充


def test_practice_debrief_honours_explicit_session_id(db_session, sample_student):
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    _smart_record(db_session, STUDENT, [True] * 2,
                  session_id=f"smart_{STUDENT}_1759800001",
                  created_at=now - timedelta(hours=3))
    _smart_record(db_session, STUDENT, [True] * 8,
                  session_id=f"smart_{STUDENT}_1759800002", created_at=now)

    latest = dispatch(db_session, "smart_practice", {"action": "debrief"},
                      _identity())
    chosen = dispatch(db_session, "smart_practice",
                      {"action": "debrief",
                       "session_id": f"smart_{STUDENT}_1759800001"},
                      _identity())

    assert latest["answered"] == 8                      # 缺省取最近一轮
    assert chosen["answered"] == 2                      # 显式指定读那一轮


# ── 复习接棒：debrief 读今天的真实提交 ─────────────────────────────────

def _card(db, student_id, qid, signal, days_until_due=3, reviewed=True):
    from datetime import datetime, timedelta
    now = datetime.utcnow()
    card = MemoryCard(student_id=student_id, question_id=qid,
                      due=now + timedelta(days=days_until_due),
                      state=2, reps=1,
                      last_review=now if reviewed else None,
                      last_signal=signal if reviewed else None)
    db.add(card)
    db.commit()
    return card


def test_review_debrief_reads_todays_real_submissions(db_session, sample_student):
    _card(db_session, STUDENT, "rq_1", "correct")
    _card(db_session, STUDENT, "rq_2", "wrong")
    _card(db_session, STUDENT, "rq_3", "forgot")

    out = dispatch(db_session, "review_today", {"action": "debrief"},
                   _identity())

    assert out["cleared"] is True            # 到期卡 0 张
    assert out["activity"]["reviewed"] == 3
    assert out["activity"]["correct"] == 1
    assert out["activity"]["wrong"] == 1
    assert out["activity"]["forgot"] == 1
    assert "实际复习了 3 张" in out["text"]
    assert "到期卡已清空" in out["text"]


def test_review_debrief_refuses_to_confirm_without_submissions(db_session,
                                                               sample_student):
    # 到期卡 0 张 + 今天零提交：不能说"已确认清空"（这就是 C4 要消灭的假闭环）
    out = dispatch(db_session, "review_today", {"action": "debrief"},
                   _identity())

    assert out["cleared"] is True and out["activity"]["reviewed"] == 0
    assert "没有复习提交记录" in out["text"]
    assert "已确认" not in out["text"]


def test_review_debrief_reports_streak_credited_today(db_session, sample_student):
    from datetime import datetime, timedelta
    db_session.add(StudentStreak(student_id=STUDENT, current=4, best=9,
                                 freeze_count=1, last_active_date=local_today()))
    db_session.add(MemoryCard(student_id=STUDENT, question_id="rq_9",
                              due=datetime.utcnow() + timedelta(days=2),
                              state=2, reps=1, last_review=datetime.utcnow(),
                              last_signal="correct"))
    db_session.commit()

    out = dispatch(db_session, "review_today", {"action": "debrief"},
                   _identity())

    assert out["activity"]["streak_credited_today"] is True
    assert out["streak"]["current"] == 4
    assert "今天已计入连击" in out["text"]


def test_review_status_carries_activity_for_parent(db_session, sample_student):
    db_session.add(Parent(parent_id="p-c4", name="测试家长", password_hash="x"))
    db_session.add(StudentParentBinding(
        binding_id="b-c4", student_id=STUDENT, parent_id="p-c4",
        status="active", bind_code="654321"))
    db_session.commit()
    _card(db_session, STUDENT, "rq_p1", "remembered")

    out = dispatch(db_session, "review_today", {"action": "status"},
                   _identity(role="parent", user_id="p-c4",
                             student_name="test_student"))

    assert out["activity"]["reviewed"] == 1
    assert "今天已提交 1 张复习" in out["text"]
    assert "_route" not in out          # 家长端不接棒（ADR-0029）


# ── 既有分支不回归：不带 action 仍然开会话 + 接棒 ────────────────────────

@pytest.fixture
def seeded_questions(db_session):
    db_session.add(ExamRecord(record_id="rec_c4", class_id="class_2025_1",
                              name="题源", type=RecordType.PRACTICE))
    for i in range(5):
        db_session.add(Question(
            question_id=f"c4_q{i}", record_id="rec_c4", content=f"题目{i}",
            options=["A", "B"], answer="A", knowledge_points=["化学平衡"],
            difficulty=Difficulty.MEDIUM, source=QuestionSource.MANUAL_SELECTED,
            audit_status=AuditStatus.PASSED))
    db_session.commit()


def test_smart_practice_default_branch_still_opens_session(db_session,
                                                           sample_student,
                                                           seeded_questions):
    out = dispatch(db_session, "smart_practice", {"kps": ["化学平衡"]},
                   _identity())

    assert "error" not in out, out.get("error")
    assert out["session_id"].startswith("smart_")
    assert out["_route"]["page"] == "/m/practice"
    assert out["_route"]["state"]["smartSession"]["practice_id"] == out["session_id"]
    # 开会话写进去的首题就是真的会话态：debrief 立刻能读到"还没作答"
    debrief = dispatch(db_session, "smart_practice", {"action": "debrief"},
                       _identity())
    assert debrief["found"] is True and debrief["answered"] == 0
    assert sps.MAX_ITEMS == debrief["max_items"]
