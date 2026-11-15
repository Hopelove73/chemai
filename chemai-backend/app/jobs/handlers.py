"""后台作业处理器（handler 面）——「作业调度边界」的消费侧。

这里的函数是幂等、同步、可脱离队列单独测试的；Celery task 只是它们的薄包装。
"""
import logging
import re
import json as _json
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.database import (
    Student, ExamRecord, StudentAnswer, Question, BarrierType, get_db,
)


def _diagnose_barrier_answers(db: Session, wrong_answers, source_id: str = "") -> dict:
    """对一批错答跑 LLM 障碍判定并重算相关学生的 barrier 分布（幂等聚合）。

    exam 场景与学生场景（chat 作答无 exam 归属）共用的核心。
    source_id: 本次诊断的来源标识（考试/练习记录 id），用于学生端通知的幂等键。
    """
    from app.services.llm_service import llm_service
    import concurrent.futures

    analyzed, failed = 0, 0
    barrier_dist = {"concept": 0, "reading": 0, "expression": 0}
    updated_students = set()

    def diagnose_single(ans):
        nonlocal analyzed, failed
        question = db.query(Question).filter(
            Question.question_id == ans.question_id
        ).first()
        if question is None:
            return

        q_content = question.content or ""
        correct = question.answer or ""
        student_ans = ans.student_answer or ""

        result = llm_service.diagnose_barrier_type(
            student_error_history=[{
                "question": q_content[:300],
                "wrong_answer": student_ans,
                "correct_answer": correct,
            }],
            question_content=q_content[:500],
            student_answer=student_ans,
            correct_answer=correct,
        )

        if result.get("success"):
            content = result.get("content", "{}")
            _json_match = re.search(r'\{[\s\S]*\}', content)
            if _json_match:
                try:
                    data = _json.loads(_json_match.group())
                    bt = data.get("barrier_type", "concept")
                    if bt in ("concept", "reading", "expression"):
                        ans.barrier_type = getattr(BarrierType, bt.upper(), BarrierType.CONCEPT)
                        analyzed += 1
                except _json.JSONDecodeError:
                    failed += 1
            else:
                failed += 1
        else:
            failed += 1

    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
        list(executor.map(lambda a: diagnose_single(a), wrong_answers))

    db.commit()

    for ans in wrong_answers:
        if ans.barrier_type and ans.student_id:
            updated_students.add(ans.student_id)
            bt = ans.barrier_type.value
            barrier_dist[bt] = barrier_dist.get(bt, 0) + 1

    for sid in updated_students:
        student = db.query(Student).filter(Student.student_id == sid).first()
        if student:
            all_answers = db.query(StudentAnswer).filter(
                StudentAnswer.student_id == sid,
                StudentAnswer.is_correct.is_(False),
                StudentAnswer.barrier_type.isnot(None),
            ).all()
            counts = {"concept": 0, "reading": 0, "expression": 0}
            for a in all_answers:
                if a.barrier_type:
                    counts[a.barrier_type.value] = counts.get(a.barrier_type.value, 0) + 1
            total = sum(counts.values()) or 1
            student.barrier_type = {k: round(v / total, 2) for k, v in counts.items()}
            student.barrier_last_updated = datetime.utcnow()
            for bt in ("concept", "reading", "expression"):
                if bt not in student.barrier_type:
                    student.barrier_type[bt] = 0.0
            # 诊断完成 → 学生端通知（幂等键=来源+学生）
            # （barrier_last_updated 上面已经赋过，这里不再重复）
            # 合并说明：原写死 `diag_{exam_record_id}_{sid}`，但 exam_record_id 不在本函数
            # 作用域内（exam 与学生场景共用）。改成由调用方透传 source_id；没有来源
            # （chat 作答触发的诊断）时按天去重。
            _notify_key = (f"diag_{source_id}_{sid}" if source_id
                           else f"diag_{sid}_{datetime.utcnow():%Y%m%d}")
            try:
                from app.services.notification_service import notification_service
                notification_service.send_to_student(
                    sid,
                    "diagnosis",
                    "🧠 学习诊断已更新",
                    content="本次练习的错因分析完成，已更新你的学习画像与薄弱点",
                    idempotency_key=_notify_key,
                )
            except Exception:
                pass  # 通知失败不阻断诊断主流程

    db.commit()

    return {"analyzed_count": analyzed, "failed_count": failed,
            "barrier_distribution": barrier_dist}


def diagnose_student_recent(db: Session, student_id: str, source: str = None) -> dict:
    """学生级诊断（P5 对话回流）：该生最近的错答（含 chat 等无 exam 归属作答）。

    source: 可选来源过滤（online/ocr/manual/chat/training）——读侧按来源
    聚合/排除的能力预留（如教学上决定对话作答不参与诊断时切到 source 参数）。
    """
    from app.models.database import AnswerSource
    from app.services.grading_center import effective_conditions
    wrong = db.query(StudentAnswer).filter(
        StudentAnswer.student_id == student_id,
        StudentAnswer.is_correct.is_(False),
        effective_conditions(),
    )
    if source:
        wrong = wrong.filter(StudentAnswer.source == AnswerSource(source))
    wrong = wrong.order_by(
        StudentAnswer.barrier_type.is_(None).desc(),  # 未诊断优先
        StudentAnswer.answered_at.desc(),
    ).limit(10).all()
    if not wrong:
        return {"student_id": student_id, "analyzed_count": 0, "failed_count": 0,
                "barrier_distribution": {"concept": 0, "reading": 0, "expression": 0}}
    out = _diagnose_barrier_answers(db, wrong)
    out["student_id"] = student_id
    return out


def scan_semantic_pending(db: Session, exam_record_id: str = None) -> dict:
    """扫描待语义判定的主观题作答（PENDING_SEMANTIC），判定后覆盖落定。

    Celery 作业与 APScheduler 周期作业共用；判定失败保留标记下轮重扫（ADR-0018）。
    """
    from sqlalchemy import and_, or_

    from app.services.grading_center import PENDING_REASON, judge_semantic
    from app.models.database import Question

    q = db.query(StudentAnswer).filter(
        StudentAnswer.grade_reason == PENDING_REASON,
    )
    if exam_record_id:
        q = q.filter(StudentAnswer.exam_record_id == exam_record_id)
    # 不可判的行（既没随行答案键、题目也没标准答案）不进扫描窗口：窗口是 LIMIT 50 且
    # 按 rowid 稳定排序，攒够 50 条不可判行就会把窗口占满，新落的主观题永远轮不到
    q = q.outerjoin(Question, Question.question_id == StudentAnswer.question_id).filter(
        or_(StudentAnswer.answer_key.isnot(None),
            and_(Question.answer.isnot(None), Question.answer != ""))
    )
    pending = q.order_by(StudentAnswer.answered_at).limit(50).all()
    if not pending:
        return {"scanned": 0, "judged": 0}

    by_id = {}
    for ans in pending:
        if ans.question_id not in by_id:
            qrow = db.query(Question).filter(
                Question.question_id == ans.question_id).first()
            by_id[ans.question_id] = qrow

    unresolved, meta = [], []
    for ans in pending:
        qrow = by_id.get(ans.question_id)
        # 优先用落行时真正用的答案键（老师粘贴 > 题库）：拿题库草稿重判会给出伪定论
        correct = (ans.answer_key or "").strip() or ((qrow.answer or "").strip() if qrow else "")
        if not correct:
            continue
        unresolved.append({
            "question_id": ans.question_id,
            "student_answer": ans.student_answer or "",
            "correct_answer": correct,
            "is_correct": False, "reason": "", "needs_semantic": True,
        })
        meta.append(ans)

    judge_semantic(unresolved)
    judged = 0
    touched = set()
    for ans, q in zip(meta, unresolved):
        if q.get("needs_semantic") or q.get("judge_failed"):
            continue  # 判定失败：保留 PENDING 标记下轮重扫
        ans.is_correct = bool(q["is_correct"])
        ans.grade_reason = (q["reason"] or "")[:256] or None
        judged += 1
        if ans.exam_record_id:
            touched.add((ans.exam_record_id, ans.student_id))
        # 记忆卡同步（批次 B P1-1 回收路径）：主观题建卡在判定前被拦，
        # 这里是判定落定的时刻——判对回收未复习卡/复习一次，判错确保有卡
        try:
            from app.services import memory_card_service as mcs
            mcs.sync_card_from_judgment(db, ans.student_id, ans.question_id,
                                        ans.is_correct)
        except Exception:  # noqa: BLE001
            import logging
            logging.getLogger(__name__).warning(
                "memory card sync after semantic judge failed", exc_info=True)
    db.commit()

    # 判定落定后：逐生补「判卷完成」通知（幂等键与同步落库共用，重放不重复）+ 诊断
    from app.services.grading_center import notify_graded
    for exam_record_id_, _sid in touched:
        notify_graded(db, _sid, exam_record_id_)
    db.commit()
    for exam_record_id_, _sid in touched:
        diagnose_exam(db, exam_record_id_)  # 同模块函数，调用时已定义
    return {"scanned": len(pending), "judged": judged}


def handle_semantic_scan(exam_record_id: str = None) -> dict:
    """扫描作业入口：自开会话（幂等，可被队列/调度器重复触发）。"""
    db_gen = get_db()
    db = next(db_gen)
    try:
        return scan_semantic_pending(db, exam_record_id)
    finally:
        db.close()


def handle_diagnosis_student(student_id: str) -> dict:
    """学生级诊断作业入口：自开会话（幂等，可被队列重复投递）。"""
    db_gen = get_db()
    db = next(db_gen)
    try:
        return diagnose_student_recent(db, student_id)
    finally:
        db.close()


def diagnose_exam(db: Session, exam_record_id: str) -> dict:
    """对一场考试的错误答案运行 LLM 障碍诊断，并重算 Student.barrier_type。

    幂等：重复执行只重算聚合（不增量），不产生重复副作用。
    返回 dict：{exam_id, analyzed_count, failed_count, barrier_distribution, error?}
    """
    exam_record = db.query(ExamRecord).filter(
        ExamRecord.record_id == exam_record_id
    ).first()
    if exam_record is None:
        return {
            "exam_id": exam_record_id, "analyzed_count": 0, "failed_count": 0,
            "barrier_distribution": {"concept": 0, "reading": 0, "expression": 0},
            "error": "exam_not_found",
        }

    from app.services.grading_center import effective_conditions
    wrong_answers = db.query(StudentAnswer).filter(
        StudentAnswer.exam_record_id == exam_record_id,
        StudentAnswer.is_correct.is_(False),
        effective_conditions(),
    ).order_by(
        StudentAnswer.barrier_type.is_(None).desc()  # 未诊断的优先
    ).limit(10).all()  # 最多分析10个

    if not wrong_answers:
        return {
            "exam_id": exam_record_id, "analyzed_count": 0, "failed_count": 0,
            "barrier_distribution": {"concept": 0, "reading": 0, "expression": 0},
        }

    out = _diagnose_barrier_answers(db, wrong_answers, source_id=exam_record_id)
    return {
        "exam_id": exam_record_id,
        "analyzed_count": out["analyzed_count"],
        "failed_count": out["failed_count"],
        "barrier_distribution": out["barrier_distribution"],
    }


def handle_diagnosis(exam_record_id: str) -> dict:
    """诊断作业入口：自开会话并运行诊断（幂等，可被队列重复投递）。"""
    db_gen = get_db()
    db = next(db_gen)
    try:
        return diagnose_exam(db, exam_record_id)
    finally:
        db.close()




# ═══════════════════ 确认门过期作业（T17 / gate-lifecycle 批次 3）═══════════════════
#
# 三件事一起管（「门与 run 一致性一起管」）：
#   ① 收敛**假门**（按判据不算数却还挂着 open 的行）
#   ② 作废**超期未决**的开门
#   ③ 对账卡死的 `plan_runs`
#
# 为什么要 ②：门开着就**全拦**（C1-Q3，不可逆动作一律先要老师点头），而没有任何一条
# 路径会替老师放弃 —— 一张没人点过的卡片能把那个会话**永久**锁住。`T4` 的假门收敛
# 管的是"按判据不算数"的行，管不了"算数但没人处理"的行：**两件事，两套判据**。
#
# 为什么要 ③：门被作废之后，那些「等这张卡」的计划步就永远等不到结果了 —— 界面上
# 表现成永远转圈的计划卡。收了门不收 run，只解决一半。

logger = logging.getLogger(__name__)

# 超期阈值。14 天来自 `docs/designs/gate-product-closure.md` 的 T17 验收
# （「超 14 天的 open 门为 0」）。挑这个数的理由：够长，不会把老师手上正在处理的
# 卡片收掉；够短，一张被遗忘的卡不会把会话锁到下个季度。
GATE_EXPIRY_DAYS = 14


def expire_gate(db: Session, days: int = GATE_EXPIRY_DAYS) -> dict:
    """超期未决的开门 → `superseded`（T17「过期执行者」）。

    **逐扇一个工作单元**：一次失败不连累其余（T17 验收「两扇中一扇失败另一扇仍完成」）。
    每条作废都留日志行 —— 事后问"这张卡怎么没了"要查得到。
    """
    from datetime import datetime, timedelta

    from app.models.database import AgentGate
    from app.services.gate_service import gate_service
    from app.services.work_unit import new_work_unit

    cutoff = datetime.utcnow() - timedelta(days=days)
    # **先把要用的字段取成普通值**，再逐扇处理：每扇门开一个工作单元，而工作单元
    # 退出时会关掉它自己那条会话 —— 继续懒读 `row.xxx` 就踩 DetachedInstanceError
    # （本会话实测：一次收两扇时第二扇就炸）。同时这也让"逐扇失败不连累其余"
    # 真的成立：失败路径要记日志，日志里要的字段此刻已经在手上了。
    todo = [(gid, tool, opened) for gid, tool, opened in
            (db.query(AgentGate.gate_id, AgentGate.tool_name, AgentGate.last_opened_at)
             .filter(AgentGate.status == "open", AgentGate.last_opened_at < cutoff)
             .order_by(AgentGate.last_opened_at.asc())
             .all())]
    expired, failed = [], []
    for gid, tool, opened in todo:
        try:
            with new_work_unit() as wdb:
                ok = gate_service.close_gate(wdb, gid, "superseded")
        except Exception as e:  # noqa: BLE001
            logger.warning("gate expiry failed gate=%s: %s", gid, e)
            failed.append(gid)
            continue
        if ok:
            expired.append(gid)
            logger.info("gate expired gate=%s tool=%s opened_at=%s (>%d 天未决)",
                        gid, tool, opened, days)
        else:
            failed.append(gid)
    return {"expired": expired, "failed": failed, "threshold_days": days}


def supersede_stale_gates_full(db: Session) -> int:
    """全库收敛假门（T4 那个收敛器的全库那一次）。返回收敛行数。"""
    from app.services.gate_service import gate_service
    from app.services.work_unit import new_work_unit

    with new_work_unit() as wdb:
        n = gate_service.supersede_stale_gates(wdb)
    if n:
        logger.info("stale gates superseded: %d", n)
    return n


def reconcile_plan_runs(db: Session) -> dict:
    """卡死的 `plan_runs` 对账：把"永远等不到结果"的步骤收到终态。

    **只收会话没有算数开门（open / in_doubt）撑着的 run。** 那扇门还在的时候，
    那一步是真在等老师（in_doubt 尤其不能替老师裁定：动作跑没跑过无从判断），
    动它就是把一个还能继续的任务提前判死。门刚被上面两步收掉的那些会话，
    这一步正好接上 —— 顺序不能反。

    落库走 `plan_run_service.project_transitions`（仓库收权：调用面只算转移，
    投影与持久化走这一个入口）。**它 `except Exception: pass`**（不阻断 SSE 投影），
    所以这里回读校验一遍：它说"做了"不算数。
    """
    from app.models.database import PlanRun
    from app.services.gate_service import gate_service
    from app.services.plan_run_service import _STEP_TERMINAL, project_transitions

    # 这一步永远等不到结果时，记哪个终态：卡在执行中断的记 failed（它确实试过了），
    # 其余记 cancelled（从没开始，或者它等的那张卡已经不在了）。
    settle = {"running": "failed", "awaiting_confirmation": "cancelled",
              "pending": "cancelled", "revising": "cancelled"}

    from app.models.database import AgentGate

    def _waiting_on_gate(cid: str) -> bool:
        if gate_service.get_open_gate(db, cid):
            return True
        return db.query(AgentGate.id).filter(
            AgentGate.conversation_id == cid,
            AgentGate.status == "in_doubt",
        ).first() is not None

    settled, skipped = [], 0
    for run in db.query(PlanRun).filter(PlanRun.status.in_(("running", "paused"))).all():
        if _waiting_on_gate(run.conversation_id):
            skipped += 1
            continue
        todo = [(s.get("step"), settle[s["status"]])
                for s in (run.steps or [])
                if s.get("status") in settle]
        if not todo:
            continue
        project_transitions(run.conversation_id, todo)
        settled.append((run.run_id, len(todo)))

    # 回读校验：project_transitions 吞异常，所以必须自己再看一眼
    unfinished = []
    for run in db.query(PlanRun).filter(PlanRun.status.in_(("running", "paused"))).all():
        left = [s.get("status") for s in (run.steps or [])
                if s.get("status") not in _STEP_TERMINAL]
        if left and not _waiting_on_gate(run.conversation_id):
            unfinished.append({"run_id": run.run_id, "left": left})
    if unfinished:
        logger.warning("plan run reconcile left %d run(s) unfinished: %s",
                       len(unfinished), unfinished[:5])
    return {"settled": settled, "skipped_waiting_on_gate": skipped,
            "unfinished": unfinished}


def handle_gate_expiry() -> dict:
    """确认门过期作业入口：自开会话（幂等，可被调度器重复触发）。

    幂等：门收成 `superseded` 之后不再满足 `status='open'`，下一轮自然跳过；
    run 的终态步骤同理。重复跑不会重复留痕。
    """
    db_gen = get_db()
    db = next(db_gen)
    try:
        stale = supersede_stale_gates_full(db)
        expired = expire_gate(db)
        runs = reconcile_plan_runs(db)
        return {"stale_superseded": stale, **expired, "runs_settled": len(runs["settled"]),
                "runs_waiting_on_gate": runs["skipped_waiting_on_gate"],
                "runs_unfinished": len(runs["unfinished"])}
    finally:
        db.close()
