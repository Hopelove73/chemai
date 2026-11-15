"""复习连击（Review Streak，student-daily-loop S1 / CONTEXT.md「Review Streak」）。

纯函数规则内核（表驱动单测覆盖四分支：清卡/漏天有冻结/漏天无冻结/修复窗口）
+ 薄 DB 胶水。规则定案（Q6/设计 D4）：
- 时区：Asia/Shanghai 自然日，服务端计算，不信任客户端时钟；
- 当日清完全部到期卡 → current+1、best 取 max；
- 冻结卡：每周（ISO 周）首次清空当日全部到期卡奖励 1 张，持有上限 2；
- 漏天：先自动消耗冻结（每天 1 张），冻结不够连击清零；
- 补签修复窗口：每周 1 次、漏天 ≤2 天时可免费回连（不消耗冻结）。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

_TZ = ZoneInfo("Asia/Shanghai")
MAX_FREEZE = 2
REPAIR_GAP_DAYS = 2  # 漏天 ≤2 天可用本周免费修复

def local_today(now: date | None = None) -> date:
    """服务器侧的本地自然日（Asia/Shanghai）。"""
    if now is not None:
        return now
    from datetime import datetime
    return datetime.now(_TZ).date()


def week_key(d: date) -> str:
    """ISO 周键（如 2026-W40），冻结奖励与修复窗口的配额周期。"""
    y, w, _ = d.isocalendar()
    return f"{y}-W{w:02d}"


@dataclass
class StreakState:
    """连击状态的纯数据投影（= student_streak 表行）。"""
    current: int = 0
    best: int = 0
    freeze_count: int = 0
    last_active_date: date | None = None
    freeze_week: str | None = None  # 最近发冻结的 ISO 周键（奖励配额）
    repair_week: str | None = None  # 最近用免费修复的 ISO 周键（修复配额）


@dataclass
class StreakEvent:
    """一次状态推进的输出（审计/提示用）。"""
    freeze_consumed: int = 0
    freeze_awarded: int = 0
    broke: bool = False
    repaired: bool = False


def consume_gap(state: StreakState, today: date) -> tuple[StreakState, StreakEvent]:
    """读取时惰性结算：last_active 之后的漏天先吃冻结，不够则连击清零。

    last_active 是今天或昨天 → 连击仍存活，不动。"""
    if state.last_active_date is None:
        return state, StreakEvent()
    gap_days = (today - state.last_active_date).days
    if gap_days <= 1:  # 今天清过 / 昨天清过（今天还没清也还活着）
        return state, StreakEvent()
    missed = gap_days - 1  # 昨天不在场证明成立，之前的日子才算漏
    consumed = min(missed, state.freeze_count)
    if consumed >= missed:
        return (
            StreakState(state.current, state.best, state.freeze_count - consumed,
                        state.last_active_date, state.freeze_week, state.repair_week),
            StreakEvent(freeze_consumed=consumed),
        )
    # 冻结不够 → 连击断
    return (
        StreakState(0, state.best, state.freeze_count - consumed, None,
                    state.freeze_week, state.repair_week),
        StreakEvent(freeze_consumed=consumed, broke=True),
    )


def register_cleared_day(state: StreakState, today: date) -> tuple[StreakState, StreakEvent]:
    """当日到期卡全部清完 → 连击 +1；本周首次清卡奖励 1 张冻结（上限 2）。

    同日重复调用幂等（当天第二次清卡不发奖不加连击）。"""
    if state.last_active_date == today:
        return state, StreakEvent()
    current = state.current + 1
    wk = week_key(today)
    awarded = 1 if (state.freeze_week != wk and state.freeze_count < MAX_FREEZE) else 0
    return (
        StreakState(current, max(state.best, current), state.freeze_count + awarded,
                    today, wk if awarded else state.freeze_week, state.repair_week),
        StreakEvent(freeze_awarded=awarded),
    )


def apply_repair(state: StreakState, today: date) -> tuple[StreakState, StreakEvent, str]:
    """补签修复：本周未用 && 漏天 ≤2 → 把 last_active 视作昨天，今天清卡即续连击。

    返回 (state, event, message)；不可修复时 message 说明原因，状态不变。"""
    wk = week_key(today)
    if state.repair_week == wk:
        return state, StreakEvent(), "本周修复机会已用过"
    if state.last_active_date is None:
        return state, StreakEvent(), "还没有开始复习，无需修复"
    gap = (today - state.last_active_date).days
    if gap <= 1:
        return state, StreakEvent(), "连击仍存活，无需修复"
    if gap - 1 > REPAIR_GAP_DAYS:
        return state, StreakEvent(), f"已漏 {gap - 1} 天，超出可修复范围（≤{REPAIR_GAP_DAYS} 天）"
    if state.current == 0:
        return state, StreakEvent(), "连击已断，从今天重新开始即可"
    return (
        StreakState(state.current, state.best, state.freeze_count,
                    yesterday_of(today), state.freeze_week, wk),
        StreakEvent(repaired=True),
        "修复成功：今天清完到期卡即可续上连击",
    )


def yesterday_of(d: date) -> date:
    return d - timedelta(days=1)


# ═══════════════ DB 胶水（薄 adapter，规则全在上面纯函数里）═══════════════

def get_or_create_row(db, student_id: str):
    from app.models.database import StudentStreak
    row = db.get(StudentStreak, student_id)
    if row is None:
        row = StudentStreak(student_id=student_id)
        db.add(row)
        db.flush()
    return row


def row_to_state(row) -> StreakState:
    return StreakState(
        current=row.current or 0,
        best=row.best or 0,
        freeze_count=row.freeze_count or 0,
        last_active_date=row.last_active_date,
        freeze_week=row.freeze_week,
        repair_week=row.repair_week,
    )


def write_state(row, state: StreakState) -> None:
    row.current = state.current
    row.best = state.best
    row.freeze_count = state.freeze_count
    row.last_active_date = state.last_active_date
    row.freeze_week = state.freeze_week
    row.repair_week = state.repair_week


def register_clear_for_student(db, student_id: str) -> dict | None:
    """submit 钩子：当日到期卡清完时调用。返回事件摘要（无变化返回 None）。

    「清完」排除 Learning/Relearning 学习步卡（state 1/3）：答错的卡有
    1 分钟学习步，会立即重新到期——把它们算进「当日应清」，连击永远无法
    达成（E2E 实测发现的口径冲突）。只看 New + Review 卡。"""
    from app.services import memory_card_service as mcs
    from app.models.database import MemoryCard

    today = local_today()
    learning_due = (db.query(MemoryCard.id)
                    .filter(MemoryCard.student_id == student_id,
                            MemoryCard.due <= datetime.utcnow(),
                            MemoryCard.state.in_([1, 3]))
                    .count())
    if mcs.due_count(db, student_id) > learning_due:
        return None  # 还有非学习步的到期卡，不算清完
    row = get_or_create_row(db, student_id)
    state, _ = consume_gap(row_to_state(row), today)
    state, event = register_cleared_day(state, today)
    if event.freeze_awarded or state.current != row.current or row.last_active_date != today:
        write_state(row, state)
        db.commit()
        # 家长动态（批 3 P1 / REQ-PFD-001）：连击里程碑 7/14/30 → 家长流卡
        from app.services.notification_service import notification_service as _ns
        if state.current in _ns.STREAK_MILESTONES:
            try:
                from app.services.notification_service import notification_service
                notification_service.notify_student_parents(
                    db, student_id, notification_service.FEED_STREAK_MILESTONE,
                    title=f"孩子复习连击达到 {state.current} 天",
                    content=None, idempotency_key=f"feed_streak_{student_id}_{state.current}")
            except Exception:
                import logging
                logging.getLogger(__name__).warning(
                    "parent feed dispatch failed student=%s streak=%s",
                    student_id, state.current, exc_info=True)
        return {"current": state.current, "best": state.best,
                "freeze_awarded": event.freeze_awarded}
    return None


def streak_view(db, student_id: str) -> dict:
    """GET 口径：先惰性结算漏天（吃冻结/断连击），再返回状态 + 今日是否已清。"""
    from app.services import memory_card_service as mcs

    today = local_today()
    row = get_or_create_row(db, student_id)
    state, event = consume_gap(row_to_state(row), today)
    if event.freeze_consumed or event.broke:
        write_state(row, state)
        db.commit()
    cleared_today = mcs.due_count(db, student_id) == 0 and state.last_active_date == today
    repair_available = row.repair_week != week_key(today)
    return {
        "student_id": student_id,
        "current": state.current,
        "best": state.best,
        "freeze_count": state.freeze_count,
        "freeze_max": MAX_FREEZE,
        "last_active_date": state.last_active_date.isoformat() if state.last_active_date else None,
        "cleared_today": cleared_today,
        "repair_available": repair_available,
    }
