"""家长摘要聚合（批 3 P1 / Guardian Summary spec design D1）。

三卡结构的唯一产出点：
- unfinished   未完成（未提交练习任务计数 + 明细前 3）
- due_soon     即将到期（截止日在未来 ≤3 天的未完成任务，按最近排序）
- week_activity 本周动态（正确率环比、完成数、复习连击）

另承接 get_child_weekly 的周聚合（原 API 函数体内的手搓逻辑迁入，
等价性快照测试护航——同数据同结果，API 变薄 adapter）。
刻意不放任何分数明细（Google Classroom 三段式：摘要不含成绩单）。
"""

from __future__ import annotations

from datetime import datetime, timedelta


def weekly_aggregate(db, student_id: str, days: int = 7) -> dict:
    """近 N 天周聚合（原 get_child_weekly 函数体逐行迁入，语义不变）。

    返回 WeeklyReportResponse 的字段 dict。streak 计数含原实现的宽容口径
    （无今日作答但有历史 → 至少 1）。"""
    from app.models.database import Question, Student, StudentAnswer

    student = db.query(Student).filter(Student.student_id == student_id).first()
    week_start = datetime.utcnow() - timedelta(days=days)
    total_answers = 0
    correct_answers = 0
    kp_error_count: dict = {}

    week_answers = db.query(StudentAnswer).filter(
        StudentAnswer.student_id == student_id,
        StudentAnswer.answered_at >= week_start,
    ).all()
    for ans in week_answers:
        total_answers += 1
        if ans.is_correct:
            correct_answers += 1
        else:
            q = db.query(Question).filter(Question.question_id == ans.question_id).first()
            if q and q.knowledge_points:
                for kp in q.knowledge_points:
                    kp_error_count[kp] = kp_error_count.get(kp, 0) + 1

    accuracy_rate = round(correct_answers / total_answers, 2) if total_answers > 0 else 0
    weak_kps = [kp for kp, _ in sorted(kp_error_count.items(), key=lambda x: -x[1])[:5]]
    if not weak_kps:
        weak_kps = ["暂无薄弱知识点数据"]

    streak_days = _streak_days(db, student_id)

    return {
        "student_id": student_id,
        "student_name": student.name if student else student_id,
        "week_start": week_start.strftime("%Y-%m-%d"),
        "week_end": datetime.utcnow().strftime("%Y-%m-%d"),
        "practice_count": total_answers,
        "practice_completed": correct_answers,
        "accuracy_rate": accuracy_rate,
        "weak_knowledge_points": weak_kps,
        "barrier_type": str(student.barrier_type.get("concept", 0.33)) if student and student.barrier_type else "未知",
        "streak_days": streak_days,
    }


def _streak_days(db, student_id: str) -> int:
    """连续练习天数（原 get_child_weekly 内联实现，含死代码的**有效**语义：
    首个 hasattr 分支即为有效路径，冗余分支从未改变结果——迁入时只保留有效逻辑）。"""
    from app.models.database import StudentAnswer

    answers_by_date = db.query(StudentAnswer).filter(
        StudentAnswer.student_id == student_id,
    ).order_by(StudentAnswer.answered_at.desc()).all()
    streak_set = set()
    for ans in answers_by_date:
        if ans.answered_at:
            d = ans.answered_at
            streak_set.add(d.date() if hasattr(d, "date") else d)
    streak = 0
    check_date = datetime.utcnow().date()
    while check_date in streak_set:
        streak += 1
        check_date -= timedelta(days=1)
    if streak == 0:
        streak = 1  # 原实现口径（无条件的兜底 1），等价性以旧实现为准
    return streak


def build_cards(db, student_id: str) -> dict:
    """三卡（REQ-GDS-001）：未完成 / 即将到期 / 本周动态。无分数明细。"""
    from app.models.database import Student, StudentAnswer

    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        return {}

    now = datetime.utcnow()
    week_ago = now - timedelta(days=7)
    two_weeks_ago = now - timedelta(days=14)

    # 三卡单一事实来源 = student_tasks 装配（practice.py 唯一实现，防口径分裂）；
    # 未完成/即将到期用同一集合的同一「未完成」判定（not submitted，仅 PRACTICE）
    # 任务装配唯一实现在 practice.student_tasks（口径单源优先于分层洁癖）；
    # 层级倒挂已知——搬迁会把 teacher 任务测试面一起动，留独立重构项
    from app.api.practice import student_tasks
    try:
        task_rows = student_tasks(db, student) or []
    except Exception:
        task_rows = []
    open_practice = [t for t in task_rows
                     if t.get("type") == "practice" and not t.get("submitted")]
    unfinished_items = [
        {"practice_id": t.get("practice_id"), "title": t.get("title") or "练习",
         "assigned_at": None}
        for t in open_practice[:3]
    ]

    due_soon = []
    for t in open_practice:
        deadline = t.get("deadline") or ""
        if not deadline:
            continue
        try:
            ddl = datetime.strptime(deadline[:10], "%Y-%m-%d")
        except ValueError:
            continue
        if now.date() <= ddl.date() <= now.date() + timedelta(days=3):
            due_soon.append({"title": t.get("title") or "任务",
                             "deadline": ddl.isoformat()})
    due_soon.sort(key=lambda x: x["deadline"])
    due_soon = due_soon[:3]

    # 本周动态：正确率环比 + 完成数 + 复习连击
    def _accuracy_since(since: datetime) -> tuple[int, int]:
        rows = db.query(StudentAnswer.is_correct).filter(
            StudentAnswer.student_id == student_id,
            StudentAnswer.answered_at >= since).all()
        total = len(rows)
        correct = sum(1 for (ok,) in rows if ok)
        return total, (round(correct / total * 100) if total else 0)

    week_total, week_acc = _accuracy_since(week_ago)
    _prev_total, prev_acc = _accuracy_since(two_weeks_ago)
    acc_delta = week_acc - prev_acc

    from app.services.streak_service import streak_view
    try:
        streak = streak_view(db, student_id)
        streak_days = streak.get("current", 0)
    except Exception:
        streak_days = 0

    return {
        "unfinished": {"count": len(open_practice), "items": unfinished_items},
        "due_soon": {"items": due_soon},
        "week_activity": {
            "accuracy": week_acc,
            "accuracy_delta": acc_delta,
            "completed": week_total,
            "streak_days": streak_days,
        },
    }


def compose_summary_text(db, student_id: str) -> tuple[str, str]:
    """推送正文（REQ-GDS-004）：标题 + 三卡一句话摘要。纯组装，无 LLM。"""
    cards = build_cards(db, student_id)
    weekly = weekly_aggregate(db, student_id)
    ua = cards.get("week_activity", {})
    title = f"【学情摘要】{weekly.get('student_name', '')} 本周学习简报"
    lines = [
        f"本周练习 {weekly.get('practice_count', 0)} 题，正确率 {int(weekly.get('accuracy_rate', 0) * 100)}%",
        f"未完成任务 {cards.get('unfinished', {}).get('count', 0)} 项"
        f"（{('、'.join(i['title'] for i in cards.get('unfinished', {}).get('items', [])[:2])) or '无明细'}）",
        f"复习连击 {ua.get('streak_days', 0)} 天",
    ]
    return title, "\n".join(lines)
