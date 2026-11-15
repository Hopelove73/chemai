"""学习技能 evals 基线（ADR-0029 批⑥，spec #24）。

fast 三断言组（确定性，CI 门禁）：
1. registry_surface —— 工具注册面（META personas + persona yaml 白名单交集；
   回退白名单本条即红，spec #24 验收 2 的断言面自证）；
2. handoff_payloads —— 接棒载荷形状（review 接棒 / smart 整会话 / variant 归属拒绝）；
3. parent_weekly_summary —— 三源拼装形状 + 未绑定拒绝。
轨迹类（真模型工具选择）在 agent_eval_golden.yaml 慢层场景，本文件不含。
"""
import asyncio
import json
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from evals.conftest import EvalReport


def _patch_get_db(db):
    """工具内 next(get_db()) 指向本测试的内存库（conftest.patch_get_db 同款）。

    run_evals 的影子 activate 只保证「不写生产件」，不保证「读到测试种子」——
    工具读库必须与种子同库，断言才确定。"""
    from app.models import database as db_mod

    def _fake():
        yield db

    db_mod.get_db = _fake


def _memory_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.models.database import Base
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def _seed_student(db, sid="202610101", kps=None):
    from app.models.database import (
        ExamRecord, Question, QuestionSource, AuditStatus, Difficulty, RecordType,
        Student,
    )
    db.add(Student(student_id=sid, name="测试学生", class_id="class_2025_1"))
    db.add(ExamRecord(record_id="ls_rec", class_id="class_2025_1", name="题源",
                      type=RecordType.PRACTICE, exam_date=datetime.utcnow()))
    for i, kp in enumerate(kps or ["化学平衡"]):
        db.add(Question(question_id=f"ls_q{i}", record_id="ls_rec", content=f"题{i}",
                        options=["A", "B"], answer="A", knowledge_points=[kp],
                        difficulty=Difficulty.MEDIUM,
                        source=QuestionSource.MANUAL_SELECTED,
                        audit_status=AuditStatus.PASSED))
    db.commit()


def test_registry_surface():
    """工具注册面 = META personas 与 persona yaml 白名单交集双双在位。"""
    import pathlib

    import yaml

    from agent.tools import TOOL_META
    meta = next(m for t, m in TOOL_META.items() if t.__name__ == "invoke_learning_skill")
    checks = []
    checks.append(("META student/parent 面", set(meta["personas"]) == {"student", "parent"}))
    checks.append(("META teacher 不接（ADR-0029）", "teacher" not in meta["personas"]
                   and "tutor" not in meta["personas"]))

    base = pathlib.Path(__file__).resolve().parent.parent / "agent" / "personas"
    for persona, expect in (("student", True), ("parent", True),
                            ("teacher", False), ("tutor", False)):
        cfg = yaml.safe_load((base / f"{persona}.yaml").read_text(encoding="utf-8"))
        checks.append((f"yaml {persona} 白名单{'含' if expect else '不含'}",
                       ("invoke_learning_skill" in cfg.get("available_skills", [])) == expect))
    return [{"name": n, "passed": ok, "details": ""} for n, ok in checks]


def test_handoff_payloads():
    """接棒载荷形状：review 接棒 / smart 整会话 / variant 归属拒绝（契约页）。"""
    from agent.tools.context import set_current_identity
    from app.models.database import (
        MemoryCard, StudentAnswer,
    )
    from app.services.learning_skills import dispatch

    db = _memory_db()
    _seed_student(db)
    db.add(MemoryCard(student_id="202610101", question_id="ls_q0",
                      due=datetime.utcnow() - timedelta(hours=1)))
    db.commit()

    out = []

    def persona_call(skill, params, role="student", user_id="202610101"):
        set_current_identity(user_id, role)
        try:
            return dispatch(db, skill, params, {"user_id": user_id, "role": role})
        finally:
            set_current_identity(None)

    # review：有到期卡 → 接棒 /m/review
    r = persona_call("review_today", {})
    out.append(("review 接棒 /m/review",
                r.get("_route", {}).get("page") == "/m/review" and r["due_count"] >= 1,
                str(r)[:160]))
    # review：无卡 → 无接棒（空库新学生）
    db2 = _memory_db()
    _seed_student(db2, sid="202610102")
    set_current_identity("202610102", "student")
    try:
        r2 = dispatch(db2, "review_today", {}, {"user_id": "202610102", "role": "student"})
    finally:
        set_current_identity(None)
    out.append(("review 无卡不接棒", "_route" not in r2 and r2["due_count"] == 0,
                str(r2)[:120]))

    # smart：整会话接棒（practice_id + 首题 + smartSession 载荷）
    r = persona_call("smart_practice", {"kps": ["化学平衡"]})
    route = r.get("_route", {})
    ss = route.get("state", {}).get("smartSession") or {}
    out.append(("smart 整会话接棒 /m/practice",
                route.get("page") == "/m/practice"
                and bool(ss.get("practice_id")) and bool(ss.get("question")),
                str(r)[:160]))

    # variant：非本人错题 → 归属拒绝（不触碰 service）
    from app.models.database import Parent, StudentParentBinding  # noqa: F401
    r = persona_call("variant_training", {"question_id": "not_mine_q"})
    out.append(("variant 归属拒绝", "error" in r and "自己的错题" in r["error"],
                str(r)[:120]))

    # mastery：无数据诚实降级
    r = persona_call("mastery_report", {})
    out.append(("mastery 无数据降级", "还没有掌握度数据" in r.get("text", ""),
                str(r)[:120]))
    return [{"name": f"handoff: {n}", "passed": ok, "details": d} for n, ok, d in out]


def test_parent_weekly_summary_shape():
    """家长周报：三源拼装形状 + 未绑定拒绝（ADR-0029 批③）。"""
    from agent.tools.context import set_current_identity
    from app.models.database import (
        Parent, StudentAnswer, StudentParentBinding,
    )

    db = _memory_db()
    _seed_student(db, kps=["盐类水解"])
    db.add(Parent(parent_id="p-1", name="周建国", password_hash="x"))
    db.add(StudentParentBinding(binding_id="b-1", student_id="202610101",
                                parent_id="p-1", status="active", bind_code="x"))
    db.add(StudentAnswer(answer_id="ls_a0", student_id="202610101",
                         question_id="ls_q0", student_answer="B",
                         is_correct=False, confirmed=True))
    # 消灭率数据面需过 mastery 样本门槛（MIN_SAMPLES=3/知识点）
    for i in (1, 2):
        db.add(StudentAnswer(answer_id=f"ls_a{i}", student_id="202610101",
                             question_id="ls_q0", student_answer="B",
                             is_correct=False, confirmed=True))
    db.commit()

    from agent.tools.parent import parent_weekly_summary

    def parent_call(student_name=""):
        set_current_identity("p-1", "parent")
        _patch_get_db(db)
        try:
            return json.loads(asyncio.run(parent_weekly_summary(student_name)))
        finally:
            set_current_identity(None)

    out = []
    r = parent_call("测试学生")
    out.append(("text 拼齐三卡+消灭率+一件事",
                "未完成" in r.get("text", "") and "消灭" in r.get("text", "")
                and "能帮上的一件事" in r.get("text", ""),
                str(r)[:200]))
    out.append(("cards 三卡齐", set(r.get("cards", {})) ==
                {"unfinished", "due_soon", "week_activity"}, str(r.get("cards"))[:120]))
    out.append(("elimination 数据面", r.get("elimination", {}).get("total") >= 1,
                str(r.get("elimination"))[:80]))
    # 未绑定 → 拒绝不泄漏
    r2 = _parent_call_raw("陌生人", db=db)
    out.append(("未绑定拒绝", "error" in r2 and "绑定" in r2["error"], str(r2)[:120]))
    return [{"name": f"weekly: {n}", "passed": ok, "details": d} for n, ok, d in out]


def _parent_call_raw(student_name, db=None):
    import asyncio
    from agent.tools.context import set_current_identity
    from agent.tools.parent import parent_weekly_summary
    set_current_identity("p-none", "parent")
    if db is not None:
        _patch_get_db(db)
    try:
        return json.loads(asyncio.run(parent_weekly_summary(student_name)))
    finally:
        set_current_identity(None)


def run():
    report = EvalReport()
    print("=== 学习技能基线（ADR-0029 批⑥） ===")
    for r in test_registry_surface():
        report.add(f"learning: {r['name']}", r["passed"], r.get("details", ""))
    for r in test_handoff_payloads():
        report.add(f"learning: {r['name']}", r["passed"], r.get("details", ""))
    for r in test_parent_weekly_summary_shape():
        report.add(f"learning: {r['name']}", r["passed"], r.get("details", ""))
    print(report.summary())
    return report


if __name__ == "__main__":
    run()
