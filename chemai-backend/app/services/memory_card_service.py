"""记忆卡服务（能力升级批次 B）——FSRS 调度内核（CONTEXT.md「Memory Card」）。

三个口径（spec 批次 B）：

- **自动建卡**：判卷完成钩子按（学生, 题目）对每道错题 upsert，零老师操作。
- **双信号**：重做自动判定（对=Good、错=Again）为主；主观题自评三档
  （忘了/模糊/记得 → Again/Hard/Good）兜底。两种信号都喂同一个 FSRS Scheduler。
- **升级替换不并存**：`/api/review` 对外契约不变（due/count/submit 形状照旧，
  task_id 换成 `mc-<card_id>` 的不透明 ID），由本服务供数；存量 ReviewTask 由
  migrate_review_task 映射初始状态后，艾宾浩斯阶梯代码退役（spaced_repetition
  保留 import 兼容，不再被 API 路径调用）。

py-fsrs（BSD-3）v6 API：`card, log = scheduler.review_card(card, rating)`。
"""
from datetime import datetime, timedelta, timezone

from typing import Sequence

from fsrs import Card as FsrsCard
from fsrs import Rating, Scheduler
from sqlalchemy.orm import Session

from app.models.database import MemoryCard

def _load_calibrated_parameters() -> Sequence[float] | None:
    """读 app_config.fsrs_weights（scripts/calibrate_fsrs.py 离线写入；REQ-RCS-003）。

    读失败/未配置 → None → Scheduler 用内置默认参数。进程内缓存一次。"""
    try:
        from app.models.database import engine
        from sqlalchemy import text
        with engine.connect() as conn:
            row = conn.execute(text(
                "SELECT value FROM app_config WHERE key = 'fsrs_weights'"
            )).fetchone()
        if row:
            import json
            return [float(w) for w in json.loads(row[0])]
    except Exception:
        return None
    return None


_CALIBRED: list | None = None
_CALIBRED_DONE = False


def _scheduler() -> Scheduler:
    global _CALIBRED, _CALIBRED_DONE
    if not _CALIBRED_DONE:
        _CALIBRED_DONE = True
        _CALIBRED = _load_calibrated_parameters()
        if _CALIBRED:
            print(f"FSRS 调度参数已启用校准值（{len(_CALIBRED)} 项）")
    if _CALIBRED:
        return Scheduler(parameters=_CALIBRED, enable_fuzzing=False)
    return Scheduler(enable_fuzzing=False)  # 调度确定性：测试与重放不吃随机





def _to_utc(dt: datetime) -> datetime:
    """SQLite 读回的 naive datetime → FSRS 要的 aware UTC。"""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _from_utc(dt: datetime) -> datetime:
    """FSRS 回来的 aware UTC → 库里统一 naive UTC。"""
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def rating_from_signal(is_correct: bool | None = None,
                       self_rating: str | None = None) -> Rating:
    """双信号 → FSRS Rating。重做自动判定优先；自评三档兜底。"""
    if is_correct is not None:
        return Rating.Good if is_correct else Rating.Again
    mapping = {"forgot": Rating.Again, "模糊": Rating.Hard, "vague": Rating.Hard,
               "remembered": Rating.Good, "记得": Rating.Good}
    return mapping.get((self_rating or "").lower(), Rating.Again)


def _card_from_row(row: MemoryCard) -> FsrsCard:
    """库行 → FSRS 卡（v6 API：只有 state/step/stability/difficulty/due/last_review，
    没有 reps/lapses——复习与遗忘次数在库行上自己累计）。
    从未复习的 New 卡（reps==0）直接用构造默认。state 钳到 1-3：
    v6.3.2 的 dataclass setter 虽静默接受 0，但 review_card 内部只认
    Learning/Review/Relearning 三个枚举值（实测 "Unknown card state: 0"）。"""
    card = FsrsCard()
    if row.reps and row.reps > 0:
        card.due = _to_utc(row.due)
        card.stability = row.stability
        card.difficulty = row.difficulty
        card.state = row.state if row.state in (1, 2, 3) else 1
        card.step = row.step
        if row.last_review:
            card.last_review = _to_utc(row.last_review)
    return card


def _write_card(row: MemoryCard, card: FsrsCard, rating: Rating) -> None:
    row.due = _from_utc(card.due)
    row.stability = card.stability
    row.difficulty = card.difficulty
    row.state = int(card.state)
    row.step = card.step
    row.last_review = _from_utc(card.last_review) if card.last_review else None
    row.reps = (row.reps or 0) + 1
    if rating == Rating.Again:
        row.lapses = (row.lapses or 0) + 1


def ensure_card(db: Session, student_id: str, question_id: str,
                source: str = "grading") -> MemoryCard:
    """按（学生, 题目）取卡，没有则建（New，立即可复习）。幂等。"""
    row = (db.query(MemoryCard)
           .filter(MemoryCard.student_id == student_id,
                   MemoryCard.question_id == question_id)
           .first())
    if row is None:
        row = MemoryCard(student_id=student_id, question_id=question_id,
                         source=source)
        db.add(row)
        db.flush()
    return row


def create_expression_card(db: Session, student_id: str, prompt: str,
                           model_answer: str) -> MemoryCard | None:
    """表述特训错句卡（批 2 S5b / REQ-EXP-003）：复用「(学生, 题目) 一卡」实体。

    正面=场景问题（prompt），背面=标准表述（model_answer）；probe_dimension
    落 expression（表述障碍）。建卡进 New 态（立即可复习），作答走复习中心。
    """
    from app.models.database import Question, QuestionSource, AuditStatus
    import uuid as _uuid

    qid = f"exp_{_uuid.uuid4().hex[:12]}"
    db.add(Question(
        question_id=qid,
        content=prompt[:2000],
        question_type="short_answer",
        answer=model_answer[:4000],
        knowledge_points=["化学表述"],
        probe_dimension="expression",
        difficulty=3,
        source=QuestionSource.AI_GENERATED,
        audit_status=AuditStatus.PASSED,
    ))
    db.flush()
    card = ensure_card(db, student_id, qid)
    db.commit()
    return card


def review_card(db: Session, card_id: int, is_correct: bool | None = None,
                self_rating: str | None = None) -> MemoryCard | None:
    """提交一次复习信号，FSRS 算出下次到期日。卡不存在返回 None。"""
    row = db.get(MemoryCard, card_id)
    if row is None:
        return None
    rating = rating_from_signal(is_correct, self_rating)
    card, _log = _scheduler().review_card(_card_from_row(row), rating)
    _write_card(row, card, rating)
    row.last_signal = ("correct" if is_correct else "wrong") if is_correct is not None \
        else (self_rating or "forgot")
    db.commit()
    return row


def due_cards(db: Session, student_id: str, limit: int = 20) -> list[MemoryCard]:
    """到期卡（含过期所有未到期排序：最该复习的在前）。"""
    now = datetime.utcnow()
    return (db.query(MemoryCard)
            .filter(MemoryCard.student_id == student_id,
                    MemoryCard.due <= now)
            .order_by(MemoryCard.due.asc())
            .limit(max(1, min(limit, 100)))
            .all())


def due_count(db: Session, student_id: str) -> int:
    now = datetime.utcnow()
    return (db.query(MemoryCard)
            .filter(MemoryCard.student_id == student_id,
                    MemoryCard.due <= now)
            .count())


def sync_cards_from_wrong_answers(db: Session, student_id: str, limit: int = 50) -> int:
    """从错题历史回填记忆卡（/api/review/sync 手动触发；新错题走判卷钩子，不经这里）。"""
    from app.models.database import StudentAnswer
    wrong = (db.query(StudentAnswer)
             .filter(StudentAnswer.student_id == student_id,
                     StudentAnswer.is_correct.is_(False))
             .order_by(StudentAnswer.answered_at.desc())
             .limit(limit).all())
    created = 0
    for answer in wrong:
        before = (db.query(MemoryCard)
                  .filter(MemoryCard.student_id == student_id,
                          MemoryCard.question_id == answer.question_id)
                  .first())
        ensure_card(db, student_id, answer.question_id)
        if before is None:
            created += 1
    db.commit()
    return created


def migrate_review_task(db: Session, task) -> MemoryCard:
    """存量艾宾浩斯 ReviewTask → MemoryCard 初始状态（ADR：契约不变，一次性迁移）。

    review_level（0-5 天梯）→ 初始稳定度近似（天数即稳定度），难度中性 5，
    状态 Review；consecutive_errors 计入 lapses。幂等：已有卡直接返回。
    """
    existing = (db.query(MemoryCard)
                .filter(MemoryCard.student_id == task.student_id,
                        MemoryCard.question_id == task.question_id)
                .first())
    if existing:
        return existing
    level = max(0, min(task.review_level or 0, 5))  # 负数会走 Python 负索引（P2-11）
    level_days = (0, 1, 3, 7, 14, 30)
    days = level_days[level]
    stability = float(days) or 0.1
    due = task.next_review_at or datetime.utcnow()
    # last_review 必须有：fsrs 的 retrievability 对 last_review=None 恒为 0，
    # 首刷 Good 会把稳定度从 7 推到 247（实测）——按「days 前复习过」近似。
    last_review = due - timedelta(days=days) if days > 0 else due
    row = MemoryCard(
        student_id=task.student_id, question_id=task.question_id,
        due=due,
        stability=stability, difficulty=5.0,
        state=2,  # Review
        last_review=last_review,
        reps=level + 1,
        lapses=task.consecutive_errors or 0,
        source="migrated",
    )
    db.add(row)
    db.flush()
    return row


def migrate_all_legacy(db: Session) -> int:
    """存量艾宾浩斯 ReviewTask 全量迁移（幂等）。迁移完的行标 status='migrated'：
    旧消费方（家长提醒/错题复习列表）按 status 过滤，死计数随迁移自然消失。"""
    from app.models.review_task import ReviewTask
    migrated = 0
    tasks = db.query(ReviewTask).filter(ReviewTask.status != "migrated").all()
    for task in tasks:
        migrate_review_task(db, task)
        task.status = "migrated"
        migrated += 1
    db.commit()
    return migrated


def sync_card_from_judgment(db: Session, student_id: str, question_id: str,
                            is_correct: bool) -> None:
    """判定落定/教师改判后同步卡状态（P1-1：主观题先建后判的两条回收路径）：
    - 判对：从未复习过的卡直接回收（答对不建/不留垃圾）；复习过的当 Good 复习一次。
    - 判错：确保有卡（幂等）。"""
    row = (db.query(MemoryCard)
           .filter(MemoryCard.student_id == student_id,
                   MemoryCard.question_id == question_id)
           .first())
    if is_correct:
        if row is None:
            return
        if (row.reps or 0) == 0:
            db.delete(row)
        else:
            review_card(db, row.id, is_correct=True)
    else:
        ensure_card(db, student_id, question_id)
