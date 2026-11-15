"""
错题强化训练 API
P1-2: 错题强化训练系统
"""

from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import List, Optional
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker, SELF_ROLES
from app.middleware.scope import ScopeGuard, require_student
from app.models.database import Question, Student, StudentAnswer, get_db
from app.services.wrong_question_trainer import (
    get_student_wrong_questions, generate_variant_questions,
    create_training_session, submit_training_result
)

router = APIRouter()


class TrainingSessionRequest(BaseModel):
    """创建训练会话请求"""
    student_id: str
    question_ids: List[str]


class TrainingSubmitRequest(BaseModel):
    """提交训练结果请求"""
    session_id: str
    student_id: str
    answers: List[dict]  # [{question_id, answer}]


class VariantGenerateRequest(BaseModel):
    """生成变式题请求"""
    original_question_id: str
    quantity: int = 3


@router.get("/student/{student_id}/wrong-questions")
async def get_wrong_questions(
    student_id: str,
    limit: int = 20,
    knowledge_point: Optional[str] = None,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("practice", "read", allow_self=True)),
    _scope: None = Depends(ScopeGuard("student_id", kind="student")),
):
    """
    获取学生的错题列表
    """
    # 验证学生存在
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="学生不存在")

    wrong_questions = get_student_wrong_questions(student_id, limit, knowledge_point)

    return {
        "success": True,
        "student_id": student_id,
        "student_name": student.name,
        "count": len(wrong_questions),
        "wrong_questions": wrong_questions
    }


@router.post("/variant/generate")
async def generate_variants(request: VariantGenerateRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("practice", "create", allow_self=True)),
):
    """
    根据原题生成变式练习题
    """
    # allow_self 的契约：LLM 生成本身有成本，本人角色只能对自己错题册里有的题生成变式
    if current_user["role"] in SELF_ROLES:
        from app.services.grading_center import effective_conditions
        owns = db.query(StudentAnswer.question_id).filter(
            effective_conditions(),
            StudentAnswer.student_id == current_user["user_id"],
            StudentAnswer.question_id == request.original_question_id,
        ).first()
        if not owns:
            raise HTTPException(status_code=403, detail="只能对错题册中的题目生成变式")

    variants = generate_variant_questions(request.original_question_id, request.quantity)

    if not variants:
        return {
            "success": False,
            "message": "生成变式题失败"
        }

    return {
        "success": True,
        "original_question_id": request.original_question_id,
        "variants": variants
    }


@router.post("/training/create")
async def create_training(request: TrainingSessionRequest, db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("practice", "create", allow_self=True)),
):
    """
    创建强化训练会话
    """
    # allow_self 的契约：student_id 在请求体里，就地校验范围
    require_student(current_user, db, request.student_id)

    student = db.query(Student).filter(Student.student_id == request.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="学生不存在")

    if not request.question_ids:
        raise HTTPException(status_code=400, detail="请选择要训练的题目")

    session = create_training_session(request.student_id, request.question_ids)

    return {
        "success": True,
        **session
    }


@router.post("/training/submit")
async def submit_training(request: TrainingSubmitRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("practice", "create", allow_self=True)),
):
    """
    提交训练结果
    """
    # allow_self 的契约：student_id 在请求体里，就地校验范围
    require_student(current_user, db, request.student_id)

    # 移动端选择题发选项下标（与 /api/practice/submit 的 _extract_student_answer
    # 同一客户端契约），在 API 层映射为字母——判卷中心只认答案键形态，
    # 不映射会把 "0"/"2" 当文本判错（E2E E4 实测：全对显示 0/2）
    qids = {a.get("question_id") for a in request.answers} - {None}
    if qids:
        qmap = {q.question_id: q for q in db.query(Question).filter(
            Question.question_id.in_(qids)).all()}
        for a in request.answers:
            q = qmap.get(a.get("question_id"))
            if q is None:
                continue
            raw = str(a.get("answer") or "").strip()
            options = q.options if isinstance(q.options, list) else None
            if options and raw.isdigit() and int(raw) < len(options):
                a["answer"] = chr(ord("A") + int(raw))

    result = submit_training_result(
        request.session_id,
        request.student_id,
        request.answers
    )

    return {
        "success": True,
        **result
    }


@router.get("/training/history/{student_id}")
async def get_training_history(
    student_id: str,
    limit: int = 10,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("practice", "read", allow_self=True)),
    _scope: None = Depends(ScopeGuard("student_id", kind="student")),
):
    """
    获取学生的训练历史
    """
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="学生不存在")

    # TODO: 实现训练历史查询
    return {
        "success": True,
        "student_id": student_id,
        "history": []
    }


@router.get("/knowledge-points")
async def get_knowledge_points_with_wrong_questions(
    student_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("practice", "read", allow_self=True)),
):
    """
    获取有错题的知识点列表
    用于筛选要强化的知识点
    """
    # student_id 是查询参数（不在路径上），就地校验范围
    require_student(current_user, db, student_id)

    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="学生不存在")

    wrong_questions = get_student_wrong_questions(student_id, limit=100)

    # 统计每个知识点的错题数量
    kp_count = {}
    for q in wrong_questions:
        for kp in (q.get("knowledge_points") or []):   # 未打知识点标签的题为 None
            if kp not in kp_count:
                kp_count[kp] = 0
            kp_count[kp] += 1

    # 按错题数量排序
    sorted_kps = sorted(kp_count.items(), key=lambda x: x[1], reverse=True)

    return {
        "success": True,
        "student_id": student_id,
        "knowledge_points": [
            {"name": kp, "wrong_count": count}
            for kp, count in sorted_kps
        ]
    }
