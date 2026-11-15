"""家长摘要推送（批 3 P1 / Guardian Summary REQ-GDS-004）。

频率偏好 parents.summary_frequency：weekly 周日 20:00、daily 每日 08:00、off 不推
（Asia/Shanghai）。幂等键 = 周号/日期 + 学生 id，重复调度不重发；站内推送经
NotificationService.send_to_parent（外发网关由 dispatch 层的既有开关统一处理）。
摘要带深链参数（家长端通知卡点击跳详情）。
"""
from __future__ import annotations

from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")


def _local_now() -> datetime:
    return datetime.now(_TZ)


def run_weekly_summary() -> dict:
    return _run("weekly")


def run_daily_summary() -> dict:
    return _run("daily")


def _bulk_compose(db, student_ids: set[str], now: datetime) -> dict[str, tuple[str, str]]:
    """批量组装全部涉及学生的摘要文本（一次分组查询，不逐生全表扫）。

    生产体量（数百绑定 × 数万作答）下逐生 compose_summary_text 会退化成
    数百次全表扫——调度作业走这里的分组聚合；单生端点仍走 service 函数。"""
    from sqlalchemy import func

    from app.models.database import ExamRecord, RecordType, Student, StudentAnswer
    week_ago = now - timedelta(days=7)
    out: dict[str, tuple[str, str]] = {}
    if not student_ids:
        return out

    names = dict(db.query(Student.student_id, Student.name)
                 .filter(Student.student_id.in_(student_ids)).all())

    # 近 7 天按生按日聚合（一次 GROUP BY）
    rows = (db.query(StudentAnswer.student_id,
                     func.date(StudentAnswer.answered_at),
                     func.count(StudentAnswer.answer_id),
                     func.sum(func.coalesce(StudentAnswer.is_correct, 0)))
            .filter(StudentAnswer.student_id.in_(student_ids),
                    StudentAnswer.answered_at >= week_ago)
            .group_by(StudentAnswer.student_id, func.date(StudentAnswer.answered_at))
            .all())
    per: dict[str, dict[str, tuple[int, int]]] = {}
    for sid, day, total, correct in rows:
        per.setdefault(sid, {})[str(day)] = (int(total), int(correct or 0))

    # 未完成：近 30 天班级练习 - 该生已作答集合（一次查询拿全量映射）
    sids_students = db.query(Student).filter(Student.student_id.in_(student_ids)).all()
    open_recs = (db.query(ExamRecord.record_id, ExamRecord.class_id, ExamRecord.name)
                 .filter(ExamRecord.type == RecordType.PRACTICE,
                         ExamRecord.exam_date >= now - timedelta(days=30))
                 .order_by(ExamRecord.exam_date.desc()).limit(500).all())
    answered_pairs = set(db.query(StudentAnswer.student_id,
                                  StudentAnswer.exam_record_id)
                         .filter(StudentAnswer.student_id.in_(student_ids)).all())

    for sid in student_ids:
        name = names.get(sid, sid)
        days = per.get(sid, {})
        week_total = sum(t for t, _ in days.values())
        week_correct = sum(c for _, c in days.values())
        acc = round(week_correct / week_total * 100) if week_total else 0
        # 连击：从今天往回数有作答的日期；无今日则至少 1（原 service 同口径）
        from datetime import date as _date
        streak = 0
        cur = now.date()
        while str(cur) in days:
            streak += 1
            cur = _date.fromordinal(cur.toordinal() - 1)
        if streak == 0:
            streak = 1  # 原 service 同口径（无条件下限 1）
        cls = next((s.class_id for s in sids_students if s.student_id == sid), None)
        answered_here = {r for (s2, r) in answered_pairs if s2 == sid}
        unfinished = [r for (r, cid, _n) in open_recs
                      if cid == cls and r not in answered_here]
        lines = [
            f"本周练习 {week_total} 题，正确率 {acc}%",
            f"未完成任务 {len(unfinished)} 项",
            f"复习连击 {streak} 天",
        ]
        out[sid] = (f"【学情摘要】{name} 本周学习简报", chr(10).join(lines))
    return out


def _run(frequency: str) -> dict:
    from app.models.database import Parent, StudentParentBinding, get_db

    now = _local_now()
    period_key = (f"{now.isocalendar().year}-W{now.isocalendar().week:02d}"
                  if frequency == "weekly" else now.strftime("%Y-%m-%d"))

    db = next(get_db())
    sent = skipped = 0
    try:
        bindings = (db.query(StudentParentBinding)
                    .filter(StudentParentBinding.status == "active").all())
        parents = {p.parent_id: p for p in db.query(Parent).all()}
        targets = [(b.parent_id, b.student_id) for b in bindings
                   if ((parents.get(b.parent_id).summary_frequency
                        if parents.get(b.parent_id) else None) or "weekly") == frequency]
        texts = _bulk_compose(db, {sid for _p, sid in targets}, now)
        from app.services.notification_service import notification_service
        ntype = "weekly_report" if frequency == "weekly" else "daily_report"
        for parent_id, sid in targets:
            idem = f"guardian_{frequency}_{period_key}_{sid}"
            try:
                # 幂等预检：同周同生同频已投 → 跳过（重跑不重发，REQ-GDS-004）
                if notification_service.parent_notification_exists(ntype, idem, parent_id):
                    skipped += 1
                    continue
                title, content = texts.get(
                    sid, ("【学情摘要】本周学习简报", ""))
                title = f"{title}（查看详情 » /#/m/parent）"  # 深链钩子
                notification_service.send_to_parent(
                    parent_id, sid, notification_type=ntype,
                    title=title, content=content, idempotency_key=idem)
                sent += 1
            except Exception:
                skipped += 1
    finally:
        db.close()
    return {"frequency": frequency, "sent": sent, "skipped": skipped}
