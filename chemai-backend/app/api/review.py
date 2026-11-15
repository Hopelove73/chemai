"""
复习任务 API
基于艾宾浩斯遗忘曲线的间隔重复复习系统
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker
from app.middleware.scope import ScopeGuard, require_student

from app.models.database import (
    MemoryCard, Student, Question, get_db
)
from app.services import memory_card_service as mcs

router = APIRouter()


class ReviewTaskResponse(BaseModel):
    """复习任务响应"""
    task_id: str
    question_id: str
    content: str
    options: Optional[List[str]] = None
    answer: Optional[str] = None
    analysis: Optional[str] = None
    knowledge_points: List[str]
    difficulty: str
    review_level: int
    next_review_at: Optional[str] = None


class ReviewSubmitRequest(BaseModel):
    """复习提交请求（批次 B 双信号：重做自动判定为主，自评三档兜底）。

    is_correct 与 self_rating 至少给一个；同时给时以客观判定为准。"""
    task_id: str
    is_correct: Optional[bool] = None
    self_rating: Optional[str] = None  # forgot/vague/remembered（忘了/模糊/记得）


@router.get("/student/{student_id}/due")
async def get_due_reviews(student_id: str, limit: int = 20, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("review", "read", allow_self=True)),
    _scope: None = Depends(ScopeGuard("student_id", kind="student")),
):
    """
    获取学生到期应复习的任务
    """
    # 验证学生存在
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail=f"学生 {student_id} 不存在")

    tasks = _due_tasks(db, student_id, limit=20)

    return {
        "success": True,
        "student_id": student_id,
        "due_count": len(tasks),
        "tasks": tasks
    }


def _count_due_with_question(db: Session, student_id: str) -> int:
    """到期且题目仍存在（与 /due 的口径一致——/due 会跳过缺题卡，P2-10）。"""
    now = datetime.utcnow()
    return (db.query(MemoryCard)
            .join(Question, Question.question_id == MemoryCard.question_id)
            .filter(MemoryCard.student_id == student_id,
                    MemoryCard.due <= now)
            .count())


def _due_tasks(db: Session, student_id: str, limit: int = 20) -> list[dict]:
    """到期卡 → 复习任务列表（契约形状与艾宾浩斯版一致；task_id = mc-<id> 不透明）。"""
    cards = mcs.due_cards(db, student_id, limit=limit)
    result = []
    for card in cards:
        question = db.query(Question).filter(
            Question.question_id == card.question_id).first()
        if not question:
            continue
        result.append({
            "task_id": f"mc-{card.id}",
            "question_id": question.question_id,
            "content": question.content,
            "options": question.options,
            "answer": question.answer,
            "analysis": question.analysis,
            "knowledge_points": question.knowledge_points,
            "difficulty": question.difficulty if question.difficulty is not None else 3,
            "review_level": card.state,
            "next_review_at": card.due.isoformat() if card.due else None,
        })
    return result


@router.get("/student/{student_id}/count")
async def get_review_count(student_id: str, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("review", "read", allow_self=True)),
    _scope: None = Depends(ScopeGuard("student_id", kind="student")),
):
    """
    获取学生待复习任务数量
    """
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail=f"学生 {student_id} 不存在")

    return {
        "success": True,
        "student_id": student_id,
        "due_count": _count_due_with_question(db, student_id)
    }


@router.post("/submit")
async def submit_review(request: ReviewSubmitRequest, db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("review", "update", allow_self=True)),
):
    """
    提交复习结果
    """
    if not request.task_id.startswith("mc-"):
        raise HTTPException(status_code=404, detail="复习任务不存在")
    try:
        card_id = int(request.task_id[3:])
    except ValueError:
        raise HTTPException(status_code=404, detail="复习任务不存在")

    # allow_self 的契约：task_id 是不透明 ID，先落到学生再判范围，然后才改
    row = db.get(MemoryCard, card_id)
    if not row:
        raise HTTPException(status_code=404, detail="复习任务不存在")
    require_student(current_user, db, row.student_id)

    if request.is_correct is None and not (request.self_rating or "").strip():
        raise HTTPException(status_code=400, detail="请提供 is_correct 或 self_rating")
    # 自评白名单（P2-7）：未知值静默映射 Again 会偷扣 lapses
    _VALID_RATINGS = {"forgot", "vague", "remembered", "忘了", "模糊", "记得"}
    if (request.self_rating or "").strip() and request.self_rating.strip() not in _VALID_RATINGS:
        raise HTTPException(status_code=400,
                            detail=f"self_rating 必须是 {sorted(_VALID_RATINGS)} 之一")

    task = mcs.review_card(db, card_id,
                           is_correct=request.is_correct,
                           self_rating=request.self_rating)
    if not task:
        raise HTTPException(status_code=404, detail="复习任务不存在")

    # 文案语义（P2-9）：客观判定优先；分钟级间隔（FSRS 学习步）按分钟报，不谎称天
    delta = task.due - datetime.utcnow()
    if request.is_correct is not None:
        signal = "答对了" if request.is_correct else "答错了"
    else:
        signal = {"forgot": "忘了", "vague": "模糊", "remembered": "记得"}.get(
            (request.self_rating or "").strip(), "已记录")
    if delta.total_seconds() < 3600:
        message = f"{signal}，{max(1, delta.seconds // 60)}分钟后再来一次"
    elif delta.total_seconds() < 86400:
        message = f"{signal}，今天稍后再复习一次"
    else:
        message = f"{signal}，{(delta.days + 1)}天后再次复习"

    # 连击钩子（S1）：本次提交后到期卡清零 → 记当日清卡（连击+1/冻结奖励）
    streak_event = None
    try:
        from app.services import streak_service
        streak_event = streak_service.register_clear_for_student(db, row.student_id)
    except Exception:  # 连击记账失败不阻断复习提交，但要留下痕迹
        import logging
        logging.getLogger(__name__).debug("streak register failed for %s", row.student_id, exc_info=True)

    return {
        "success": True,
        "task_id": request.task_id,
        "is_correct": request.is_correct,
        "review_level": task.state,
        "next_review_at": task.due.isoformat() if task.due else None,
        "message": message,
        "streak": streak_event,
    }


@router.get("/streak/{student_id}")
async def get_review_streak(student_id: str, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("review", "read", allow_self=True)),
    _scope: None = Depends(ScopeGuard("student_id", kind="student")),
):
    """复习连击状态（惰性结算漏天：吃冻结/断连击，REQ-RCS-002）"""
    from app.services import streak_service
    return streak_service.streak_view(db, student_id)


@router.post("/streak/{student_id}/repair")
async def repair_review_streak(student_id: str, db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("review", "update", allow_self=True)),
    _scope: None = Depends(ScopeGuard("student_id", kind="student")),
):
    """补签修复（每周 1 次、漏天 ≤2 天免费回连）"""
    require_student(current_user, db, student_id)
    from app.services import streak_service
    row = streak_service.get_or_create_row(db, student_id)
    state, _event, message = streak_service.apply_repair(
        streak_service.row_to_state(row), streak_service.local_today())
    streak_service.write_state(row, state)
    db.commit()
    return {"success": True, "message": message,
            "current": state.current, "freeze_count": state.freeze_count}


@router.post("/sync/{student_id}")
async def sync_review_tasks(student_id: str, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("review", "create", allow_self=True)),
    _scope: None = Depends(ScopeGuard("student_id", kind="student")),
):
    """
    同步复习任务（从错题历史创建复习任务）
    """
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail=f"学生 {student_id} 不存在")

    created = mcs.sync_cards_from_wrong_answers(db, student_id)

    return {
        "success": True,
        "message": f"复习任务已同步（新建 {created} 张记忆卡）"
    }


@router.get("/history/{student_id}")
async def get_review_history(student_id: str, limit: int = 20, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("review", "read", allow_self=True)),
    _scope: None = Depends(ScopeGuard("student_id", kind="student")),
):
    """
    获取学生复习历史
    """
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail=f"学生 {student_id} 不存在")

    cards = (db.query(MemoryCard)
             .filter(MemoryCard.student_id == student_id,
                     MemoryCard.last_review.isnot(None))
             .order_by(MemoryCard.last_review.desc())
             .limit(limit).all())

    history = []
    for card in cards:
        question = db.query(Question).filter(Question.question_id == card.question_id).first()
        if question:
            history.append({
                "task_id": f"mc-{card.id}",
                "question_id": card.question_id,
                "content": question.content[:100] + "..." if len(question.content) > 100 else question.content,
                "review_level": card.state,
                "completed_at": card.last_review.isoformat() if card.last_review else None,
                "status": card.last_signal or ("reviewed" if card.state == 2 else "learning"),
            })

    return {
        "success": True,
        "student_id": student_id,
        "history": history
    }


@router.post("/create")
async def create_review(
    student_id: str,
    question_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("review", "create")),
):
    """
    手动创建复习任务
    """
    # student_id 是查询参数（不在路径上），就地校验范围
    require_student(current_user, db, student_id)

    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="学生不存在")

    question = db.query(Question).filter(Question.question_id == question_id).first()
    if not question:
        raise HTTPException(status_code=404, detail="题目不存在")

    card = mcs.ensure_card(db, student_id, question_id)
    db.commit()

    return {
        "success": True,
        "task_id": f"mc-{card.id}",
        "message": "复习任务已创建"
    }
