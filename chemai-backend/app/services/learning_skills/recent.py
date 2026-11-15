"""接棒会话的读侧聚合（C4：debrief 不靠学生口述）。

学生点接棒跳到 /m/practice、/m/review 自己作答，对话侧收不到"做完了"这个事件：
原 debrief 只能复述 due_count/streak，"做了几题/对几题/连击有没有变"全靠学生回来说
一句"清完了"。这里只反查**已经落库**的两处，回答这几个问题：

- 智能练习会话态在 ExamRecord.question_stats["smart"]（smart_practice_service 逐题
  写入；record_id 前缀 smart_<student_id>_<ts> 同时是逐生归属键）；
- 复习信号在 MemoryCard.last_review / last_signal（/api/review/submit 落库）。

只读不写：新增写路径要跟 work_unit/事务纪律打交道（CLAUDE.md「确认门的提交主权」），
而作答/提交那一刻数据就已经在库里，读侧信息量足够——不为了"知道做完了"再开一条
写入链路。
"""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from zoneinfo import ZoneInfo

# 连击日口径 = streak_service.local_today（Asia/Shanghai 自然日）。不 import 它的
# 模块私有 _TZ；parent_task_service.week_start 同样自己建 ZoneInfo（同款取舍）。
_TZ = ZoneInfo("Asia/Shanghai")


def _utc_day_start(day: date) -> datetime:
    """本地自然日 0 点 → 库里存的 naive UTC。

    memory_card_service._from_utc 把时间统一落成 naive UTC，而连击日是按本地自然日算
    的：直接拿"本地 0 点"去比 UTC 列会错开 8 小时，当地 0–8 点提交的复习会被漏掉。
    """
    return (datetime.combine(day, time.min, tzinfo=_TZ)
            .astimezone(timezone.utc).replace(tzinfo=None))


def review_activity(db, student_id: str) -> dict:
    """今日复习的真实提交结果（逐信号计数）+ 连击今天有没有记上。"""
    from app.models.database import MemoryCard, StudentStreak
    from app.services.streak_service import local_today

    today = local_today()
    rows = (db.query(MemoryCard.last_signal, MemoryCard.last_review)
            .filter(MemoryCard.student_id == student_id,
                    MemoryCard.last_review >= _utc_day_start(today))
            .all())
    signals = [str(sig or "") for sig, _at in rows]
    stamps = [at for _sig, at in rows if at]
    streak_row = (db.query(StudentStreak)
                  .filter(StudentStreak.student_id == student_id).first())
    return {
        "reviewed": len(rows),
        "correct": signals.count("correct"),
        "wrong": signals.count("wrong"),
        "forgot": signals.count("forgot"),
        "vague": signals.count("vague"),
        "remembered": signals.count("remembered"),
        "last_review_at": max(stamps).isoformat() if stamps else None,
        # 连击"有没有变"的可信证据只有日期：连击行今天被记过 = 已结算过清卡。
        # 不调 streak_view —— 它会 get_or_create 建行并可能 commit，读侧不该产生写入。
        "streak_credited_today": bool(streak_row and streak_row.last_active_date == today),
    }


def latest_practice_session(db, student_id: str, session_id: str = "") -> dict | None:
    """最近一次智能练习会话的真实结果；无会话/不属于本人返回 None。

    session_id 给了就读那一轮（契约里 debrief 可带 session_id）；缺省取最近一轮。
    """
    from app.models.database import ExamRecord, RecordType
    from app.services import smart_practice_service as sps

    query = db.query(ExamRecord).filter(ExamRecord.type == RecordType.PRACTICE)
    if session_id:
        query = query.filter(ExamRecord.record_id == session_id)
    else:
        # 逐生归属键在 record_id 前缀（start_session 的 smart_<student_id>_<ts>）。
        # 不按 question_stats 的 JSON 列过滤：SQLite 存的是 ensure_ascii 转义文本，
        # 直接比值会漏（候选池解码同坑，smart_practice_service:314）。
        query = query.filter(ExamRecord.record_id.like(f"smart_{student_id}_%"))
    record = query.order_by(ExamRecord.created_at.desc()).first()
    if record is None:
        return None
    state = sps.load_session_state(record)
    if state.get("student_id") != student_id:
        return None  # 归属：只认会话自己的 student_id（ADR-0014，不信调用方传的 id）

    responses = state.get("responses") or []
    answered = len(responses)
    correct = sum(1 for r in responses if r.get("is_correct"))
    # θ 与停止规则用 /smart/answer 的同一套口径（逐题 EAP + SE≤0.3 或 8 题）：会话态
    # 只有逐题快照，finish 端点只汇总不写"已完成"标记，所以"做没做完"必须用停止规则
    # 重算——学生直接关页也判定得出来。
    #
    # 这里没调 sps.theta_of：它把（参数, 对错）整对当参数喂给 estimate_theta
    # （smart_practice_service.py:228 的推导式漏了拆元组）——params 里有未标定占位
    # None 时对错列表被滤空、zip 空转，EAP 静默忽略全部作答；params 是标定值时直接
    # TypeError。上游修好后这段可以换回 sps.theta_of。
    theta_start = state.get("theta_start")
    paired = [(tuple(p) if p else None, bool(r.get("is_correct")))
              for p, r in zip(state.get("params") or [], responses)]
    theta_end, se = sps.estimate_theta(
        [prm for prm, _ok in paired if prm is not None],
        [ok for prm, ok in paired if prm is not None],
        prior=theta_start or 0.0)
    started = record.exam_date or record.created_at
    return {
        "practice_id": record.record_id,
        "answered": answered,
        "correct": correct,
        "max_items": sps.MAX_ITEMS,
        "done": sps.should_stop(answered, se),
        "theta_start": theta_start,
        "theta_end": theta_end,
        "theta_delta": round(theta_end - (theta_start or 0.0), 3),
        "uncalibrated": state.get("uncalibrated") or 0,
        "kps": state.get("kps") or [],
        "started_at": started.isoformat() if started else None,
    }
