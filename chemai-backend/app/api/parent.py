"""
家长端 API
支持家长注册、登录、子女绑定、通知接收等功能
"""
from fastapi import APIRouter, HTTPException, Depends, Header
from pydantic import BaseModel
from typing import List, Optional
import hashlib
import json
import uuid

from sqlalchemy.orm import Session
from app.models.database import (
    Parent, StudentParentBinding, ParentNotification, Account, Student, Class,
    get_db
)

router = APIRouter()


# ==================== 辅助函数 ====================

def hash_password(password: str) -> str:
    """密码哈希"""
    return hashlib.sha256(password.encode()).hexdigest()


def generate_id(prefix: str = "") -> str:
    """生成唯一ID"""
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def get_current_parent_id(authorization: str = Header(None)) -> str:
    """从 token 解析当前家长 id（不允许通过查询参数伪造他人 parent_id）"""
    from app.middleware.auth import verify_token
    if not authorization:
        raise HTTPException(status_code=401, detail="未登录")
    token = authorization.replace("Bearer ", "")
    payload = verify_token(token)
    if not payload:
        raise HTTPException(status_code=401, detail="token无效或已过期")
    if payload.get("role") != "parent":
        raise HTTPException(status_code=403, detail="仅家长可访问")
    return payload.get("user_id")


# ==================== Request/Response Models ====================

class ParentRegisterRequest(BaseModel):
    """家长注册请求"""
    name: str
    phone: Optional[str] = None
    email: Optional[str] = None
    password: str


class ParentLoginRequest(BaseModel):
    """家长登录请求"""
    username: str
    password: str


class BindStudentRequest(BaseModel):
    """绑定学生请求"""
    student_id: str
    bind_code: str
    parent_id: str = ""
    relation: str = "家长"  # 父亲/母亲/其他


class WeeklyReportResponse(BaseModel):
    """周报响应"""
    student_id: str
    student_name: str
    week_start: str
    week_end: str
    practice_count: int
    practice_completed: int
    accuracy_rate: float
    weak_knowledge_points: List[str]
    barrier_type: str
    streak_days: int


class ChildInfo(BaseModel):
    """子女信息"""
    student_id: str
    student_name: str
    class_name: str
    grade: str
    relation: str
    binding_id: str


class NotificationInfo(BaseModel):
    """通知信息"""
    notification_id: str
    student_id: str
    student_name: str
    type: str
    title: str
    content: Optional[str]
    is_read: bool
    created_at: str


# ==================== API 端点 ====================

@router.post("/register")
async def register_parent(
    data: ParentRegisterRequest,
    db: Session = Depends(get_db)
):
    """注册家长账号"""
    # 检查用户名是否已存在
    # 修复: Python 'or' 优先级导致永远匹配，改用 SQLAlchemy '|' 操作符
    from sqlalchemy import or_
    existing = db.query(Account).filter(
        or_(Account.username == data.phone, Account.username == data.email)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="用户名已存在")

    # 创建家长
    parent_id = generate_id("parent_")
    parent = Parent(
        parent_id=parent_id,
        name=data.name,
        phone=data.phone,
        email=data.email,
        password_hash=hash_password(data.password)
    )
    db.add(parent)

    # 创建账户（使用手机号或邮箱作为用户名）
    username = data.phone or data.email
    account = Account(
        account_id=generate_id("acct_"),
        role="parent",
        username=username,
        password_hash=hash_password(data.password),
        parent_id=parent_id
    )
    db.add(account)
    db.commit()

    return {
        "success": True,
        "message": "注册成功",
        "parent_id": parent_id
    }


@router.post("/login")
async def login_parent(
    data: ParentLoginRequest,
    db: Session = Depends(get_db)
):
    """家长登录"""
    account = db.query(Account).filter(
        Account.username == data.username,
        Account.role == "parent"
    ).first()

    if not account:
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if account.password_hash != hash_password(data.password):
        raise HTTPException(status_code=401, detail="用户名或密码错误")

    if account.status != "active":
        raise HTTPException(status_code=401, detail="账号已被禁用")

    # 获取家长信息
    parent = db.query(Parent).filter(Parent.parent_id == account.parent_id).first()
    if not parent:
        raise HTTPException(status_code=404, detail="家长信息不存在")

    # 生成 JWT token
    from app.middleware.auth import create_access_token, create_refresh_token
    token = create_access_token(parent.parent_id, "parent")
    refresh_token = create_refresh_token(parent.parent_id)

    return {
        "success": True,
        "message": "登录成功",
        "token": token,
        "refresh_token": refresh_token,
        "parent_id": parent.parent_id,
        "name": parent.name,
        "role": "parent"
    }


@router.get("/children")
async def get_children(
    parent_id: str = Depends(get_current_parent_id),
    db: Session = Depends(get_db)
):
    """获取已绑定子女列表"""
    bindings = db.query(StudentParentBinding).filter(
        StudentParentBinding.parent_id == parent_id,
        StudentParentBinding.status == "active"
    ).all()

    children = []
    for binding in bindings:
        student = db.query(Student).filter(Student.student_id == binding.student_id).first()
        if student:
            class_obj = db.query(Class).filter(Class.class_id == student.class_id).first()
            children.append(ChildInfo(
                student_id=student.student_id,
                student_name=student.name,
                class_name=class_obj.name if class_obj else "未知班级",
                grade=class_obj.grade if class_obj else "未知年级",
                relation=binding.relation,
                binding_id=binding.binding_id
            ))

    return {
        "success": True,
        "children": [c.model_dump() for c in children]
    }


@router.post("/bind")
async def bind_student(
    data: BindStudentRequest,
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """绑定学生（通过绑定码），parent_id 取自登录 token，忽略请求体中的伪造值"""

    # 查找学生
    student = db.query(Student).filter(Student.student_id == data.student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="学生不存在")

    # 验证绑定码
    if not student.bind_code or student.bind_code != data.bind_code:
        raise HTTPException(status_code=400, detail="绑定码错误")

    # 检查是否已经绑定
    existing = db.query(StudentParentBinding).filter(
        StudentParentBinding.student_id == data.student_id,
        StudentParentBinding.parent_id == parent_id,
        StudentParentBinding.status == "active"
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail="已经绑定过该学生")

    # 创建绑定关系
    binding = StudentParentBinding(
        binding_id=generate_id("bind_"),
        student_id=data.student_id,
        parent_id=parent_id,
        relation=data.relation,
        bind_code=data.bind_code,
        status="active"
    )
    db.add(binding)
    db.commit()

    return {
        "success": True,
        "message": "绑定成功",
        "binding_id": binding.binding_id,
        "student_name": student.name
    }


@router.delete("/bind/{binding_id}")
async def unbind_student(
    binding_id: str,
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """解除绑定"""
    binding = db.query(StudentParentBinding).filter(
        StudentParentBinding.binding_id == binding_id,
        StudentParentBinding.parent_id == parent_id
    ).first()

    if not binding:
        raise HTTPException(status_code=404, detail="绑定关系不存在")

    binding.status = "inactive"
    db.commit()

    return {
        "success": True,
        "message": "解除绑定成功"
    }


@router.get("/notifications")
async def get_notifications(
    parent_id: str = Depends(get_current_parent_id),
    limit: int = 20,
    offset: int = 0,
    db: Session = Depends(get_db)
):
    """获取通知列表"""
    notifications = db.query(ParentNotification).filter(
        ParentNotification.parent_id == parent_id
    ).order_by(ParentNotification.created_at.desc()).limit(limit).offset(offset).all()

    result = []
    for n in notifications:
        student = db.query(Student).filter(Student.student_id == n.student_id).first()
        result.append(NotificationInfo(
            notification_id=n.notification_id,
            student_id=n.student_id,
            student_name=student.name if student else "未知",
            type=n.type,
            title=n.title,
            content=n.content,
            is_read=n.is_read,
            created_at=n.created_at.isoformat() if n.created_at else ""
        ))

    return {
        "success": True,
        "notifications": [r.model_dump() for r in result]
    }


@router.put("/notifications/{notification_id}/read")
async def mark_notification_read(
    notification_id: str,
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """标记通知已读"""
    notification = db.query(ParentNotification).filter(
        ParentNotification.notification_id == notification_id,
        ParentNotification.parent_id == parent_id
    ).first()

    if not notification:
        raise HTTPException(status_code=404, detail="通知不存在")

    notification.is_read = True
    db.commit()

    return {
        "success": True,
        "message": "已标记为已读"
    }


@router.get("/child/{student_id}/report")
async def get_child_report(
    student_id: str,
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """获取子女报告摘要"""
    # 验证绑定关系
    binding = db.query(StudentParentBinding).filter(
        StudentParentBinding.student_id == student_id,
        StudentParentBinding.parent_id == parent_id,
        StudentParentBinding.status == "active"
    ).first()

    if not binding:
        raise HTTPException(status_code=403, detail="未绑定该学生")

    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="学生不存在")

    class_obj = db.query(Class).filter(Class.class_id == student.class_id).first()

    return {
        "success": True,
        "report": {
            "student_id": student_id,
            "student_name": student.name,
            "class_name": class_obj.name if class_obj else "未知班级",
            "grade": class_obj.grade if class_obj else "未知年级",
            "exercises_completed": student.exercises_completed,
            "barrier_type": student.barrier_type,
            "last_exercise_at": student.last_exercise_at.isoformat() if student.last_exercise_at else None
        }
    }


@router.get("/child/{student_id}/weekly")
async def get_child_weekly(
    student_id: str,
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """获取子女周报（薄 adapter：聚合在 parent_summary_service，REQ-GDS-002）"""
    _require_binding(db, parent_id, student_id)

    from app.services.parent_summary_service import weekly_aggregate
    return {
        "success": True,
        "weekly": WeeklyReportResponse(**weekly_aggregate(db, student_id)).model_dump()
    }


class FrequencyRequest(BaseModel):
    frequency: str  # weekly / daily / off


class NotifyChannelRequest(BaseModel):
    webhook: str  # 飞书/企业微信/钉钉群机器人 webhook 地址（host 白名单识别平台）


class NotifyPrefsRequest(BaseModel):
    types: dict = {}
    quiet_hours: dict = {}
    practice_reminder: dict = {}


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.get("/child/{student_id}/summary")
async def get_child_summary(
    student_id: str,
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """家长摘要三卡（REQ-GDS-001）：未完成 / 即将到期 / 本周动态，无分数明细。

    数据单一真源 parent_summary_service.build_cards；另有 ai-summary 一句话 +
    消灭率 + 「家长现在能做的一件事」（REQ-GDS-005 由前端组卡）。"""
    _require_binding(db, parent_id, student_id)
    from app.services.parent_summary_service import build_cards
    return {"success": True, "cards": build_cards(db, student_id)}


@router.get("/summary-frequency")
async def get_summary_frequency(
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """摘要频率偏好（REQ-GDS-003）。"""
    from app.models.database import Parent
    parent = db.get(Parent, parent_id)
    return {"success": True,
            "frequency": (parent.summary_frequency if parent else None) or "weekly"}


@router.get("/notify-channel")
async def get_notify_channel(
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """外发通知通道（ADR-0029 中国化：飞书/企微/钉钉群机器人 webhook）。"""
    from app.models.database import Parent
    parent = db.get(Parent, parent_id)
    masked = ""
    if parent and parent.notify_webhook:
        w = parent.notify_webhook
        masked = (w[:42] + "…") if len(w) > 46 else w
    return {"success": True,
            "channel": (parent.notify_channel if parent else None) or "",
            "webhook_masked": masked}


@router.put("/notify-channel")
async def set_notify_channel(
    request: NotifyChannelRequest,
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """绑定外发通道：webhook 地址按 host 白名单识别平台（防学业数据外送到任意地址）。"""
    from app.services.notification_service import webhook_channel_of
    from app.models.database import Parent

    url = (request.webhook or "").strip()
    channel = webhook_channel_of(url)
    if not channel:
        raise HTTPException(status_code=400, detail=(
            "无法识别的 webhook 地址：请粘贴飞书（open.feishu.cn）、"
            "企业微信（qyapi.weixin.qq.com）或钉钉（oapi.dingtalk.com）的群机器人地址"))
    parent = db.get(Parent, parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="家长不存在")
    parent.notify_channel = channel
    parent.notify_webhook = url
    db.commit()
    return {"success": True, "channel": channel}


@router.post("/notify-channel/test")
async def test_notify_channel(
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """向已绑定通道发一条测试消息（验证 webhook 真可用）。"""
    from app.models.database import Parent
    from app.services.notification_service import send_webhook_text
    parent = db.get(Parent, parent_id)
    if not parent or not parent.notify_channel or not parent.notify_webhook:
        raise HTTPException(status_code=400, detail="尚未绑定外发通道，请先粘贴 webhook 地址")
    ok, err = send_webhook_text(
        parent.notify_channel, parent.notify_webhook,
        "【智辅化学】外发通道测试消息：绑定成功，孩子的学情摘要将经此通道推送。")
    if not ok:
        raise HTTPException(status_code=502, detail=f"测试消息发送失败：{err}")
    return {"success": True}


@router.get("/notify-prefs")
async def get_notify_prefs(
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """通知偏好（类型开关 + 免打扰时段）：缺省按全开 + 22:00-07:00 兜底。"""
    from app.services.notification_service import DEFAULT_DELIVERY_PREFS
    from app.models.database import Parent
    parent = db.get(Parent, parent_id)
    stored = parent.notify_prefs if parent and isinstance(parent.notify_prefs, dict) else {}
    prefs = json.loads(json.dumps(DEFAULT_DELIVERY_PREFS))
    prefs["types"].update(stored.get("types") or {})
    prefs["quiet_hours"].update(stored.get("quiet_hours") or {})
    prefs["practice_reminder"].update(stored.get("practice_reminder") or {})
    return {"success": True, "prefs": prefs}


@router.put("/notify-prefs")
async def set_notify_prefs(
    request: NotifyPrefsRequest,
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """保存通知偏好：仅接受可开关的三类型（warning 安全类始终送达）+ 免打扰时段。"""
    import re as _re
    from app.services.notification_service import MUTABLE_PARENT_TYPES
    from app.models.database import Parent

    types = {}
    for key, value in (request.types or {}).items():
        if key not in MUTABLE_PARENT_TYPES:
            raise HTTPException(status_code=400, detail=f"未知通知类型: {key}")
        if not isinstance(value, bool):
            raise HTTPException(status_code=400, detail=f"{key} 的值必须是布尔")
        types[key] = value
    qh = request.quiet_hours or {}
    quiet = {"enabled": bool(qh.get("enabled", True))}
    for field in ("start", "end"):
        v = str(qh.get(field) or "")
        if v and not _re.match(r"^([01]\d|2[0-3]):[0-5]\d$", v):
            raise HTTPException(status_code=400, detail=f"{field} 需为 HH:MM 格式")
        quiet[field] = v or ("22:00" if field == "start" else "07:00")

    # 每日练习督学提醒（§11 B4）：只允许 6:00-23:00 整点——凌晨催作业既无意义，
    # 又会绕过免打扰时段那层语义
    pr = request.practice_reminder or {}
    practice_reminder = {}
    if "enabled" in pr:
        if not isinstance(pr["enabled"], bool):
            raise HTTPException(status_code=400, detail="practice_reminder.enabled 必须是布尔")
        practice_reminder["enabled"] = pr["enabled"]
    if "hour" in pr:
        try:
            hour = int(pr["hour"])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail="practice_reminder.hour 必须是整数小时")
        if not 6 <= hour <= 23:
            raise HTTPException(status_code=400, detail="practice_reminder.hour 需在 6-23 之间")
        practice_reminder["hour"] = hour

    parent = db.get(Parent, parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="家长不存在")
    stored = parent.notify_prefs if isinstance(parent.notify_prefs, dict) else {}
    stored_types = stored.get("types") or {}
    stored_types.update(types)
    stored["types"] = stored_types
    stored["quiet_hours"] = {**(stored.get("quiet_hours") or {}), **quiet}
    if practice_reminder:
        stored["practice_reminder"] = {**(stored.get("practice_reminder") or {}),
                                       **practice_reminder}
    parent.notify_prefs = stored
    # JSON 列不做变更追踪：stored 往往就是原来那个 dict 对象，原地改完再赋回去
    # 等于「没变」→ commit 空跑，第二次保存静默失效（实测：先存 hour=19，改成 20
    # 后回读还是 19）。显式打脏标记，第一次之后的每次保存才真的落库。
    from sqlalchemy.orm.attributes import flag_modified
    flag_modified(parent, "notify_prefs")
    db.commit()
    return {"success": True, "prefs": stored}


@router.post("/change-password")
async def change_password(
    request: ChangePasswordRequest,
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """家长修改密码：验旧密码 → 更新 Account.password_hash（登录同一凭据面）。"""
    from app.api.auth import hash_password
    from app.models.database import Account
    if len(request.new_password or "") < 6:
        raise HTTPException(status_code=400, detail="新密码至少 6 位")
    account = db.query(Account).filter(Account.parent_id == parent_id).first()
    if not account:
        raise HTTPException(status_code=404, detail="未找到登录账号")
    if account.password_hash != hash_password(request.old_password or ""):
        raise HTTPException(status_code=400, detail="原密码不正确")
    account.password_hash = hash_password(request.new_password)
    db.commit()
    return {"success": True}


@router.put("/summary-frequency")
async def set_summary_frequency(
    request: FrequencyRequest,
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """设置摘要频率（weekly/daily/off；站内推送与外发摘要同一偏好，REQ-GDS-003）。"""
    if request.frequency not in ("weekly", "daily", "off"):
        raise HTTPException(status_code=400, detail="frequency 必须是 weekly/daily/off")
    from app.models.database import Parent
    parent = db.get(Parent, parent_id)
    if not parent:
        raise HTTPException(status_code=404, detail="家长不存在")
    parent.summary_frequency = request.frequency
    db.commit()
    return {"success": True, "frequency": request.frequency}


def _require_binding(db, parent_id: str, student_id: str) -> None:
    """亲子绑定校验（ weekly/summary/动态 读侧共用，fail-closed）。"""
    binding = db.query(StudentParentBinding).filter(
        StudentParentBinding.student_id == student_id,
        StudentParentBinding.parent_id == parent_id,
        StudentParentBinding.status == "active"
    ).first()
    if not binding:
        raise HTTPException(status_code=403, detail="未绑定该学生")
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail="学生不存在")


# ── AI 报告摘要 ──

class AISummaryRequest(BaseModel):
    section: str
    data: dict

@router.post("/child/{student_id}/report/ai-summary")
async def ai_summary(
    student_id: str,
    request: AISummaryRequest,
    db: Session = Depends(get_db),
    parent_id: str = Depends(get_current_parent_id),
):
    """对报告某个板块做 AI 摘要，返回 2-3 句通俗总结。"""
    # 校验绑定关系
    binding = db.query(StudentParentBinding).filter(
        StudentParentBinding.student_id == student_id,
        StudentParentBinding.parent_id == parent_id,
        StudentParentBinding.status == "active"
    ).first()
    if not binding:
        raise HTTPException(status_code=403, detail="未绑定该学生")
    from app.services.llm_service import llm_service

    section_labels = {
        "overview": "学习概览（练习量、正确率）",
        "trends": "进步趋势（本周vs上周对比）",
        "knowledge_points": "知识点掌握情况",
        "barrier": "学习特点分析",
        "suggestions": "家庭配合建议",
    }
    section_name = section_labels.get(request.section, request.section)

    prompt = f"""你是 ChemAI 家长助手。用 2-3 句通俗中文总结以下数据。语气鼓励、不制造焦虑。40-55岁家长能看懂。

板块: {section_name}
数据: {json.dumps(request.data, ensure_ascii=False)}

要求: 不超过 100 字，先说结论再给建议。"""

    result = llm_service.generate_text(prompt, max_tokens=256, temperature=0.7)
    if result.get("success") and result.get("content", "").strip():
        return {"success": True, "summary": result["content"].strip()}
    # 重试
    result = llm_service.generate_text(prompt, max_tokens=256, temperature=0.7, provider="deepseek")
    if result.get("success") and result.get("content", "").strip():
        return {"success": True, "summary": result["content"].strip()}
    # LLM 双通道都失败：502 让前端保留「生成一句话解读」重试入口，
    # NEVER 把错误文案当摘要正文返回（用户实测会被当数据显示进卡片）
    raise HTTPException(status_code=502, detail="服务暂不可用，请稍后重试")
