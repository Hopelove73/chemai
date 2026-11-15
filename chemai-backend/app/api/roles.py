"""角色与权限只读展示（role-visibility）。

**不引入第二份权限配置**：矩阵每次实时从 `ROLE_PERMISSIONS` 派生；人数一次 group by。
编辑权限必须发版 —— 界面只负责解释「为什么这个按钮看不到」。
"""
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.middleware.auth import (
    ALL_ROLES, ROLE_LABELS, ROLE_PERMISSIONS, PermissionChecker,
)
from app.models.database import Account, get_db

router = APIRouter(prefix="/api/roles", tags=["角色与权限"])

SCOPE_HINT = {
    "super_admin": "默认空集（跨校需 break-glass 限时提权）",
    "admin": "全校",
    "academic_admin": "全校（不含教师审批与学校设置）",
    "subject_lead": "本学科任教班级",
    "teacher": "本人任教班级",
    "student": "本人",
    "parent": "绑定子女",
}


@router.get("/overview")
async def roles_overview(
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("school", "update")),
):
    counts = dict(db.query(Account.role, func.count()).group_by(Account.role).all())
    roles = []
    for role in ALL_ROLES:
        perms = ROLE_PERMISSIONS.get(role, {})
        roles.append({
            "role": role,
            "label": ROLE_LABELS.get(role, role),
            "count": int(counts.get(role, 0)),
            "scope_hint": SCOPE_HINT.get(role, ""),
            # 扁平矩阵：资源 → 动作列表（前端按资源分组渲染）
            "permissions": {res: sorted(acts) for res, acts in perms.items()},
        })
    return {"success": True, "read_only": True, "roles": roles}
