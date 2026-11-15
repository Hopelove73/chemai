"""
P2-3: LMS集成API
提供REST API开放平台、Webhook、LTI 1.3协议支持
"""

from fastapi import APIRouter, HTTPException, Depends, Query
from pydantic import BaseModel
from typing import List, Optional, Dict, Any
from datetime import datetime
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker
from app.models.database import (
    IntegrationConfig, IntegrationLog, IntegrationWebhook, get_db,
)
from app.services import integration_keys
from app.services.lms_connector import (
    get_lms_connector, get_webhook_service, WEBHOOK_EVENT_TYPES, LTI13Connector
)

router = APIRouter()


def _integration_config(db: Session) -> dict:
    """单行配置表读取（integration-persistence）。"""
    row = db.get(IntegrationConfig, 1)
    return (row.config if row else None) or {}


def _save_integration_config(db: Session, config: dict) -> None:
    row = db.get(IntegrationConfig, 1)
    if row is None:
        row = IntegrationConfig(id=1)
        db.add(row)
    row.config = config
    row.updated_at = datetime.utcnow()
    db.commit()


def _log_delivery(db: Session, event_type: str, url: str, results: list) -> None:
    """投递留痕：service.trigger_event 的结果逐条落 integration_logs。"""
    for r in results or []:
        db.add(IntegrationLog(
            log_type="delivery", event_type=event_type, url=r.get("url", url),
            status="success" if r.get("success") else "failure",
            duration_ms=r.get("duration_ms"),
            detail={k: v for k, v in r.items() if k not in ("url",)},
        ))
    db.commit()


# ==================== LMS配置 ====================

class LMSConfigRequest(BaseModel):
    """LMS配置请求"""
    platform: str  # generic/dingtalk/wecom/lti13
    enabled: bool = False
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    api_secret: Optional[str] = None
    app_key: Optional[str] = None
    app_secret: Optional[str] = None
    agent_id: Optional[str] = None
    corp_id: Optional[str] = None
    corp_secret: Optional[str] = None


@router.get("/lms/config")
async def get_lms_config(db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("integration", "read")),
):
    """
    获取LMS配置（integration-persistence：从单行配置表读，重启不丢）
    """
    config = _integration_config(db).get("lms", {})

    def _platform(name: str) -> dict:
        pc = config.get(name, {})
        return {"enabled": bool(pc.get("enabled", False)), "connected": bool(pc.get("connected", False))}

    return {
        "success": True,
        "platforms": {
            "generic": _platform("generic"),
            "dingtalk": _platform("dingtalk"),
            "wecom": _platform("wecom"),
            "lti13": _platform("lti13"),
        }
    }


@router.post("/lms/config")
async def save_lms_config(request: LMSConfigRequest, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("integration", "update")),
):
    """
    保存LMS配置
    """
    # 验证配置
    if request.enabled:
        connector = get_lms_connector(request.platform, {
            "enabled": True,
            "api_url": request.api_url,
            "api_key": request.api_key,
            "api_secret": request.api_secret,
            "app_key": request.app_key,
            "app_secret": request.app_secret,
            "agent_id": request.agent_id,
            "corp_id": request.corp_id,
            "corp_secret": request.corp_secret
        })

        # 尝试连接测试
        test_result = connector.send_assignment({
            "title": "连接测试",
            "content": "这是一条测试消息"
        }) if hasattr(connector, 'send_assignment') else {"success": True}

        connected = test_result.get("success", False)
    else:
        connected = False

    # 持久化（integration-persistence）：按平台写配置单行表
    config_all = _integration_config(db)
    lms = config_all.get("lms", {})
    lms[request.platform] = {
        "enabled": request.enabled,
        "connected": connected,
        "api_url": request.api_url,
        "api_key": request.api_key,
        "api_secret": request.api_secret,
        "app_key": request.app_key,
        "app_secret": request.app_secret,
        "agent_id": request.agent_id,
        "corp_id": request.corp_id,
        "corp_secret": request.corp_secret,
    }
    config_all["lms"] = lms
    _save_integration_config(db, config_all)
    db.add(IntegrationLog(
        log_type="config", event_type="lms_config", url=None,
        status="success" if connected else "failure",
        detail={"platform": request.platform, "enabled": request.enabled, "connected": connected},
    ))
    db.commit()

    return {
        "success": True,
        "platform": request.platform,
        "enabled": request.enabled,
        "connected": connected
    }


@router.post("/lms/test")
async def test_lms_connection(
    platform: str,
    config: Dict[str, Any],
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("integration", "read")),
):
    """
    测试LMS连接
    """
    connector = get_lms_connector(platform, {**config, "enabled": True})

    test_result = connector.send_assignment({
        "title": "连接测试",
        "content": "这是一条测试消息"
    }) if hasattr(connector, 'send_assignment') else {"success": True, "message": "不支持此操作"}

    return test_result


# ==================== Webhook管理 ====================

class WebhookRegisterRequest(BaseModel):
    """Webhook注册请求"""
    event_type: str
    url: str
    secret: Optional[str] = ""


@router.get("/webhooks")
async def list_webhooks(db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("integration", "read")),
):
    """
    列出所有注册的Webhook（integration-persistence：表为真源，重启不丢）。
    secret 永不出现在响应里。
    """
    rows = db.query(IntegrationWebhook).order_by(IntegrationWebhook.created_at.desc()).all()
    webhooks = [{
        "event_type": r.event_type, "url": r.url, "enabled": r.enabled,
        "registered_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]
    return {
        "success": True,
        "count": len(webhooks),
        "webhooks": webhooks,
        "available_events": WEBHOOK_EVENT_TYPES
    }


@router.post("/webhooks")
async def register_webhook(request: WebhookRegisterRequest, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("integration", "create")),
):
    """
    注册Webhook（写表 + 内存服务同步，重启不丢）
    """
    if request.event_type not in WEBHOOK_EVENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的事件类型。可用类型: {', '.join(WEBHOOK_EVENT_TYPES)}"
        )

    service = get_webhook_service()
    result = service.register_webhook(request.event_type, request.url, request.secret)

    row = db.query(IntegrationWebhook).filter(IntegrationWebhook.event_type == request.event_type).first()
    if row is None:
        row = IntegrationWebhook(event_type=request.event_type)
        db.add(row)
    row.url = request.url
    row.secret = request.secret or ""
    row.enabled = True
    row.created_at = datetime.utcnow()
    db.commit()

    return result


@router.delete("/webhooks/{event_type}")
async def unregister_webhook(event_type: str, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("integration", "delete")),
):
    """
    取消注册Webhook（表 + 内存服务同步删除）
    """
    service = get_webhook_service()
    result = service.unregister_webhook(event_type)

    row = db.query(IntegrationWebhook).filter(IntegrationWebhook.event_type == event_type).first()
    if row is not None:
        db.delete(row)
        db.commit()
    elif not result.get("success"):
        raise HTTPException(status_code=404, detail="Webhook不存在")

    return result


@router.post("/webhooks/test")
async def test_webhook(event_type: str, url: str, secret: str = "",
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("integration", "read")),
):
    """
    测试Webhook
    """
    if event_type not in WEBHOOK_EVENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail=f"不支持的事件类型。可用类型: {', '.join(WEBHOOK_EVENT_TYPES)}"
        )

    # 发送测试事件
    test_payload = {
        "event": event_type,
        "timestamp": datetime.now().isoformat(),
        "data": {
            "test": True,
            "message": "这是一条测试Webhook消息"
        }
    }

    # 临时注册并触发
    service = get_webhook_service()
    service.register_webhook(event_type, url, secret)
    results = service.trigger_event(event_type, test_payload)

    # 投递留痕（integration-persistence：测试投递也记日志）
    _log_delivery(db, event_type, url, results)

    return {
        "success": True,
        "event_type": event_type,
        "results": results
    }


# ==================== LTI 1.3 支持 ====================

class LTIDeepLinkRequest(BaseModel):
    """LTI Deep Linking请求"""
    platform_id: str
    deployment_id: str
    content_items: List[Dict[str, Any]]


@router.post("/lti/deep-link")
async def lti_deep_link(request: LTIDeepLinkRequest, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("integration", "create")),
):
    """
    LTI 1.3 Deep Linking
    创建要返回给LMS平台的内容项
    """
    config = {
        "issuer": request.platform_id,
        "deployment_id": request.deployment_id
    }

    connector = LTI13Connector(config)
    result = connector.create_deep_link(request.content_items)

    return result


@router.get("/lti/.well-known/jwks.json")
async def lti_jwks():
    """
    LTI 1.3 JWKS端点
    返回公钥用于验证JWT签名
    """
    # TODO: 实现动态JWKS
    return {
        "keys": [
            {
                "kty": "RSA",
                "use": "sig",
                "alg": "RS256",
                "kid": "chemai-key-1"
            }
        ]
    }


# ==================== REST API开放平台 ====================

@router.get("/api-keys")
async def list_api_keys(db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("integration", "read")),
):
    """
    列出已创建的API Key（掩码展示：不回明文/哈希）
    """
    return {"success": True, "keys": integration_keys.list_api_keys(db)}


@router.post("/api-keys")
async def create_api_key(
    name: str = Query(...),
    permissions: List[str] = Query(...),
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("integration", "create")),
):
    """
    创建新的API Key（明文仅本次响应出现一次，落库哈希）
    """
    result = integration_keys.create_api_key(db, name, permissions,
                                             created_by=current_user.get("user_id", ""))
    db.add(IntegrationLog(
        log_type="config", event_type="api_key_created", url=None, status="success",
        detail={"key_id": result["key_id"], "name": name, "permissions": permissions},
    ))
    db.commit()
    return {
        "success": True,
        **result,
        "message": "请妥善保存API Secret，它不会再显示"
    }


@router.delete("/api-keys/{key_id}")
async def delete_api_key(key_id: str, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("integration", "delete")),
):
    """
    删除API Key（删行 → verify 立即失效）
    """
    deleted = integration_keys.delete_api_key(db, key_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="API Key不存在")
    db.add(IntegrationLog(
        log_type="config", event_type="api_key_deleted", url=None, status="success",
        detail={"key_id": key_id},
    ))
    db.commit()
    return {
        "success": True,
        "message": "API Key已删除"
    }


# ==================== 事件日志 ====================

@router.get("/logs")
async def get_integration_logs(
    event_type: Optional[str] = None,
    platform: Optional[str] = None,
    status: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("integration", "read")),
):
    """
    获取集成日志（integration-persistence：读表，按事件类型/状态筛选）
    """
    query = db.query(IntegrationLog)
    if event_type:
        query = query.filter(IntegrationLog.event_type == event_type)
    if status:
        query = query.filter(IntegrationLog.status == status)
    logs = query.order_by(IntegrationLog.created_at.desc()).limit(max(1, min(limit, 500))).all()
    return {
        "success": True,
        "count": len(logs),
        "logs": [{
            "id": log.id,
            "log_type": log.log_type,
            "event_type": log.event_type,
            "url": log.url,
            "status": log.status,
            "duration_ms": log.duration_ms,
            "detail": log.detail,
            "created_at": log.created_at.isoformat() if log.created_at else None,
        } for log in logs]
    }


# ==================== SFTP文件交换 ====================

class SFTPConfigRequest(BaseModel):
    """SFTP配置请求"""
    enabled: bool = False
    host: Optional[str] = None
    port: int = 22
    username: Optional[str] = None
    password: Optional[str] = None
    upload_path: Optional[str] = None
    download_path: Optional[str] = None


@router.post("/sftp/config")
async def save_sftp_config(request: SFTPConfigRequest, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("integration", "update")),
):
    """
    保存SFTP配置（integration-persistence：写单行配置表）
    """
    config_all = _integration_config(db)
    config_all["sftp"] = request.model_dump()
    _save_integration_config(db, config_all)
    return {
        "success": True,
        "message": "SFTP配置已保存"
    }


@router.get("/sftp/config")
async def get_sftp_config(db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("integration", "read")),
):
    """
    获取SFTP配置（integration-persistence：读单行配置表）
    """
    saved = _integration_config(db).get("sftp", {})
    return {
        "success": True,
        "enabled": saved.get("enabled", False),
        "host": saved.get("host", ""),
        "port": saved.get("port", 22),
        "username": saved.get("username"),
        "upload_path": saved.get("upload_path"),
        "download_path": saved.get("download_path"),
    }


@router.post("/sftp/test")
async def test_sftp_connection(request: SFTPConfigRequest,
    _perm: None = Depends(PermissionChecker("integration", "read")),
):
    """
    测试SFTP连接
    """
    # TODO: 实现SFTP连接测试
    return {
        "success": False,
        "message": "SFTP功能开发中"
    }
