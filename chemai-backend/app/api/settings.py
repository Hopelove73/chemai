"""运行时开关 API（runtime-settings）—— 白名单旗子的读/改/恢复默认。

门：`settings:read` / `settings:update`（仅 admin；super_admin 经镜像继承）。
修改同事务写审计（旧值 → 新值）。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker
from app.models.database import OperationLog, get_db
from app.services import runtime_settings

router = APIRouter(prefix="/api/settings", tags=["运行时开关"])


@router.get("")
async def list_settings(
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("settings", "read")),
):
    """白名单键列表：生效值 / 默认值 / 是否覆写。"""
    return {"success": True, "settings": runtime_settings.all_settings(db)}


class SettingUpdate(BaseModel):
    value: object  # bool 或 enum 字符串（service 层校验）


@router.put("/{key}")
async def update_setting(
    key: str,
    body: SettingUpdate,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("settings", "update")),
):
    """修改一个白名单键；同事务写审计（旧值→新值）。"""
    try:
        old = runtime_settings.get_setting(key, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        stored = runtime_settings.set_setting(key, body.value, db,
                                              operator=current_user.get("user_id", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.add(OperationLog(
        user_id=current_user.get("user_id"),
        action="runtime_setting_update",
        target_type="runtime_setting",
        target_id=key,
        detail={"old": old, "new": runtime_settings.get_setting(key, db)},
    ))
    db.commit()
    return {"success": True, "key": key, "value": runtime_settings.get_setting(key, db),
            "stored": stored}


@router.delete("/{key}")
async def reset_setting(
    key: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("settings", "update")),
):
    """恢复默认（删覆写行，env 种子值重新生效）。"""
    try:
        old = runtime_settings.get_setting(key, db)
        runtime_settings.reset_setting(key, db)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    db.add(OperationLog(
        user_id=current_user.get("user_id"),
        action="runtime_setting_reset",
        target_type="runtime_setting",
        target_id=key,
        detail={"old": old, "new": runtime_settings.get_setting(key, db)},
    ))
    db.commit()
    return {"success": True, "key": key, "value": runtime_settings.get_setting(key, db)}
