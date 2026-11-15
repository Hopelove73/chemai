"""批次 C —— 掌握概率（BKT）+ IRT 标定 + CAT 选题。

四组口径：
1. BKT 数学：一路错→低掌握；先错后对→上升；样本 <3 不下结论（不下结论也是结论）。
2. 标定数学：正确率→logit 难度（负号，IRT 惯例 p=σ(θ-b)）、小样本向 0 收缩；
   n 按不同学生计数（重做不刷满门槛）。
3. 能力与选题：强生 θ>0 / 弱生 θ<0；门槛按**不同标定题数**≥5；标定池 Fisher
   信息优先、未达门槛退回难度先验（降级规则在选题服务内部）。
4. 接线：diagnose payload 带 mastery；assign 的 θ 定向写入 audit（str 词汇表）。
"""
import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import Base, Question, Student
from app.services import assessment_service as assess
from app.services import mastery_service as mastery


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(Student(student_id="s-1", name="测试生", class_id="cls-1", status="approved"))
    session.add(Student(student_id="s-2", name="学霸", class_id="cls-1", status="approved"))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _add_answer(db, student_id, question_id, correct, kp="氧化还原", idx=0):
    from datetime import datetime, timedelta
    db.add(_StudentAnswer(
        answer_id=f"ans-{student_id}-{question_id}-{idx}",
        student_id=student_id, question_id=question_id,
        student_answer="x", is_correct=correct,
        source=_AnswerSource.ONLINE, confirmed=True,
        answered_at=datetime(2026, 9, 1) + timedelta(days=idx)))
    db.commit()


def _seed_calibrated(db, qid, n, correct_ratio):
    """n 条作答、n 个**不同学生**（批次 C 审查 P1-6：门槛按不同学生计数）。"""
    n_correct = int(n * correct_ratio)
    for i in range(n):
        db.add(_StudentAnswer(
            answer_id=f"cal-{qid}-{i}", student_id=f"sc-{i}", question_id=qid,
            student_answer="x", is_correct=(i < n_correct),
            source=_AnswerSource.ONLINE, confirmed=True))
    db.commit()


# 模块级别名：让上面的 helper 与各测试的局部导入保持同源
from app.models.database import StudentAnswer as _StudentAnswer  # noqa: E402
from app.models.database import AnswerSource as _AnswerSource  # noqa: E402


# ── BKT 数学 ────────────────────────────────────────────────────────────

def test_bkt_all_wrong_low_mastery():
    assert mastery.bkt_sequence([False] * 6) < 0.3


def test_bkt_improving_sequence_rises():
    low = mastery.bkt_sequence([False, False, False])
    high = mastery.bkt_sequence([False, False, False, True, True, True])
    assert high > low + 0.2


def test_bkt_all_right_high_mastery():
    assert mastery.bkt_sequence([True] * 5) > 0.8


def test_mastery_skips_insufficient_samples(db_session):
    _add_answer(db_session, "s-1", "q-1", False, idx=0)
    _add_answer(db_session, "s-1", "q-1", False, idx=1)  # 只有 2 条
    result = mastery.mastery_for_student(db_session, "s-1")
    assert result == []  # 样本不足的知识点不下结论


def test_mastery_orders_weakest_first(db_session):
    # 氧化还原全错、化学平衡全对 → 薄弱在前（mastery 查询 join questions，行要建）
    for i in range(4):
        db_session.add(Question(question_id=f"redox-{i}", content="题干",
                                knowledge_points=["氧化还原"]))
        db_session.add(Question(question_id=f"eq-{i}", content="题干",
                                knowledge_points=["化学平衡"]))
    db_session.commit()
    for i in range(4):
        _add_answer(db_session, "s-1", f"redox-{i}", False, kp="氧化还原", idx=i)
    for i in range(4):
        _add_answer(db_session, "s-1", f"eq-{i}", True, kp="化学平衡", idx=i)
    result = mastery.mastery_for_student(db_session, "s-1")
    assert [m["knowledge_point"] for m in result] == ["氧化还原", "化学平衡"]
    assert result[0]["mastery"] < 0.3 < result[1]["mastery"]
    assert result[0]["samples"] == 4


# ── IRT 标定 ────────────────────────────────────────────────────────────

def test_calibrate_easy_question_negative_b(db_session):
    """50 条作答（5 个学生）80% 正确 → 简单题，b 为负。
    n 按不同学生计数（批次 C 审查 P1-6：重做不刷满门槛）。"""
    for i in range(50):
        db_session.add(_StudentAnswer(
            answer_id=f"ca-{i}", student_id=f"s-{i % 5}", question_id="q-easy",
            student_answer="x", is_correct=(i % 10 < 8),
            source=_AnswerSource.ONLINE, confirmed=True))
    db_session.commit()
    n = assess.calibrate(db_session)
    assert n >= 1
    row = db_session.get(assess.QuestionCalibration, "q-easy")
    assert row.n_answers == 5
    assert row.b < 0  # 简单题


def test_calibration_shrinks_small_samples(db_session):
    """3 条全对的题 b 被收缩得接近 0（小样本不极端）。"""
    for i in range(3):
        db_session.add(_StudentAnswer(
            answer_id=f"cs-{i}", student_id="s-1", question_id="q-small",
            student_answer="x", is_correct=True,
            source=_AnswerSource.ONLINE, confirmed=True))
    db_session.commit()
    assess.calibrate(db_session)
    row = db_session.get(assess.QuestionCalibration, "q-small")
    # 全对 → b 为负（容易）；收缩后幅度远小于裸 logit(0.95)≈2.94
    assert -2.94 < row.b < 0
    assert abs(row.b) < assess._logit(0.95)


# ── 能力估计与选题 ──────────────────────────────────────────────────────

def _seed_calibrated_set(db, questions, correct_ratio=0.5):
    """一组题各 n 条作答（每行不同学生），全部可标定。"""
    for qid in questions:
        _seed_calibrated(db, qid, 32, correct_ratio)


def test_ability_strong_positive_weak_negative(db_session):
    questions = ("q-easy", "q-mid", "q-hard", "q-e2", "q-m2", "q-h2")
    _seed_calibrated_set(db_session, questions)
    assess.calibrate(db_session)
    # 学霸在 6 道不同标定题上全对（门槛按不同题数 ≥5）→ θ 为正
    for i, qid in enumerate(questions):
        db_session.add(_StudentAnswer(
            answer_id=f"strong-{i}", student_id="s-2", question_id=qid,
            student_answer="x", is_correct=True,
            source=_AnswerSource.ONLINE, confirmed=True))
    db_session.commit()
    theta = assess.ability(db_session, "s-2")
    assert theta is not None and theta > 0


def test_select_items_degrades_without_calibration(db_session):
    """无标定数据 → 全部退回难度先验（degraded=True），但仍出题。"""
    out = assess.select_items(db_session, "s-1", ["u1", "u2", "u3"], 2)
    assert out["degraded"] is True and out["theta"] is None
    assert len(out["items"]) == 2
    assert all(i["source"] == "prior" for i in out["items"])


def test_select_items_fisher_prefers_informative(db_session):
    """标定池内按 Fisher 信息选题：θ 低的学生，中等偏易题信息量更大。"""
    questions = ("q-hard", "q-mid", "q-easy2", "q-easy", "q-e3")
    for i, qid in enumerate(questions):
        _seed_calibrated(db_session, qid, 32, (0.15, 0.55, 0.9, 0.9, 0.6)[i])
    assess.calibrate(db_session)
    # 弱生作答序列：5 道不同标定题基本全错（过 ≥5 题门槛）→ θ < 0
    wrong_on = ("q-hard", "q-hard", "q-mid", "q-easy2", "q-easy", "q-e3")
    for i, qid in enumerate(wrong_on):
        db_session.add(_StudentAnswer(
            answer_id=f"weak-{i}", student_id="s-1", question_id=qid,
            student_answer="x", is_correct=False,
            source=_AnswerSource.ONLINE, confirmed=True))
    db_session.commit()
    theta = assess.ability(db_session, "s-1")
    assert theta is not None and theta < 0

    out = assess.select_items(db_session, "s-1", list(questions), 2)
    sources = {i["question_id"]: i["source"] for i in out["items"]}
    # θ≈-2.7 的弱生：最易题（b 最负、离 θ 最近）信息量最大，难题（b>0）最小
    assert sources.get("q-easy2") == "calibrated"
    assert sources.get("q-easy") == "calibrated"
    assert "q-hard" not in sources  # 最小信息量的题落选
    assert out["degraded"] is False


# ── 接线 ────────────────────────────────────────────────────────────────

def test_diagnose_payload_contains_mastery(db_session, tmp_path, monkeypatch):
    """诊断 payload 带 mastery 列表（薄弱在前，样本门槛同服务层）。
    diagnose_barrier 是 agent 工具、经 get_db 读默认引擎（影子库）——种子数据
    与断言都落在影子会话上。"""
    from sqlalchemy.orm import Session as _Session
    from app.models.database import (Account, Class, Teacher,
                                     TeacherClassSubject, engine)
    monkeypatch.setenv("CHEMAI_WORKSPACE", str(tmp_path / "ws"))
    Base.metadata.create_all(engine)
    shadow = _Session(bind=engine)
    try:
        if shadow.get(Account, "acc-dbg") is None:
            shadow.add(Account(account_id="acc-dbg", role="teacher",
                               username="dbg-t-1", password_hash="x",
                               teacher_id="t-1", status="active"))
        if shadow.get(Teacher, "t-1") is None:
            shadow.add(Teacher(teacher_id="t-1", name="调试教师", school_id="sch-1",
                               status="approved"))
        if shadow.get(Class, "cls-1") is None:
            shadow.add(Class(class_id="cls-1", name="调试班", grade="高一", subject="化学"))
        if shadow.query(TeacherClassSubject).filter(
                TeacherClassSubject.teacher_id == "t-1",
                TeacherClassSubject.class_id == "cls-1").first() is None:
            shadow.add(TeacherClassSubject(id="tcs-dbg", teacher_id="t-1",
                                           class_id="cls-1", subject="化学"))
        shadow.add(Student(student_id="900001", name="诊断生", class_id="cls-1",
                           status="approved"))
        for i in range(4):
            shadow.add(Question(question_id=f"dbg-redox-{i}", content="题干",
                                knowledge_points=["氧化还原"]))
            _add_answer(shadow, "900001", f"dbg-redox-{i}", False, kp="氧化还原", idx=i)
        shadow.commit()

        from agent.tools import context as ctx
        from agent.tools.diagnosis import diagnose_barrier
        ctx.set_current_identity("t-1", "teacher")
        out = json.loads(asyncio.run(diagnose_barrier(student_id="900001")))
        assert "mastery" in out
        assert out["mastery"][0]["knowledge_point"] == "氧化还原"
        # _component.params 同步携带（面板渲染源）
        assert out["_component"]["params"]["mastery"] == out["mastery"]
    finally:
        shadow.close()


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


def test_theta_to_difficulty_mapping():
    assert assess._theta_to_difficulty(-3) == 1
    assert assess._theta_to_difficulty(0) == 3
    assert assess._theta_to_difficulty(3) == 5


def test_theta_to_zpd_vocabulary():
    assert assess.theta_to_zpd(-3) == "easy"
    assert assess.theta_to_zpd(0) == "medium"
    assert assess.theta_to_zpd(3) == "hard"


# ── 批次 C 审查修复的接线回归钉 ─────────────────────────────────────────

def test_adaptive_theta_targets_zpd(db_session, monkeypatch):
    """P0-2 回归钉：θ 定向走 str 词汇表——audit 的 zpd_difficulty 是
    easy/medium/hard 之一、ability_theta 记录在案。"""
    from app.services import assignment_service as asvc
    questions = ("q-easy", "q-b", "q-c", "q-d", "q-e", "q-f")
    _seed_calibrated_set(db_session, questions, 0.6)
    assess.calibrate(db_session)
    # s-1 在 ≥5 道不同标定题上全对 → θ > 0
    for i, qid in enumerate(questions):
        db_session.add(_StudentAnswer(
            answer_id=f"tz-{i}", student_id="s-1", question_id=qid,
            student_answer="x", is_correct=True,
            source=_AnswerSource.ONLINE, confirmed=True))
    db_session.commit()

    captured = {}
    monkeypatch.setattr(asvc, "_llm_generate",
                        lambda kps, zpd, count, rag_context=None, question_types=None:
                        captured.update(zpd=zpd) or
                        [{"content": "题", "answer": "A",
                          "knowledge_points": kps, "difficulty": zpd}])
    monkeypatch.setattr(asvc, "_audit_gate", lambda raw: (list(raw), []))
    monkeypatch.setattr(asvc, "_bank_find", lambda kps, zpd, limit: [])

    student = db_session.get(Student, "s-1")
    out = asvc._assign_one(db_session, student,
                           {"kind": "adaptive", "question_count": 2})
    assert "error" not in out, out
    audit = out.get("audit") or {}
    assert audit.get("ability_theta") is not None
    assert audit["zpd_difficulty"] in ("easy", "medium", "hard")
    assert captured["zpd"] == audit["zpd_difficulty"]  # LLM 拿到的也是 str 词汇表


def test_bank_branch_has_no_question_ids_dependency(db_session):
    """P0-1 回归钉：bank 源不依赖 question_ids 键（原 CAT no-op 块已删，
    布置不得因此崩溃或产出空卷）。"""
    from app.services import assignment_service as asvc
    student = db_session.get(Student, "s-1")
    out = asvc._assign_one(db_session, student,
                           {"kind": "bank", "knowledge_points": ["氧化还原"],
                            "difficulty": "medium", "question_count": 3})
    assert "error" not in out or "无" in str(out)  # 检索为空也不崩


def test_select_items_mixed_candidates_degraded_semantics(db_session):
    """P1-4 语义：混合候选里标定项入选 → degraded=False（标定项照用）；
    标定项恒排未标定项前是显式设计决策。"""
    questions = ("q-cal", "q-cal-b", "q-cal-c", "q-cal-d", "q-cal-e")
    _seed_calibrated_set(db_session, questions, 0.5)
    assess.calibrate(db_session)
    for i, qid in enumerate(questions):
        db_session.add(_StudentAnswer(
            answer_id=f"mx-{i}", student_id="s-1", question_id=qid,
            student_answer="x", is_correct=(i % 2 == 0),
            source=_AnswerSource.ONLINE, confirmed=True))
    db_session.commit()

    out = assess.select_items(db_session, "s-1",
                              list(questions) + ["q-uncal", "q-uncal2"], 3)
    assert out["degraded"] is False  # 有标定项入选就不算降级
    assert all(i["source"] == "calibrated" for i in out["items"])  # 标定项恒在前


def test_select_items_empty_count(db_session):
    """P2-11：count<=0 返回空，不出 1 题。"""
    out = assess.select_items(db_session, "s-1", ["q-1"], 0)
    assert out["items"] == []
