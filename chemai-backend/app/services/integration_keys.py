"""LMS 集成 API 密钥服务（integration-persistence）。

- 签发：明文只在响应出现一次，落库 SHA-256 哈希（高熵随机串，无盐可接受）。
- 校验：常量时间比对 + 权限清单比对 + 已删除失效 —— 作为可复用能力交付，
  本 change 不接到任何业务端点（真接 LMS 时开放，避免无消费者的认证面）。
- 删除 = 删行，verify 立即失效。
"""
import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Optional
from uuid import uuid4

from sqlalchemy.orm import Session

from app.models.database import IntegrationApiKey


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def create_api_key(db: Session, name: str, permissions: list[str],
                   created_by: str = "") -> dict:
    """签发密钥：返回明文（仅此一次）+ 元数据。"""
    key_id = f"ik_{uuid4().hex[:12]}"
    api_key = f"ck_{secrets.token_hex(16)}"
    api_secret = secrets.token_hex(32)
    row = IntegrationApiKey(
        key_id=key_id, name=name, secret_hash=_hash(api_secret),
        permissions=permissions, status="active", created_by=created_by,
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()
    return {
        "key_id": key_id, "api_key": api_key, "api_secret": api_secret,
        "name": name, "permissions": permissions,
        "created_at": row.created_at.isoformat(),
    }


def list_api_keys(db: Session) -> list[dict]:
    """列表（掩码展示：不回明文/哈希）。"""
    out = []
    for r in db.query(IntegrationApiKey).order_by(IntegrationApiKey.created_at.desc()).all():
        out.append({
            "key_id": r.key_id, "name": r.name, "permissions": r.permissions or [],
            "status": r.status,
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return out


def delete_api_key(db: Session, key_id: str) -> bool:
    """删除（删行 → verify 立即失效）。返回是否真删了。"""
    row = db.get(IntegrationApiKey, key_id)
    if row is None:
        return False
    db.delete(row)
    db.commit()
    return True


def verify_api_key(db: Session, api_secret: str,
                   required_permission: Optional[str] = None) -> tuple[bool, Optional[IntegrationApiKey], str]:
    """校验路径（不接业务端点，供将来 LMS 认证用）。

    返回 (身份是否有效, 密钥行, 拒绝原因)。常量时间比对防时序侧信道。
    """
    if not api_secret:
        return False, None, "missing credential"
    target = _hash(api_secret)
    for r in db.query(IntegrationApiKey).filter(IntegrationApiKey.status == "active").all():
        if hmac.compare_digest(r.secret_hash, target):
            if required_permission and required_permission not in (r.permissions or []):
                return False, r, f"permission denied: {required_permission}"
            return True, r, ""
    return False, None, "invalid credential"
