"""家长派任务（批 3 P2 / parent-agent-tools REQ-PAT-002）。

纯函数规则 + 薄查询：
- 频控（防滥用）：每学生每周家长派任务 ≤2 次。计数 = 本 ISO 周内 created_by
  为家长身份、面向该学生的计划草稿链数（LearningPlanDraft.created_at ≥ 周一）。
- 绑定校验复用亲子绑定表。

工具层（agent/tools/parent.py）按 _GATE_OPEN_TOOLS 生成即门模式配对：
parent_assign_task（生成草稿 + approval_needed）→ 门批准后重放 parent_send_task
（send_draft 落学生计划）。
"""

from __future__ import annotations

from datetime import datetime, timedelta

WEEKLY_ASSIGN_LIMIT = 2


def week_start(now: datetime | None = None) -> datetime:
    """本周一 00:00（Asia/Shanghai 自然周，服务端计算）。"""
    from zoneinfo import ZoneInfo
    tz = ZoneInfo("Asia/Shanghai")
    now = now or datetime.now(tz)
    monday = (now - timedelta(days=now.weekday())).date()
    return datetime(monday.year, monday.month, monday.day)


def parent_assignments_used(db, student_id: str, now: datetime | None = None) -> int:
    """本周已派（送达）次数：家长创建且 **已 sent** 的该生草稿链数。

    口径为「送达计」（用户定案，2026-10-03）：放弃/打回的草稿不计额——
    只有真正送到孩子学习计划的任务才消耗每周 ≤2 的配额。
    """
    from app.models.database import LearningPlanDraft
    from sqlalchemy import String as _String, cast as _cast

    start = week_start(now)
    rows = (db.query(LearningPlanDraft.draft_id)
            .filter(LearningPlanDraft.student_id == student_id,
                    LearningPlanDraft.created_at >= start,
                    LearningPlanDraft.status == "sent",
                    _cast(LearningPlanDraft.created_by, _String).like("parent:%"))
            .all())
    return len({r[0] for r in rows})


def can_assign(db, student_id: str, now: datetime | None = None) -> tuple[bool, int, str]:
    """频控判定（REQ-PAT-002）：返回 (是否可派, 本周已派, 提示语)。"""
    used = parent_assignments_used(db, student_id, now)
    if used >= WEEKLY_ASSIGN_LIMIT:
        return False, used, f"本周派任务次数已用完（每周 {WEEKLY_ASSIGN_LIMIT} 次），下周再来"
    return True, used, ""


def parent_of_student(db, parent_id: str, student_id: str) -> bool:
    """亲子绑定校验（active 绑定才算）。"""
    from app.models.database import StudentParentBinding
    row = (db.query(StudentParentBinding)
           .filter(StudentParentBinding.student_id == student_id,
                   StudentParentBinding.parent_id == parent_id,
                   StudentParentBinding.status == "active").first())
    return row is not None
