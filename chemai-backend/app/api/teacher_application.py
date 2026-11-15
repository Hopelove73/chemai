"""
教师入驻申请API
GET /api/teacher-applications - 获取申请列表
POST /api/teacher-applications/{id}/approve - 审批通过
POST /api/teacher-applications/{id}/reject - 审批拒绝
"""
from fastapi import APIRouter, Depends, Request
from typing import Optional
from sqlalchemy.orm import Session
from datetime import datetime

from app.models.database import get_db, OperationLog, TeacherApplication, Teacher, Account
from app.middleware.auth import PermissionChecker
import hashlib

router = APIRouter()


def hash_password(password: str) -> str:
    """哈希密码"""
    return hashlib.sha256(password.encode()).hexdigest()


def _audit(db: Session, user_id: str, action: str, target_id: str, detail: dict):
    """管理动作审计 —— 与 data_import 同款直写（阶段 7.1 后矩阵外的授权不写第二套日志）。"""
    db.add(OperationLog(
        user_id=user_id,
        action=action,
        target_type="teacher_application",
        target_id=target_id,
        detail=detail,
    ))


@router.get("")
async def get_applications(
    request: Request,
    status: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("teacher", "create"))
):
    """
    获取教师入驻申请列表。

    门是 `teacher:create`（仅 admin 持有）—— admin-console-p0 整改：原先借用
    `teacher:read` 当门、函数体内再手写 `role != "admin"`，是矩阵外的第二套
    授权（阶段 7.1 对 log.py 的同款反模式），现在矩阵重新成为唯一授权真源。
    """
    query = db.query(TeacherApplication)
    if status:
        query = query.filter(TeacherApplication.status == status)

    applications = query.order_by(TeacherApplication.created_at.desc()).all()

    return {
        "success": True,
        "total": len(applications),
        "applications": [
            {
                "id": a.id,
                "name": a.name,
                "phone": a.phone,
                "school": a.school,
                "subject": a.subject,
                "status": a.status,
                "created_at": a.created_at.isoformat() if a.created_at else None
            }
            for a in applications
        ]
    }


@router.post("/{app_id}/approve")
async def approve_application(
    app_id: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("teacher", "create"))
):
    """
    审批通过教师入驻申请
    创建教师账号和登录账号
    """
    application = db.query(TeacherApplication).filter(TeacherApplication.id == app_id).first()
    if not application:
        return {"success": False, "message": "申请不存在"}

    if application.status != "pending":
        return {"success": False, "message": f"申请状态已是{application.status}"}

    # 创建教师记录（e2e 实测抓到：Teacher 没有 role 列 —— 此前该端点从未被真调通）
    teacher_id = f"teacher_{datetime.now().strftime('%Y%m%d%H%M%S')}"
    teacher = Teacher(
        teacher_id=teacher_id,
        name=application.name,
        phone=application.phone,
        school_id=current_user.get("school_id"),
        status="approved"
    )
    db.add(teacher)

    # 创建登录账号（默认密码为手机号后6位）
    default_password = application.phone[-6:] if len(application.phone) >= 6 else "123456"
    account_id = f"acc_{teacher_id}"
    account = Account(
        account_id=account_id,
        username=application.phone,  # 手机号作为用户名
        password_hash=hash_password(default_password),
        role="teacher",
        teacher_id=teacher_id,
        status="active"
    )
    db.add(account)

    # 更新申请状态
    application.status = "approved"
    application.reviewer_id = current_user.get("user_id")
    application.reviewed_at = datetime.utcnow()
    _audit(db, current_user.get("user_id"), "teacher_application_approve", app_id,
           {"name": application.name, "teacher_id": teacher_id})

    db.commit()

    return {
        "success": True,
        "message": "审批通过，已创建教师账号",
        "teacher_id": teacher_id,
        "username": application.phone,
        "default_password": default_password,
        "note": "请通知教师使用手机号和默认密码登录，首次登录后请修改密码"
    }


@router.post("/{app_id}/reject")
async def reject_application(
    app_id: str,
    request: Request,
    reason: str = "",
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("teacher", "create"))
):
    """
    审批拒绝教师入驻申请
    """
    application = db.query(TeacherApplication).filter(TeacherApplication.id == app_id).first()
    if not application:
        return {"success": False, "message": "申请不存在"}

    if application.status != "pending":
        return {"success": False, "message": f"申请状态已是{application.status}"}

    application.status = "rejected"
    application.reviewer_id = current_user.get("user_id")
    application.reviewed_at = datetime.utcnow()
    _audit(db, current_user.get("user_id"), "teacher_application_reject", app_id,
           {"name": application.name, "reason": reason or None})

    db.commit()

    return {
        "success": True,
        "message": "已拒绝申请"
    }
