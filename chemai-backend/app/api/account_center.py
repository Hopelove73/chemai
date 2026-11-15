"""账号中心 —— 学生账号（account-centers，批次 2 首批）。

与教师端 `/students` 的分工：
  · 教师端看**学情**（正确率、障碍类型、诊断）；
  · 这里看**账号生命周期**（有没有账号、最近练习、绑定家长数、批量开通/停用）。

门统一 `student:update`（admin / academic_admin 持有，teacher 403）。
数据范围走 RequestScope（班级级收窄）。
"""
import hashlib
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker
from app.middleware.scope import RequestScope
from app.models.database import (
    Account, Class, OperationLog, Parent, Student, StudentParentBinding, get_db,
)

router = APIRouter(prefix="/api/account-center", tags=["账号中心"])

DEFAULT_PASSWORD = "123456"


def _audit(db: Session, user_id: str, action: str, target_id: str, detail: dict):
    db.add(OperationLog(user_id=user_id, action=action, target_type="student",
                        target_id=target_id, detail=detail))


class BatchAccountsRequest(BaseModel):
    student_ids: List[str]


class AccountStatusRequest(BaseModel):
    status: str  # active / disabled


@router.get("/students")
async def list_student_accounts(
    class_id: Optional[str] = None,
    account: Optional[str] = None,   # has / none
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("student", "update")),
):
    """学生账号视图：账号状态、最近练习、绑定家长数。

    与教师端学情列表同源（students 表），但选的是账号维度字段。
    """
    scope = RequestScope.from_user(current_user, db)
    allowed = scope.class_ids()
    if not allowed:
        return {"success": True, "total": 0, "students": []}

    query = (
        db.query(Student, Class.name.label("class_name"))
        .join(Class, Student.class_id == Class.class_id)
        .filter(Student.class_id.in_(allowed))
    )
    if class_id:
        if class_id not in allowed:
            raise HTTPException(status_code=403, detail="无权查看该班级")
        query = query.filter(Student.class_id == class_id)
    if q:
        like = f"%{q.strip()}%"
        query = query.filter((Student.name.like(like)) | (Student.student_id.like(like)))

    rows = query.order_by(Student.class_id, Student.student_id).all()

    # 一次取齐账号与绑定计数，避免 N+1
    ids = [s.student_id for s, _ in rows]
    accounts = {a.student_id: a for a in db.query(Account).filter(Account.student_id.in_(ids)).all()} if ids else {}
    bind_counts = dict(
        db.query(StudentParentBinding.student_id, func.count())
        .filter(StudentParentBinding.student_id.in_(ids),
                StudentParentBinding.status == "active")
        .group_by(StudentParentBinding.student_id).all()
    ) if ids else {}

    out = []
    for stu, class_name in rows:
        acc = accounts.get(stu.student_id)
        if account == "has" and acc is None:
            continue
        if account == "none" and acc is not None:
            continue
        out.append({
            "student_id": stu.student_id,
            "name": stu.name,
            "class_id": stu.class_id,
            "class_name": class_name,
            "approval_status": stu.status,
            "account_status": (acc.status if acc else "none"),
            "username": acc.username if acc else None,
            "last_exercise_at": stu.last_exercise_at.isoformat() if stu.last_exercise_at else None,
            "exercises_completed": stu.exercises_completed or 0,
            "parent_count": int(bind_counts.get(stu.student_id, 0)),
        })
    return {"success": True, "total": len(out), "students": out}


@router.get("/students/summary")
async def student_account_summary(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("student", "update")),
):
    """顶部四格：学生数 / 有账号 / 无账号 / 有绑定家长。"""
    scope = RequestScope.from_user(current_user, db)
    allowed = scope.class_ids()
    if not allowed:
        return {"success": True, "total": 0, "with_account": 0, "without_account": 0, "with_parent": 0}

    total = db.query(Student).filter(Student.class_id.in_(allowed)).count()
    with_account = (
        db.query(Student).join(Account, Account.student_id == Student.student_id)
        .filter(Student.class_id.in_(allowed)).count()
    )
    with_parent = (
        db.query(Student).join(StudentParentBinding,
                               StudentParentBinding.student_id == Student.student_id)
        .filter(Student.class_id.in_(allowed),
                StudentParentBinding.status == "active")
        .distinct().count()
    )
    return {
        "success": True, "total": total,
        "with_account": with_account,
        "without_account": max(0, total - with_account),
        "with_parent": with_parent,
    }


@router.post("/students/batch-accounts")
async def batch_create_student_accounts(
    body: BatchAccountsRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("student", "update")),
):
    """为选中学生批量开通账号。初始密码**只在本次响应出现一次**，落库为哈希。

    已开通的跳过（不覆盖原密码），结果里如实标注。
    """
    scope = RequestScope.from_user(current_user, db)
    operator = current_user.get("user_id", "")

    created, skipped, denied = [], [], []
    for sid in body.student_ids:
        if not scope.allows_student(sid):
            denied.append(sid)
            continue
        stu = db.query(Student).filter(Student.student_id == sid).first()
        if stu is None:
            denied.append(sid)
            continue
        existing = db.query(Account).filter(Account.student_id == sid).first()
        if existing is not None:
            skipped.append({"student_id": sid, "name": stu.name, "reason": "已有账号"})
            continue

        account = Account(
            account_id=f"acc_{sid}",
            username=sid,
            password_hash=hashlib.sha256(DEFAULT_PASSWORD.encode()).hexdigest(),
            role="student",
            student_id=sid,
            status="active",
        )
        db.add(account)
        created.append({"student_id": sid, "name": stu.name,
                        "username": sid, "password": DEFAULT_PASSWORD})

    if created:
        _audit(db, operator, "student_batch_accounts", ",".join(c["student_id"] for c in created),
               {"created": len(created), "skipped": len(skipped), "denied": len(denied)})
    db.commit()
    return {"success": True, "created": created, "skipped": skipped, "denied": denied}


@router.post("/students/{student_id}/account-status")
async def set_student_account_status(
    student_id: str,
    body: AccountStatusRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("student", "update")),
):
    """停用/启用登录账号（不动学籍审批状态）。"""
    if body.status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="status 只能是 active / disabled")
    if not RequestScope.from_user(current_user, db).allows_student(student_id):
        raise HTTPException(status_code=403, detail="无权操作该学生")

    acc = db.query(Account).filter(Account.student_id == student_id).first()
    if acc is None:
        raise HTTPException(status_code=404, detail="该学生还没有登录账号")
    acc.status = body.status
    acc.updated_at = datetime.utcnow()
    _audit(db, current_user.get("user_id", ""), "student_account_status", student_id,
           {"status": body.status})
    db.commit()
    return {"success": True, "student_id": student_id, "account_status": acc.status}


# ─────────────────────────────────────────────────────────────
# 家长与绑定（批次 2 剩余）
# ─────────────────────────────────────────────────────────────

class BatchParentsRequest(BaseModel):
    parent_ids: List[str]


def _parent_bindings_q(db: Session, allowed: set):
    return (
        db.query(StudentParentBinding, Student, Parent)
        .join(Student, StudentParentBinding.student_id == Student.student_id)
        .join(Parent, StudentParentBinding.parent_id == Parent.parent_id)
        .filter(Student.class_id.in_(allowed))
    )


@router.get("/parents/summary")
async def parent_summary(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("student", "update")),
):
    """家长激活漏斗三段：绑定 → 有账号 → 近 7 天活跃（暂无数据源，诚实返回 null）。"""
    scope = RequestScope.from_user(current_user, db)
    allowed = scope.class_ids()
    if not allowed:
        return {"success": True, "bound": 0, "activated": 0,
                "active_7d": None, "active_7d_available": False}

    bound = (
        db.query(StudentParentBinding)
        .join(Student, StudentParentBinding.student_id == Student.student_id)
        .filter(StudentParentBinding.status == "active", Student.class_id.in_(allowed))
        .count()
    )
    parent_ids = [
        r[0] for r in db.query(StudentParentBinding.parent_id)
        .join(Student, StudentParentBinding.student_id == Student.student_id)
        .filter(StudentParentBinding.status == "active", Student.class_id.in_(allowed))
        .distinct().all()
    ]
    activated = db.query(Account).filter(
        Account.role == "parent", Account.status == "active",
        Account.parent_id.in_(parent_ids),
    ).count() if parent_ids else 0
    return {"success": True, "bound": bound, "activated": activated,
            "active_7d": None, "active_7d_available": False}


@router.get("/parents")
async def list_parents(
    activated: Optional[str] = None,   # yes / no
    q: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("student", "update")),
):
    """家长账号列表：绑定子女数、是否激活、绑定码。"""
    scope = RequestScope.from_user(current_user, db)
    allowed = scope.class_ids()
    if not allowed:
        return {"success": True, "total": 0, "parents": []}

    rows = _parent_bindings_q(db, allowed).all()
    agg: dict[str, dict] = {}
    for binding, student, parent in rows:
        item = agg.setdefault(parent.parent_id, {
            "parent_id": parent.parent_id,
            "name": parent.name,
            "phone": parent.phone,
            "status": parent.status,
            "children": [],
        })
        if binding.status == "active":
            item["children"].append({"student_id": student.student_id,
                                     "name": student.name,
                                     "class_name": None})
    accounts = {
        a.parent_id: a for a in db.query(Account).filter(
            Account.role == "parent", Account.parent_id.in_(list(agg.keys()) or [None])
        ).all()
    }
    out = []
    for pid, item in agg.items():
        acc = accounts.get(pid)
        item["account_status"] = acc.status if acc else "none"
        item["username"] = acc.username if acc else None
        item["children_count"] = len(item["children"])
        item["children"] = item["children"][:3]
        if activated == "yes" and acc is None:
            continue
        if activated == "no" and acc is not None:
            continue
        if q and q.strip() and q.strip() not in f"{item['name']} {item['phone'] or ''}":
            continue
        out.append(item)
    out.sort(key=lambda x: (-x["children_count"], x["name"]))
    return {"success": True, "total": len(out), "parents": out}


@router.post("/parents/batch-accounts")
async def batch_create_parent_accounts(
    body: BatchParentsRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("student", "update")),
):
    """为未激活家长批量开通账号（一次性凭据，落库哈希）。"""
    operator = current_user.get("user_id", "")
    created, skipped = [], []
    for pid in body.parent_ids:
        parent = db.query(Parent).filter(Parent.parent_id == pid).first()
        if parent is None:
            continue
        if db.query(Account).filter(Account.parent_id == pid).first():
            skipped.append({"parent_id": pid, "name": parent.name, "reason": "已有账号"})
            continue
        # 家长自助注册用手机号登录；批量开通沿用同一用户名规则
        username = parent.phone or pid
        if db.query(Account).filter(Account.username == username).first():
            skipped.append({"parent_id": pid, "name": parent.name,
                            "reason": f"登录名 {username} 已被占用"})
            continue
        db.add(Account(
            account_id=f"acc_{pid}",
            username=username,
            password_hash=hashlib.sha256(DEFAULT_PASSWORD.encode()).hexdigest(),
            role="parent",
            parent_id=pid,
            status="active",
        ))
        created.append({"parent_id": pid, "name": parent.name,
                        "username": username, "password": DEFAULT_PASSWORD})
    if created:
        _audit(db, operator, "parent_batch_accounts",
               ",".join(c["parent_id"] for c in created),
               {"created": len(created), "skipped": len(skipped)})
    db.commit()
    return {"success": True, "created": created, "skipped": skipped}


@router.post("/parents/{parent_id}/reset-bind-code")
async def reset_parent_bind_code(
    parent_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("student", "update")),
):
    """重置该家长全部生效绑定的绑定码（旧码立即失效）。"""
    import secrets
    bindings = db.query(StudentParentBinding).filter(
        StudentParentBinding.parent_id == parent_id,
        StudentParentBinding.status == "active",
    ).all()
    if not bindings:
        raise HTTPException(status_code=404, detail="该家长没有生效中的绑定")
    new_codes = []
    for b in bindings:
        b.bind_code = f"B{secrets.token_hex(4).upper()}"
        new_codes.append({"student_id": b.student_id, "bind_code": b.bind_code})
    _audit(db, current_user.get("user_id", ""), "parent_reset_bind_code", parent_id,
           {"count": len(new_codes)})
    db.commit()
    return {"success": True, "parent_id": parent_id, "codes": new_codes}


@router.get("/parent-bindings")
async def list_parent_bindings(
    issue: Optional[str] = None,   # dup / unactivated / over
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("student", "update")),
):
    """绑定关系 + 异常标记：重复手机号 / 未激活 / 一孩多家长（>3）。"""
    scope = RequestScope.from_user(current_user, db)
    allowed = scope.class_ids()
    if not allowed:
        return {"success": True, "total": 0, "bindings": [], "anomalies": {}}

    rows = _parent_bindings_q(db, allowed).all()
    class_names = {c.class_id: c.name for c in db.query(Class).all()}
    account_owner = {
        a.parent_id for a in db.query(Account).filter(Account.role == "parent").all()
    }
    phone_count: dict[str, int] = {}
    for _, _, parent in rows:
        if parent.phone:
            phone_count[parent.phone] = phone_count.get(parent.phone, 0) + 1
    child_count: dict[str, int] = {}
    for binding, _, _ in rows:
        if binding.status == "active":
            child_count[binding.student_id] = child_count.get(binding.student_id, 0) + 1

    out = []
    for binding, student, parent in rows:
        flags = []
        if parent.phone and phone_count.get(parent.phone, 0) > 1:
            flags.append("dup")
        if parent.parent_id not in account_owner:
            flags.append("unactivated")
        if child_count.get(student.student_id, 0) > 3:
            flags.append("over")
        if issue and issue not in flags:
            continue
        out.append({
            "binding_id": binding.binding_id,
            "student_id": student.student_id,
            "student_name": student.name,
            "class_name": class_names.get(student.class_id, ""),
            "parent_id": parent.parent_id,
            "parent_name": parent.name,
            "parent_phone": parent.phone,
            "relation": binding.relation,
            "status": binding.status,
            "bind_code": binding.bind_code,
            "flags": flags,
        })
    out.sort(key=lambda x: (x["status"] != "active", x["student_id"]))
    anomalies = {
        "dup": sum(1 for x in out if "dup" in x["flags"]),
        "unactivated": sum(1 for x in out if "unactivated" in x["flags"]),
        "over": sum(1 for x in out if "over" in x["flags"]),
    }
    return {"success": True, "total": len(out), "bindings": out, "anomalies": anomalies}


@router.delete("/parent-bindings/{binding_id}")
async def revoke_parent_binding(
    binding_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("student", "update")),
):
    """解绑（状态置 revoked，保留历史；学生端只取 active）。"""
    binding = db.query(StudentParentBinding).filter(
        StudentParentBinding.binding_id == binding_id).first()
    if binding is None:
        raise HTTPException(status_code=404, detail="绑定不存在")
    if not RequestScope.from_user(current_user, db).allows_student(binding.student_id):
        raise HTTPException(status_code=403, detail="无权操作该学生")
    binding.status = "revoked"
    binding.updated_at = datetime.utcnow()
    _audit(db, current_user.get("user_id", ""), "parent_binding_revoke", binding_id,
           {"student_id": binding.student_id, "parent_id": binding.parent_id})
    db.commit()
    return {"success": True, "binding_id": binding_id, "status": "revoked"}
