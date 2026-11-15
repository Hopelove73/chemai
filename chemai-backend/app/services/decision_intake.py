"""决定入口（Decision Intake）—— 老师的一切决定从这里进来（架构候选 A · S1）。

interface（本轮收拢的分类面，纯读、零写库）：
    normalize_decision(request) -> {action, feedback, draft_id, gate_id} | None
    resume_intent(request, pending_name, plan_tools) -> (decision|None, user_response)
    classify_typed(cid, message, pending, is_plan_gate) -> ("guidance"|"reject"|"void"|"none", 引导文案)
    interrupt_action(decision, user_response) -> "approve"|"reject"|"abandon"
    is_confirmation(text) -> bool

S1 把 langgraph_channel 的三个分类器（_normalize_decision / _resolve_resume_intent /
_classify_typed_text）与 pilot 恢复的 _act 归一化原样收拢；S2 起布尔门三值保留
（abandon/reject 走决定流布尔门分支，批准仍归 legacy 重放，见 resume_intent）。

为什么纯读：ADR-0021 决定工作单元主权 —— 账本与它描述的动作同一次提交，
发生在路由执行侧；intake 若先记账、执行后失败，库里就是一条假凭据。

分层：本模块属 app/services，不 import agent/*；门 kind 问 gate_service，
打字确认词表自 guard 收拢于此（agent.guard 留再导出，方向合法 agent→app）。
"""
import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)


@contextmanager
def _db():
    """一次性只读会话，退出即关（与 channel._entity_db 同纪律，延迟绑定 get_db）。"""
    from app.models import database as _db_mod
    db = next(_db_mod.get_db())
    try:
        yield db
    finally:
        db.close()


# ── 打字确认词表（自 agent/guard.py 收拢，词表不变） ─────────────────
# ADR-0015：确认词只用来「引导点卡」，永不构成批准 —— 词表命中「不要发送」
# 这类否定句时也只产生引导（见 classify_typed），这是打字路径的安全底线。
_CONFIRMATION_WORDS = {
    "同意", "可以", "好的", "好", "行", "确认", "发送", "发吧", "发",
    "批准", "ok", "okay", "yes", "approve", "approved", "没问题", "就这样",
    "发送给学生", "同步给家长", "可以发送", "请发送", "发过去",
}


# ── 门工具分类表（S3 自 channel 收拢：草稿门工具 → 工件路由的唯一真源）──
# 生成类工具（generate_*）生成即门：门登记的 name 是实际调用的工具名，
# 也必须路由到同一工件——缺键会 fallback 到学习计划服务，家长报告打回时
# 查错表报「草稿不存在」（2026-09-20 实测）。_gate_artifact（channel）从这里取。
GATE_DRAFT_TOOLS = {
    "send_learning_plan": ("plan_draft_service", "learning_plan", "学习计划"),
    "generate_learning_plan": ("plan_draft_service", "learning_plan", "学习计划"),
    "send_report_to_parent": ("parent_report_draft_service", "parent_report", "学习报告"),
    "generate_parent_report": ("parent_report_draft_service", "parent_report", "学习报告"),
}
# 兼容别名（/stream、conversation 的门判断）：回退判据与工件路由同源，不另抄一张。
GATE_PLAN_TOOLS = frozenset(GATE_DRAFT_TOOLS)


def is_plan_gate(cid: str, pending) -> bool:
    """当前待批是否**草稿门**（三值决定），而非布尔门。

    DB 开放门的 kind 权威，只有查不到门（登记尚未落库的窗口）才回退工具名
    集合。此前 /stream、_gate_draft_id、/history 各自拿 `name in GATE_PLAN_TOOLS`
    猜，与 kind 并存两套真源——新增草稿门工具忘了同步集合就会错分，布尔分支
    误清待批，C1-Q3 的「有未决门全拦工具」随之失效。
    """
    kind = _open_gate_kind(cid or "")
    if kind:
        return kind == "draft"
    return bool(pending) and pending.get("name") in GATE_PLAN_TOOLS


def is_confirmation(text: str) -> bool:
    """自然语言确认判断：短确认词/短句含确认性动词即命中（仅用于引导，不批准）。"""
    normalized = (text or "").strip().lower().replace("，", "").replace("。", "")
    if normalized in _CONFIRMATION_WORDS:
        return True
    if len(normalized) <= 12 and any(w in normalized for w in ("同意", "批准", "发吧", "发送")):
        return True
    return False


# ── 归一化（原 _normalize_decision） ────────────────────────────────

def normalize_decision(request) -> dict | None:
    """请求 → {action, feedback, draft_id, gate_id} | None。

    decision 对象优先（新协议）；旧字符串只保留协议值映射
    （approved→approve、cancelled→abandon）。
    ADR-0015：中文确认词白名单已废止 —— 打字永不构成批准，批准只来自结构化决定。
    C1(a) fail-closed：陌生 action（拼错/未来第四值/REST 乱传）一律判「非决定」，
    绝不原样透传到 approve 重放路径。
    """
    d = getattr(request, "decision", None)
    if isinstance(d, dict) and d.get("action"):
        action = str(d["action"]).strip().lower()
        if action not in ("approve", "reject", "abandon"):
            return None
        return {
            "action": action,
            "feedback": str(d.get("feedback") or ""),
            "draft_id": str(d.get("draft_id") or ""),
            "gate_id": str(d.get("gate_id") or ""),
        }
    msg = (request.message or "").strip().lower()
    if msg == "approved":
        return {"action": "approve", "feedback": "", "draft_id": "", "gate_id": ""}
    if msg == "cancelled":
        return {"action": "abandon", "feedback": "", "draft_id": "", "gate_id": ""}
    return None


# ── 门 kind 解析（原 _open_gate_kind） ──────────────────────────────

def _open_gate_kind(cid: str) -> str:
    """当前开放门的权威 kind（draft/boolean）；无门或查询失败返回空串。

    门实体自己是唯一真相，不靠工具名集合 + draft_id 猜（会漂）。
    """
    if not cid:
        return ""
    try:
        from app.services.gate_service import gate_service
        with _db() as db:
            og = gate_service.get_open_gate(db, cid)
        return (og or {}).get("kind", "") or ""
    except Exception as e:  # noqa: BLE001
        logger.warning("open gate kind lookup failed cid=%s: %s", cid, e)
        return ""


# ── resume 决定归一（原 _resolve_resume_intent） ────────────────────

def resume_intent(request, pending_name: str = "", plan_tools=None):
    """resume 请求 → (三值决定 or None, user_response)。

    草稿门保留三值决定。布尔门自 S2（grilling Q5）起三值同样保留：
    abandon/reject 走 channel `_gate_decision_stream` 的布尔门分支
    （关门 + 记账 + 步取消）；**批准**仍归 legacy —— decision 置 None、
    只留 user_response="approved"，由 /resume 的 `_direct_replay_events`
    占门重放，那条路才是它的执行面与记账面（2026-09-14 断链：布尔门批准
    被改写 cancelled → 批准等于没批，这条不回退）。

    kind 判据以门实体为权威（_open_gate_kind），查不到门才回退
    工具名集合。plan_tools 缺省用本模块的 GATE_PLAN_TOOLS（唯一真源，S3 起
    channel 不再注入自己的集合）。
    """
    if plan_tools is None:
        plan_tools = GATE_PLAN_TOOLS
    decision = normalize_decision(request)
    _d = getattr(request, "decision", None)
    if decision is None and isinstance(_d, dict) and _d.get("action"):
        return None, ""
    user_response = request.message or ""
    if decision and decision["action"] in ("approve", "reject", "abandon"):
        kind = _open_gate_kind(getattr(request, "conversation_id", None) or "")
        if kind:
            is_plan_gate = kind == "draft"
        else:
            is_plan_gate = (pending_name in plan_tools
                            or bool(decision.get("draft_id")))
        approved = decision["action"] == "approve"
        if not is_plan_gate and approved:
            decision = None          # 布尔门批准：归 legacy 重放路径（见 docstring）
        user_response = "approved" if approved else "cancelled"
    return decision, user_response


# ── 打字语义（原 _classify_typed_text 的分类半段） ──────────────────

_GUIDANCE_TEXT = ("请在上方确认卡上点击「批准 / 打回 / 放弃」处理这个确认请求；"
                  "直接发送文字不会执行任何操作。")


def classify_typed(cid: str, message: str, pending, is_plan_gate: bool) -> tuple:
    """门开着时打字的语义（ADR-0015 决策通道唯一化）。

    返回 (kind, 引导文案)：
      guidance — 确认词 → 引导点卡（零副作用；打字永不构成批准）
      reject   — 其他文字 + 草稿门 → 打回+反馈（本产品的有意领域分歧，
                 见 CONTEXT.md「Decision Intake」词条；主流助手是排队不解释）
      void     — 其他文字 + 布尔门 → 作废待批（S1 语义保持，S2 归一为 abandon）
      none     — 无未决门（按普通消息流转）
    SSE 事件的拼装留在 channel（渲染不是分类）。
    """
    if not pending:
        return ("none", "")
    if is_confirmation(message or ""):
        return ("guidance", _GUIDANCE_TEXT)
    if is_plan_gate:
        return ("reject", "")
    return ("void", "")


# ── pilot interrupt 恢复归一（原 channel /resume 内联 _act 块） ──────

def interrupt_action(decision, user_response: str) -> str:
    """回填给 interrupt() 的 action 必须是协议值 —— 布尔门没有三值决定
    （resume_intent 已置 None），意图只在 user_response（"approved"）里，
    照抄它会让恢复成功的那一轮在工具里当场判成「已拒绝」（2026-10 实测）。"""
    act = (decision or {}).get("action") if isinstance(decision, dict) else ""
    if not act:
        act = ("approve" if (user_response or "").strip().lower() == "approved"
               else "abandon")
    return act
