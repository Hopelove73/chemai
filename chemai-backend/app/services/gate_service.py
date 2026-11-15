"""确认门 service —— Confirmation Gate 的唯一家园（ADR-0015 阶段 2：门合一）。

draft / boolean 两个 adapter 落在同一 seam 后。interface 即测试面：

    open_gate(db, conversation_id, kind, tool_name, kwargs, ...) → gate_id
    get_open_gate(db, conversation_id) → 门卡载荷 dict | None
    check_binding(db, conversation_id, gate_id) → None | {gate_mismatch, open_gate}
    close_gate(db, gate_id, status) → bool   有没有关上（放弃用）
    claim_gate(db, gate_id) → bool   占门：批准生效的唯一凭证（执行不可逆动作前调）
    replay_target(db, conversation_id) → (tool_name, kwargs) | None   重放依据（重启后仍可重建）
    supersede_stale_gates(db, conversation_id="") → int   假门收敛（唯一写库处）
    record_run_scope(db, gate_id) → 授权摘要|None   批准覆盖"这批步骤"（P3 合并门）
    scope_covers(db, conversation_id, tool_name, kwargs) → 授权摘要|None   这次调用已批过了吗
    pending_gates(db, user_id) → 4a 列表载荷（四字段，只读）   见 T8 / D14 / D18
    pending_gate_criteria() → SQL 判据   聊天页与 4a 列表共用的「哪些 open 行算数」

写与读分开（前置一 / `app/services/work_unit.py`）：`open_gate`、`close_gate`、
`close_conversation`、`supersede_stale_gates` 是写入口，只 flush 且必须在最外层调用方
开着的工作单元里；`get_open_gate`、`check_binding`、`replay_target`、`pending_gates`
是读入口，不要求单元、也绝不写库。

    draft_gate_id / boolean_gate_id → gate_id（卡片与实体共用的编号格式）
    gate_open_event(...) → 确认卡 SSE 载荷（首发/重开门/重启重建的唯一构造点）

gate_id 身份：草稿门 g-draft-{draft_id}（跨版本稳定）；布尔门
g-tool-{tool}-{hash8（含会话）}——哈希掺会话，否则两位教师同工具同参数
撞同一全局 UNIQUE 门，后开门者会把先开者的门刷回 open（T18）。
一决定一动作：决定必带 gate_id（旧客户端空 gate_id + 单门开着时放行），
不一致拒绝执行并让调用方重发当前门状态——批准门 A 永不执行门 B 的动作。
"""
import hashlib
import json
import logging
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import and_, func, not_, or_, select
from sqlalchemy.orm import aliased
from app.services.work_unit import require_unit

logger = logging.getLogger(__name__)


def draft_gate_id(draft_id: str) -> str:
    return f"g-draft-{draft_id}"


def boolean_gate_id(conversation_id: str, tool_name: str, kwargs: Dict) -> str:
    """布尔门编号：哈希必须掺 `conversation_id`。

    `gate_id` 是全库 UNIQUE 的，只哈希 tool+kwargs 会让两个会话的同参数调用
    拿到同一个编号——`open_gate` 的 upsert 于是复用同一行，B 教师批准执行的是
    挂在 A 教师会话上的门。
    """
    raw = (f"{conversation_id}|{tool_name}:"
           f"{json.dumps(kwargs or {}, sort_keys=True, ensure_ascii=False, default=str)}")
    return f"g-tool-{tool_name}-{hashlib.sha1(raw.encode()).hexdigest()[:8]}"


# ── 一次批准覆盖这批步骤（P3：一个计划里连着几步撞门，不再连点 N 次）──────
# 判据三条，缺一条就不合并，三条全从**数据**取（模型说"这几步是同一件事"不算数）：
#   ① 对象是**单个**学生/班级 —— kwargs 里有 student_id 或 class_id，且没有
#      student_ids / class_ids 这类"点一片"的列表；
#   ② 动作属确认类（对外发送 / 布置练习）—— 删题库这类不可逆破坏与工作区外读写
#      这类按次审批永不合并；
#   ③ 会话里有一个**活跃 plan run**：授权只在那个 run 里算数，run 一收尾或被新
#      计划顶掉，授权当场失效（见 `scope_covers` 的 run_id 比对）。
# 合并的是"点几次"，不是"看没看过"：确认卡上会点名这次批准**顺带覆盖**哪几步
# （agent/guard.py 的 `_scope_note`），老师在他点的那张卡上就看得见它会连带放行什么。
BATCH_MERGE_TOOLS = frozenset({
    # 发送侧：批准后重放的就是这两个（agent/guard.py 的 `_GATE_OPEN_PAIRS`）
    "send_learning_plan", "send_report_to_parent",
    # 布尔门：布置练习
    "assign_adaptive_practice",
    # ⚠️ 生成侧（generate_learning_plan / generate_parent_report）**刻意不在**这里。
    # 「生成即门」是草稿获得老师批准的唯一入口：把它并进一次批量授权，等于让老师
    # 批准一份**没看过**的内容（ADR-0015 想避免的正是这个）。
    # 2026-10 真机实测的代价：合并门吞掉 generate_parent_report 那扇门 → 家长报告草稿
    # 从未 teacher_approved → 第 7 步 send_report_to_parent 查不到可发送的草稿 →
    # 返回 no_draft_to_send → 那一步永远停在 running，计划卡永久「进行中」。
})

# 授权行自己的状态：它**不是一扇等人决定的门**，而是"这次批准覆盖了哪一批"的记录。
# 所有按 status 过滤的读路径（get_open_gate / pending_gates / 过期作业）都看不见它。
SCOPE_STATUS = "authorized"
SCOPE_KIND = "scope"

# 一次"点一片人"的参数键：出现即不属「同一对象」→ 逐条批准。
_OBJECT_LIST_KEYS = ("student_ids", "class_ids", "students")


def gate_object_key(kwargs: Dict) -> str:
    """这次确认动作针对的**单一**对象：`student:<id>` / `class:<id>` / 空串。

    空串 = 不属「同一对象」（没写 id、只写了姓名、或一次点名一片学生）。空串一律
    不可合并：群发 30 个学生的批准是老师逐个看人的动作，合并成一次等于把 30 个人的
    发送绑成一票（P3 硬约束：跨对象群发必须保留单独批准）。
    """
    kw = kwargs if isinstance(kwargs, dict) else {}
    if any(kw.get(k) for k in _OBJECT_LIST_KEYS):
        return ""
    sid = str(kw.get("student_id") or "").strip()
    if sid:
        return f"student:{sid}"
    class_id = str(kw.get("class_id") or "").strip()
    if class_id:
        return f"class:{class_id}"
    return ""


def scope_gate_id(conversation_id: str, object_key: str) -> str:
    """授权行编号：一个会话里一个对象一行（幂等 upsert 靠它）。

    掺会话的理由同 `boolean_gate_id`：gate_id 全库 UNIQUE，只哈希对象会让两位老师
    批准同一个学生时复用同一行，后一批覆盖前一批的 run。
    """
    raw = f"{conversation_id}|scope|{object_key}"
    return f"g-scope-{hashlib.sha1(raw.encode()).hexdigest()[:8]}"


def compute_covered_steps(steps, object_key: str) -> List[int]:
    """本计划里**还没执行**的、同一对象的确认类步骤号（纯函数）。

    这串步号就是「一次批准覆盖的那批步骤」：卡上点名它们，批准后走到这几步时
    不再弹第二张卡。已到终态（completed/failed/cancelled）的步不列 —— 那些事已经
    办完或已作废，再授权一遍只会让卡上的覆盖面说得比事实多。

    类的集合取自本文件的 `BATCH_MERGE_TOOLS`（唯一真源），不在别处再抄一张表。
    """
    if not object_key:
        return []
    covered: List[int] = []
    for s in steps or []:
        if not isinstance(s, dict) or s.get("skill") not in BATCH_MERGE_TOOLS:
            continue
        if s.get("status") not in ("pending", "running", "awaiting_confirmation"):
            continue
        args = s.get("args") if isinstance(s.get("args"), dict) else {}
        if gate_object_key(args) != object_key:
            continue
        try:
            covered.append(int(s.get("step")))
        except (TypeError, ValueError):
            continue
    return covered


# ── 重放载荷指纹（gate-lifecycle 2.6）──────────────────────────────
# 载荷结构/算法版本。改结构或改算法就 +1：旧行带着旧版本号，重放前被拒，
# 而不是被新代码按今天的理解执行。
REPLAY_PAYLOAD_SCHEMA = 1


# ── 状态转移表（架构候选 B：转移合法性的唯一说法）──────────────────────
# 唯一写点 transition()/transition_bulk() 按这张表校验；加状态或加边先改这里，
# 测试面（tests/test_gate_transition.py）随之确定。
# 铁律：终态（executed/cancelled/superseded）不出现在 from 位——终态行不被改写。
LEGAL_TRANSITIONS = frozenset({
    ("open", "executing"),       # claim_gate：批准占门
    ("open", "executed"),        # pilot 批准即收口（图内挂起，恢复即执行）
    ("open", "cancelled"),       # 放弃/打回/打字作废
    ("open", "superseded"),      # 开门互斥 / T17 过期
    ("executing", "executed"),   # finish_gate(ok)
    ("executing", "open"),       # finish_gate(失败退回，老师可重试)
    ("executing", "in_doubt"),   # 启动扫描：进程死了，结果未知
    ("executing", "superseded"), # 重放载荷指纹不符，卡不可信
    ("executing", "cancelled"),  # 占门后在执行前反悔/收口（含测试收口形状）
    ("in_doubt", "open"),        # 老师裁定：重试
    ("in_doubt", "executed"),    # 老师裁定：确认已执行
    ("authorized", "superseded"),  # 批量授权（scope 行）随会话收口
})

# decided_at 的三种处置：置当前时刻 / 清空 / 不动（缺省）
_DT_NOW, _DT_CLEAR = "now", "clear"


def transition(db, gate_id: str, to_status: str, allowed_from: tuple = (),
               decided_at=_DT_NOW) -> bool:
    """门状态转移的**唯一单行写点**（架构候选 B）。

    条件更新：行当前状态在 allowed_from 里才转移，返回有没有转移上
    （没转移上=行已不在预期状态，调用方按自己的语义收场）。
    (from, to) 不在 LEGAL_TRANSITIONS 是**程序员错误**——当场抛 ValueError，
    绝不静默写出表外形状的状态行（fail-loud：转移表错了要红，不要潜伏）。

    decided_at："now"=置决定时刻（缺省），"clear"=清掉（退回待确认不留旧时刻），
    其余值=不动这一列。
    """
    require_unit(db)
    from app.models.database import AgentGate as _Gate
    for f in allowed_from:
        if (f, to_status) not in LEGAL_TRANSITIONS:
            raise ValueError(f"非法转移 {f} → {to_status}：不在 LEGAL_TRANSITIONS")
    values = {"status": to_status}
    if decided_at == _DT_NOW:
        values["decided_at"] = datetime.utcnow()
    elif decided_at == _DT_CLEAR:
        values["decided_at"] = None
    done = db.query(_Gate).filter(
        _Gate.gate_id == gate_id, _Gate.status.in_(allowed_from),
    ).update(values, synchronize_session=False)
    if done:
        db.flush()
    return bool(done)


def transition_bulk(db, from_statuses: tuple, to_status: str, *extra_filters) -> int:
    """门状态转移的**批量写点**（收敛器/会话收口用）：同表校验，返回转移行数。"""
    require_unit(db)
    from app.models.database import AgentGate as _Gate
    for f in from_statuses:
        if (f, to_status) not in LEGAL_TRANSITIONS:
            raise ValueError(f"非法转移 {f} → {to_status}：不在 LEGAL_TRANSITIONS")
    n = (db.query(_Gate)
         .filter(_Gate.status.in_(from_statuses), *extra_filters)
         .update({"status": to_status}, synchronize_session=False))
    if n:
        db.flush()
    return n


def _conv_filter(conversation_id: str) -> tuple:
    from app.models.database import AgentGate as _Gate
    return (_Gate.conversation_id == conversation_id,)


def replay_payload_hash(tool_name: str, kwargs: Dict) -> str:
    """重放载荷指纹 = `tool_name + kwargs` 的规范化摘要。

    只覆盖重放真正要用的那两样东西（`_TOOL_BY_NAME[tool](**kwargs)`），
    不掺会话/时间 —— 指纹回答的是「要执行的还是开门时钉住的那一份吗」。
    """
    raw = json.dumps({"tool": tool_name or "", "kwargs": kwargs or {}},
                     sort_keys=True, ensure_ascii=False,
                     separators=(",", ":"), default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


# gate_open 事件的收件人语义（ADR-0015 阶段 4）：前端按钮文案由此而来，
# 非发送类动作（含布尔门、未来新增 action）一律 system → 「批准」。
_GATE_RECIPIENT_BY_ACTION = {"send_parent": "parent", "send_student": "student"}

# 4a 第一列的动作词（D1）。查不到 = 空串，前端只渲染对象名，NEVER 显示 `kind` 原值。
_GATE_ACTION_LABELS = {"send_parent": "发送给家长", "send_student": "发送给学生"}

# 第三级兜底的工件人话（D18：`draft_id` 取不到草稿行、且 `message` 也空时用它）。
_GATE_ARTIFACT_LABELS = {"send_parent": "家长报告", "send_student": "学习方案（发学生）"}

# 布尔门的工具→人话映射（D18）。只覆盖**能开布尔门的**那几个工具：护栏前置审批
# （`assign_adaptive_practice` / `delete_bank`）与按次审批（工作区外读写、bash）。
# `summary_from` 取 kwargs 的哪个键，`summary_kind` 决定怎么把它变成人话——
# NEVER 把整段 kwargs JSON 露给老师（D18 验收③：页面上不出现 `{`、`}`、`student_id`）。
_GATE_TOOL_LABELS = {
    "bash": {"label": "执行命令", "summary_from": "command", "summary_kind": "text"},
    "read": {"label": "读取文件", "summary_from": "path", "summary_kind": "filename"},
    "write": {"label": "写入文件", "summary_from": "path", "summary_kind": "filename"},
    "edit": {"label": "修改文件", "summary_from": "path", "summary_kind": "filename"},
    "delete_bank": {"label": "删除题库", "summary_from": "set_id", "summary_kind": "text"},
    "assign_adaptive_practice": {
        "label": "发放自适应练习", "summary_from": "student_id", "summary_kind": "student"},
}


def gate_action_label(action: str) -> str:
    return _GATE_ACTION_LABELS.get(action or "", "")


def _brief(text: str, limit: int = 40) -> str:
    """压成一行并截断（命令 / 路径 / id 摘要用）。"""
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[:limit] + "…"


def _boolean_subject(tool_name: str, kwargs: Dict, student_labels: Dict) -> str:
    """布尔门的对象名：`<工具人话> · <关键参数摘要>`，摘要查不到时用工具人话本身。

    工具不在映射表 ⇒ 显示 `tool_name` 原值（D18：NEVER 显示整段 `kwargs` JSON）。
    """
    meta = _GATE_TOOL_LABELS.get(tool_name or "")
    if not meta:
        return _brief(tool_name) or "待确认动作"
    raw = str((kwargs or {}).get(meta["summary_from"], "") or "")
    if meta["summary_kind"] == "filename":
        summary = _brief(raw.replace("\\", "/").rsplit("/", 1)[-1])
    elif meta["summary_kind"] == "student":
        # 与 draft 门同一口径：查不到该 id 就显示 id 原值，NEVER 空串
        summary = _brief(student_labels.get(raw) or raw)
    else:
        summary = _brief(raw, 24)
    return f"{meta['label']} · {summary}" if summary else meta["label"]


def _latest_draft_rows(db, draft_ids) -> Dict:
    """每条草稿链的 MAX(version) 行（一次 IN 批查，两张表各一条，NEVER 逐行查）。

    同一份结果喂两处：D18 的 `student_id` 取数源（R17/D23=A：`kwargs.student_id`
    只是副本，regenerate 会换 `draft_id` 另开新链，照副本取会认错人）。
    返回 `{draft_id: (student_id, status)}`。
    """
    from app.models.database import LearningPlanDraft as _LP, ParentReportDraft as _PR
    ids = [d for d in set(draft_ids or ()) if d]
    if not ids:
        return {}
    latest: Dict = {}
    for model in (_LP, _PR):
        rows = (db.query(model.draft_id, model.version, model.status, model.student_id)
                .filter(model.draft_id.in_(ids)).all())
        best: Dict = {}
        for did, version, status, sid in rows:
            if did not in best or (version or 0) > best[did][0]:
                best[did] = (version or 0, sid, status)
        for did, (_v, sid, status) in best.items():
            latest.setdefault(did, (sid, status))
    return latest


def _student_labels(db, student_ids) -> Dict:
    """`{student_id: "姓名(班级)"}`，一次 IN 批查。

    班级取 `classes.name`，没有班级行时退到 `class_id`；学生行整体缺失（被删）时
    **不**进这张表，由调用方显示 `student_id` 原值（D18：NEVER 空单元格）。
    """
    from app.models.database import Class as _Class, Student as _Student
    ids = [s for s in set(student_ids or ()) if s]
    if not ids:
        return {}
    rows = (db.query(_Student.student_id, _Student.name,
                     _Class.name, _Student.class_id)
            .outerjoin(_Class, _Student.class_id == _Class.class_id)
            .filter(_Student.student_id.in_(ids)).all())
    return {sid: f"{name or sid}({class_name or class_id or '无班级'})"
            for sid, name, class_name, class_id in rows}


def _gate_label_sources(db, rows) -> tuple:
    """两张批查表，喂第一列的文案：`(drafts, students)`。

    查询形状是 D18 定的：**一次草稿链 + 一次学生表**，NEVER 逐行查。
    `pending_gates` 与 `in_doubt_gates` 共用（两处渲染同一副卡片形状）。
    """
    drafts = _latest_draft_rows(
        db, [r.draft_id for r in rows if r.kind == "draft" and r.draft_id])
    student_ids = {sid for sid, _status in drafts.values() if sid}
    for r in rows:
        meta = _GATE_TOOL_LABELS.get(r.tool_name or "")
        if meta and meta["summary_kind"] == "student":
            sid = str((r.kwargs or {}).get(meta["summary_from"], "") or "")
            if sid:
                student_ids.add(sid)
    return drafts, _student_labels(db, student_ids)


def _draft_subject(row, drafts, students) -> str:
    """draft 门对象名的三级兜底（D18）：草稿行的学生 → message → 工件人话。

    最后一档是常量，所以这一列永不为空（D18 验收④ / NEVER 空单元格）。
    """
    sid = (drafts.get(row.draft_id or "") or (None, None))[0]
    if sid:
        return students.get(sid) or sid
    return (_brief(row.message or "", 60)
            or _GATE_ARTIFACT_LABELS.get(row.action or "")
            or "待确认动作")


def _gate_item(row, drafts, students) -> Dict:
    """一行门 → 卡片载荷（4a 与「结果未知」清单共用的形状）。"""
    return {
        "gate_id": row.gate_id,
        "conversation_id": row.conversation_id,
        "kind": row.kind,
        "action": row.action or "",
        "tool_name": row.tool_name or "",
        "action_label": gate_action_label(row.action),
        "subject_label": (_draft_subject(row, drafts, students) if row.kind == "draft"
                          else _boolean_subject(row.tool_name, row.kwargs, students)),
        "message": row.message or "",
        "last_opened_at": (row.last_opened_at.isoformat()
                           if row.last_opened_at else None),
    }


def _ownership_predicates(user_id: str):
    """4a 两段的归属谓词（R20/D25=A），与 `owns_conversation` 的 True 集等价。

    - `owned`：该会话在 `conversation_owners` 有**登记给这个人**的行
    - `unowned`：该会话**没有任何**登记行，且 id 以 `t-` / `c` 开头
      （`STAFF_LEGACY_PREFIXES` —— 归属记录上线前的员工老会话）

    别人的登记会话两段都不出现；无登记的 `m-` / `p-` 学生会话门一律不出现
    （聊天页对本人角色之外的访问返回 False，摆进教师队列就是一次跨角色露出）。
    """
    from app.middleware.conversation import STAFF_LEGACY_PREFIXES
    from app.models.conversation_owner import ConversationOwner
    from app.models.database import AgentGate as _Gate

    mine = (select(ConversationOwner.thread_id)
            .where(ConversationOwner.thread_id == _Gate.conversation_id,
                   ConversationOwner.user_id == user_id)
            .correlate(_Gate).exists())
    registered = (select(ConversationOwner.thread_id)
                  .where(ConversationOwner.thread_id == _Gate.conversation_id)
                  .correlate(_Gate).exists())
    # 前缀匹配走 substr 而不是 LIKE：LIKE 里 `_` 是单字符通配，`t-%` 会把 `tXabc`
    # 也算进来，与 Python 侧的 `startswith` 不再等价——而验收⑫ 要的正是这个等价式。
    legacy = or_(*[
        func.substr(_Gate.conversation_id, 1, len(p)) == p
        for p in STAFF_LEGACY_PREFIXES
    ])
    return mine, and_(not_(registered), legacy)


OWNED_LIMIT = 50      # D14：上段（你名下）的 SQL 层 LIMIT
UNOWNED_LIMIT = 20    # R15/D22：折叠段自己的上限，两个名额互不占用


def gate_recipient(action: str) -> str:
    return _GATE_RECIPIENT_BY_ACTION.get(action or "", "system")


def gate_open_event(gate_id: str, action: str, message: str,
                    draft_id: str = "", version: Optional[int] = None,
                    tool_name: str = "") -> Dict:
    """确认卡（gate_open 事件）载荷的唯一构造点。

    此前 channel 三处 + adapter 一处各自手写，已漂出三类不一致：卡片编号
    用字面量重述 `draft_gate_id` 的格式（改格式即与实体编号不等，`check_binding`
    误判错门、老师点批准被拒）、recipient 缺 system 分支、可选字段一处恒发
    空串/0 一处按有无省略。首发/重开门/重启重建必须逐字段相同。

    `tool_name`（2026-10-26）：布尔门文案是通用话术（「此操作需要老师确认…」），
    终态小票要显示「已批准执行：布置练习」就得知道工具名。活卡翻面（点击批准/
    放弃的乐观更新）只改状态不动文案——不带 tool_name 的话，翻转那一刻只能渲染
    裸的「已放弃」，要等刷新走账本才有标签（8 步长链实测）。
    """
    event: Dict = {
        "type": "gate_open",
        "gate_id": gate_id or "",
        "message": message or "请确认操作",
        "action": action or "",
        "recipient": gate_recipient(action),
    }
    if tool_name:
        event["tool_name"] = tool_name
    if draft_id:
        event["draft_id"] = draft_id
    if version is not None:
        event["version"] = version
    return event


def pending_gate_criteria():
    """「哪些 open 行算数」的唯一判据（D17 + eng 二轮 R14/D21=A）。

    一条 SQL 条件表达式，两处消费：聊天页 `get_open_gate` 与 4a 待确认列表（T8）。
    写成表达式而不是"按会话调一次的函数"，是为了让 `LIMIT 50` 与 `COUNT` 挂在
    同一个已过滤集合上——先截断再逐行过滤，假门会占掉展示名额，角标与页面分叉。

    判据两条，都不写库：
    ① 每会话只认最新一行的 open（同会话多行 open 是历史双登记留下的存量）；
    ② `kind='draft'` 且带 `draft_id` 的行，要求该 `draft_id` 的 **MAX(version)
       那一行**仍是 `awaiting_confirmation`。

    NEVER 把 ② 简写成 `draft_id IN (SELECT draft_id FROM 草稿表 WHERE
    status='awaiting')`：草稿是一条版本链，`plan_draft_service.send_draft` 只把
    命中 `(draft_id, version)` 的那一行置 `sent`，同链更早的版本行永远留在待确认态，
    按 `IN` 问会问出"还在等确认"的错答案，假门原地复活。

    `draft_id` 为空（NULL 或空串）的 draft 门不做草稿校验、照常算数（R23）：
    `open_gate` 允许这种行存在，聊天页今日也返回它，判据两边必须一致。
    """
    from app.models.database import (
        AgentGate as _Gate, LearningPlanDraft as _LP, ParentReportDraft as _PR)

    newer = aliased(_Gate)
    is_latest_open = _Gate.id == (
        select(func.max(newer.id))
        .where(newer.conversation_id == _Gate.conversation_id,
               newer.status == "open")
        .correlate(_Gate)
        .scalar_subquery()
    )

    def _latest_version_awaiting(model):
        d = aliased(model)
        top_version = (select(func.max(d.version))
                       .where(d.draft_id == _Gate.draft_id)
                       .correlate(_Gate)
                       .scalar_subquery())
        return (select(d.draft_id)
                .where(d.draft_id == _Gate.draft_id,
                       d.version == top_version,
                       d.status == "awaiting_confirmation")
                .correlate(_Gate)
                .exists())

    draft_still_awaiting = or_(
        _Gate.kind != "draft",
        _Gate.draft_id.is_(None),
        _Gate.draft_id == "",
        _latest_version_awaiting(_LP),
        _latest_version_awaiting(_PR),
    )
    return [is_latest_open, draft_still_awaiting]


class GateService:
    @staticmethod
    def open_gate(db, conversation_id: str, kind: str, tool_name: str,
                  kwargs: Dict, draft_id: str = "", version: int = 0,
                  action: str = "", message: str = "") -> str:
        """开门（幂等 upsert）：同会话旧开着的门置 superseded（互斥），返回 gate_id。

        只 flush 不提交（前置一）：门状态、决定日志、草稿行由最外层那一个工作单元
        一次带走。没有单元时 `require_unit` 直接抛 —— 漏包通路要的是当场 500，
        不是"页面看着成功、门其实没开"。
        """
        require_unit(db)
        if not conversation_id:
            return ""
        # 临时诊断（2026-10）：家长报告那条链上出现了第二扇 g-tool-send_report_to_parent，
        # 三次定位都猜错分支。这里把调用栈打出来，一次跑就能看出是谁登记的。
        try:
            import inspect as _insp
            logger.warning("GATE_OPEN_CALLED kind=%s tool=%s caller=%s stack=%s",
                           kind, tool_name,
                           _insp.stack()[1].function,
                           [f.function for f in _insp.stack()[1:5]])
        except Exception:  # noqa: BLE001
            pass
        from app.models.database import AgentGate as _Gate
        gid = draft_gate_id(draft_id) if kind == "draft" and draft_id \
            else boolean_gate_id(conversation_id, tool_name, kwargs or {})
        now = datetime.utcnow()
        db.query(_Gate).filter(
            _Gate.conversation_id == conversation_id, _Gate.status == "open",
            _Gate.gate_id != gid,
        ).update({"status": "superseded"}, synchronize_session=False)
        # ⚠ 按列查询而非 db.get（PK 是自增 id，不是 gate_id）——原实现
        # upsert 分支永不命中，重开门 UNIQUE 冲突被吞（code-review 2026-09-20）
        row = db.query(_Gate).filter(_Gate.gate_id == gid).first()
        if row:
            # 复位守卫（架构候选 B，审查 I-2 的双执行口子）：
            # · executing：占门的那次批准还在执行中，复位=绕过 claim 直接放行
            # · in_doubt：上一次执行结果未知（进程崩过），复位后老师再批准就会
            #   把可能已执行过的破坏性动作跑第二遍——必须先裁定
            if row.status in ("executing", "in_doubt"):
                raise ValueError(
                    f"确认门 {gid} 处于 {row.status}，不得复位重开："
                    + ("占门执行中的动作结果未落定" if row.status == "executing"
                       else "结果未知，须先在「结果待核实」里裁定"))
            # 复位也必须改归属：同一条草稿在两处会话各开一次门时，不改
            # conversation_id 会让这行永远挂在首次那个会话上（T18）
            row.conversation_id = conversation_id
            row.status = "open"
            row.kind = kind
            row.tool_name = tool_name
            row.kwargs = kwargs or {}
            row.version = version
            row.action = action
            row.message = message
            row.decided_at = None
            row.last_opened_at = now
        else:
            row = _Gate(
                gate_id=gid, conversation_id=conversation_id, kind=kind,
                tool_name=tool_name, kwargs=kwargs or {},
                draft_id=draft_id or None, version=version or None,
                action=action, message=message, status="open",
                last_opened_at=now,
            )
            db.add(row)
        # 载荷指纹在**两个分支都要写**：复位分支（同 gate_id 重开门）不改的话，
        # 打回重开那一次会带着上一版的指纹过去，重放前校验立刻误报。
        row.args_hash = replay_payload_hash(tool_name, kwargs or {})
        row.schema_version = REPLAY_PAYLOAD_SCHEMA
        db.flush()
        # 开门顺带收敛本会话的假门（T4/R22）：判据里剩下的"不算数"行在这里落库，
        # 不挂在读路径上——读路径写库会把决定单元中途别人的事务提交掉
        GateService.supersede_stale_gates(db, conversation_id)
        return gid

    @staticmethod
    def get_open_gate(db, conversation_id: str) -> Optional[Dict]:
        """当前待确认门 —— 纯读，不写库、不 commit（T4）。

        收敛假门是 `supersede_stale_gates` 的活（开门路径与每日作业调）。挂在这条
        路径上的后果实测过两次：`check_binding` / `replay_target` 都经此函数，
        于是一次"读"把活动工作单元提交掉，并在只写不提交的会话上握着 WAL 写锁。
        """
        if not conversation_id:
            return None
        from app.models.database import AgentGate as _Gate
        row = (db.query(_Gate)
               .filter(_Gate.conversation_id == conversation_id,
                       _Gate.status == "open",
                       *pending_gate_criteria())
               .order_by(_Gate.id.desc())
               .first())
        if not row:
            return None
        return {
            "gate_id": row.gate_id, "kind": row.kind,
            "tool_name": row.tool_name, "kwargs": row.kwargs or {},
            "draft_id": row.draft_id or "", "version": row.version or 0,
            "action": row.action, "message": row.message,
            # 本门绑的计划步号；0 = 未记录（本列之前的存量门），调用方回退旧行为
            "plan_step": row.plan_step or 0,
        }

    @staticmethod
    def supersede_stale_gates(db, conversation_id: str = "", draft_id: str = "") -> int:
        """把"库里 open、但已不算数"的门行落成 superseded，返回收敛行数。

        唯一的假门写库点（前置二）。三种调用法：传 `conversation_id` 收敛单会话
        （`open_gate` 之后）、传 `draft_id` 收敛某个草稿名下的（**发送落定之后**，
        gate-lifecycle 2.2）、留空跑全库 —— 全库那一次归 T17 的每日作业。
        判据与 `get_open_gate` 同源（`pending_gate_criteria`），所以页面不露出的行
        一定会在这里消失——否则 `stats`、决定日志与 4b 的过期计数会永久被假门污染。

        和其余写入口一样只 flush：作业侧包一层 `new_work_unit()`，开门侧与发送侧
        由各自的决定单元一并提交。
        """
        from app.models.database import AgentGate as _Gate
        require_unit(db)
        stale = (db.query(_Gate)
                 .filter(_Gate.status == "open",
                         not_(and_(*pending_gate_criteria()))))
        if conversation_id:
            stale = stale.filter(_Gate.conversation_id == conversation_id)
        if draft_id:
            stale = stale.filter(_Gate.draft_id == draft_id)
        count = stale.update(
            {"status": "superseded", "decided_at": datetime.utcnow()},
            synchronize_session=False)
        if count:
            db.flush()
        return count

    @staticmethod
    def pending_gates(db, user_id: str, kind: str = "") -> Dict:
        """4a 的只读列表（T8）：四字段各管一处，全程不写库、不 commit。

        - `items`（≤ `OWNED_LIMIT`）：你名下，`last_opened_at ASC`（等最久的在前）
        - `owned_total`：上段全量 —— 侧栏胶囊读它（D2/D3，NEVER 用 `items.length`）
        - `unowned_items`（≤ `UNOWNED_LIMIT`）：归属记录上线前的员工老会话，折叠段
        - `unowned_total`：折叠段全量 —— 段头读它，`> 上限` 时段头标注截断

        四段查询挂在**同一份**已过滤集合上（假门谓词 + 归属谓词），所以"假门不占
        展示名额"成立（D17/R14）。第一列的两半（`action_label` / `subject_label`）
        在这里算好下发，前端 NEVER 解析 `kwargs`、NEVER 二次查学生表（D18）。

        布尔门的 `action_label` 是空串：它的动作词就是工具人话，已经含在
        `subject_label`（「执行命令 · 批卷脚本」）里，两处都发会让第一列重复一遍。

        `kind`（`draft` / `boolean`）是 D14 的唯一筛选维度：加在假门与归属谓词同一层、
        LIMIT 之前，所以启用筛选后四字段一起收窄，计数与页面不会互相打脸。
        """
        from app.models.database import AgentGate as _Gate
        if not user_id:
            return {"items": [], "owned_total": 0,
                    "unowned_items": [], "unowned_total": 0}
        criteria = [_Gate.status == "open", *pending_gate_criteria()]
        if kind:
            # D14 的唯一筛选维度：筛选发生在 LIMIT 之前，四字段随筛选一起收窄
            criteria.append(_Gate.kind == kind)
        mine, unowned = _ownership_predicates(user_id)
        order_by = (_Gate.last_opened_at.asc(), _Gate.id.asc())

        def segment(predicate, limit):
            """同一条谓词取两份：行数喂计数，行本身喂列表（D17 的「同一份集合」）。"""
            q = db.query(_Gate).filter(*criteria).filter(predicate)
            rows = q.order_by(*order_by).limit(limit).all()
            total = (db.query(func.count(_Gate.id))
                     .filter(*criteria).filter(predicate).scalar() or 0)
            return rows, total

        owned_rows, owned_total = segment(mine, OWNED_LIMIT)
        unowned_rows, unowned_total = segment(unowned, UNOWNED_LIMIT)

        rows = owned_rows + unowned_rows
        drafts, students = _gate_label_sources(db, rows)

        def to_item(row) -> Dict:
            return _gate_item(row, drafts, students)

        return {"items": [to_item(r) for r in owned_rows],
                "owned_total": owned_total,
                "unowned_items": [to_item(r) for r in unowned_rows],
                "unowned_total": unowned_total}

    @staticmethod
    def in_doubt_gates(db, user_id: str) -> Dict:
        """4a 的「结果未知」清单（gate-lifecycle 2.7 第 2 步）：等老师裁定的门，只读。

        **判据另起一条，不碰 `pending_gate_criteria`。** 问的不是同一件事：那个判据
        回答「哪些 open 门算数」（待确认），这条回答「哪些门需要老师裁定」（待裁定）。
        混进一个断言，将来没人说得清它在保证什么。而且它的草稿判据对 in-doubt
        **根本不成立** —— 它要求草稿 `MAX(version)` 那行仍是 `awaiting_confirmation`，
        而 in-doubt 最典型的情形恰恰是**动作已经成功**（草稿已 sent）、进程只是死在
        `finish_gate` 之前：套上去会**恰好漏掉最该报的那一类**。

        所以这里的判据只有两条：
          ① `status == 'in_doubt'`（启动扫描落下的，见 `sweep_stuck_executing`）
          ② 每会话取最新一行（同 `pending_gate_criteria` 的①，防历史多行）

        归属收权与 `pending_gates` 同一套谓词（同一个 `owns_conversation` True 集）：
        登记在别人名下的会话两段都不出现。返回也是同一副四字段形状，
        前端两处可以共用一个渲染器。
        """
        from app.models.database import AgentGate as _Gate
        if not user_id:
            return {"items": [], "owned_total": 0,
                    "unowned_items": [], "unowned_total": 0}

        newer = aliased(_Gate)
        is_latest = _Gate.id == (
            select(func.max(newer.id))
            .where(newer.conversation_id == _Gate.conversation_id,
                   newer.status == "in_doubt")
            .correlate(_Gate)
            .scalar_subquery()
        )
        criteria = [_Gate.status == "in_doubt", is_latest]
        mine, unowned = _ownership_predicates(user_id)
        order_by = (_Gate.last_opened_at.asc(), _Gate.id.asc())

        def segment(predicate, limit):
            q = db.query(_Gate).filter(*criteria).filter(predicate)
            rows = q.order_by(*order_by).limit(limit).all()
            total = (db.query(func.count(_Gate.id))
                     .filter(*criteria).filter(predicate).scalar() or 0)
            return rows, total

        owned_rows, owned_total = segment(mine, OWNED_LIMIT)
        unowned_rows, unowned_total = segment(unowned, UNOWNED_LIMIT)
        rows = owned_rows + unowned_rows
        drafts, students = _gate_label_sources(db, rows)

        def to_item(row) -> Dict:
            return _gate_item(row, drafts, students)

        return {"items": [to_item(r) for r in owned_rows],
                "owned_total": owned_total,
                "unowned_items": [to_item(r) for r in unowned_rows],
                "unowned_total": unowned_total}


    @staticmethod
    def check_binding(db, conversation_id: str, gate_id: str) -> Optional[Dict]:
        """决定 ↔ 门的强绑定校验。None=放行；dict=拒绝（含当前门状态供重发）。"""
        open_gate = GateService.get_open_gate(db, conversation_id)
        if open_gate is None:
            return None
        if not gate_id:
            return None  # 旧客户端未带 gate_id + 单门开着 → 放行
        if gate_id == open_gate["gate_id"]:
            return None
        return {"gate_mismatch": True, "open_gate": open_gate}

    @staticmethod
    def close_gate(db, gate_id: str, status: str) -> bool:
        """关门并返回**有没有关上**（R8 D9=A）：查无此行返回 False。

        给"决定本身不落工具"的路径用（放弃 = 把这一步标成 cancelled）。要执行不可逆
        动作的批准走 `claim_gate` —— 那条必须先占到再执行，事后关门挡不住第二次批准。
        空 `gate_id` 由调用方先判掉（无门状态下继续决定是既有合法路径），不算失败。
        """
        return transition(db, gate_id, status,
                          allowed_from=("open", "executing"))

    @staticmethod
    def record_decision_log(db, gate_id: str, decision: str, *,
                            conversation_id: str = "", actor_user_id: str = "",
                            tool_name: str = "", kind: str = "",
                            detail: str = "") -> None:
        """门决定账本（只 insert 不 update）：谁、什么时候、对哪扇门做了什么。

        `agent_gates` 是**当前态**表：批准之后再开门会覆盖门面，`decided_at` 也等着被
        下一轮改写。所以「谁批的、批的哪一版」必须另有落点——没有这张表，验收里的
        「可回放」就没有数据来源（门行只剩最后一个离开的人留下的痕迹）。

        必须在工作单元里调用：账本和它描述的那次动作是同一次提交。账记上了而动作没跑
        （或反过来）比不记更糟——下一次重试会拿着一条假凭据。
        """
        from app.models.database import AgentGateDecision as _Decision
        require_unit(db)
        db.add(_Decision(
            gate_id=gate_id,
            conversation_id=conversation_id or None,
            decision=decision,
            actor_user_id=actor_user_id or None,
            tool_name=tool_name or "",
            kind=kind or "",
            detail=detail or "",
        ))
        db.flush()

    @staticmethod
    def closed_gates(db, conversation_id: str, limit: int = 10) -> list:
        """该会话最近的门决定 —— 终态卡（已批/已弃）的数据来源。

        读**账本**不读 `agent_gates.status`：门行是当前态表，重开门会把它覆盖掉，
        「上一版是怎么被处置的」在门行上根本留不住。门行只用来补展示字段
        （文案/动作/类别），归属与时间一律取账本。

        倒序（最新在前）；空 `conversation_id` 返回空表而不是全库扫描。
        """
        from app.models.database import AgentGate as _Gate
        from app.models.database import AgentGateDecision as _Decision
        if not conversation_id:
            return []
        rows = (db.query(_Decision)
                .filter(_Decision.conversation_id == conversation_id)
                .order_by(_Decision.id.desc())
                .limit(limit).all())
        if not rows:
            return []
        gates = {g.gate_id: g for g in db.query(_Gate)
                 .filter(_Gate.gate_id.in_({r.gate_id for r in rows})).all()}
        out = []
        for r in rows:
            g = gates.get(r.gate_id)
            out.append({
                "gate_id": r.gate_id,
                "decision": r.decision,
                "actor_user_id": r.actor_user_id or "",
                "kind": r.kind or (g.kind if g else ""),
                "tool_name": r.tool_name or (g.tool_name if g else ""),
                "action": (g.action if g else "") or "",
                "message": (g.message if g else "") or "",
                "decided_at": (r.created_at.isoformat() if r.created_at else None),
            })
        return out

    @staticmethod
    def record_run_scope(db, gate_id: str) -> Optional[Dict]:
        """批准一扇门后，记下"这批步骤也归这次批准"（P3 合并门的写入口）。

        调用点就在 `claim_gate` 内部：批准是唯一的许可（ADR-0021 §4），授权只能是
        它的副产品。另开一条"批量批准"入口等于给同一次决定做第二个说法，两份记录
        迟早漂开（ADR-0030 同一个道理）。

        三种情况什么都不记，且都必须什么都不记：
          ① 工具不在 `BATCH_MERGE_TOOLS` —— 不可逆破坏 / 按次审批不参与合并；
          ② 对象查不出（只给了姓名、或一次点名一片学生）—— 合并就成了拿张三的
             批准去放李四的动作；
          ③ 会话里没有活跃 plan run —— 没有计划就没有"这批步骤"。

        返回授权摘要（含 covered 步号），None = 没记。
        """
        require_unit(db)
        from app.models.database import AgentGate as _Gate
        row = (db.query(_Gate).filter(_Gate.gate_id == gate_id)
               .with_entities(_Gate.conversation_id, _Gate.tool_name, _Gate.kwargs)
               .first())
        if row is None:
            return None
        conversation_id, tool_name, kwargs = row
        if tool_name not in BATCH_MERGE_TOOLS:
            return None
        object_key = gate_object_key(kwargs or {})
        if not object_key:
            return None
        from app.services.plan_run_service import plan_run_service
        run = plan_run_service.get_active_run(db, conversation_id)
        if not run:
            return None
        covered = compute_covered_steps(run.get("steps") or [], object_key)
        gid = scope_gate_id(conversation_id, object_key)
        now = datetime.utcnow()
        scope_kwargs = {"run_id": run.get("run_id") or "", "object": object_key,
                        "from_gate": gate_id, "steps": covered}
        note = (f"本计划第 {'、'.join(str(n) for n in covered)} 步同属这次批准"
                if covered else "本次批准不是批量授权")
        existing = db.query(_Gate).filter(_Gate.gate_id == gid).first()
        if existing:
            existing.conversation_id = conversation_id
            existing.status = SCOPE_STATUS
            existing.kind = SCOPE_KIND
            existing.tool_name = tool_name
            existing.kwargs = scope_kwargs
            existing.message = note
            existing.decided_at = now
            existing.last_opened_at = now
        else:
            db.add(_Gate(gate_id=gid, conversation_id=conversation_id,
                         kind=SCOPE_KIND, tool_name=tool_name,
                         kwargs=scope_kwargs, action="", message=note,
                         status=SCOPE_STATUS, last_opened_at=now, decided_at=now))
        db.flush()
        return {"gate_id": gid, "object": object_key,
                "run_id": run.get("run_id") or "", "from_gate": gate_id,
                "steps": covered}

    @staticmethod
    def scope_covers(db, conversation_id: str, tool_name: str,
                     kwargs: Dict) -> Optional[Dict]:
        """这次调用是否已被**同一次批准**覆盖（只读，不写库）。

        返回授权摘要（含 `steps` / `from_gate`），None = 没覆盖 → 调用方照旧弹卡。

        三条同时成立才算覆盖：工具在 `BATCH_MERGE_TOOLS` 里、对象查得出、且授权记的
        正是**当前活跃 run**。第三条是「同一 plan_run 内」这句需求的落点 —— 上一个
        计划留下的授权不许放行新计划的动作，所以计划一收尾（`get_active_run` 返回
        None）或换了一轮，授权当场失效。
        """
        if not conversation_id or tool_name not in BATCH_MERGE_TOOLS:
            return None
        object_key = gate_object_key(kwargs or {})
        if not object_key:
            return None
        from app.models.database import AgentGate as _Gate
        row = (db.query(_Gate)
               .filter(_Gate.gate_id == scope_gate_id(conversation_id, object_key),
                       _Gate.status == SCOPE_STATUS)
               .first())
        if row is None:
            return None
        scope = row.kwargs or {}
        if not scope.get("run_id"):
            return None
        from app.services.plan_run_service import plan_run_service
        run = plan_run_service.get_active_run(db, conversation_id)
        if not run or run.get("run_id") != scope.get("run_id"):
            return None
        return {"gate_id": row.gate_id, "object": object_key,
                "run_id": scope.get("run_id"), "from_gate": scope.get("from_gate", ""),
                "steps": list(scope.get("steps") or [])}

    @staticmethod
    def claim_gate(db, gate_id: str) -> bool:
        """把一扇开着的门**占下来**（`open` → `executed`），返回占没占到。

        这是批准通路唯一的"许可"：动作（删题库、执行命令、发送给学生）只能由占到门
        的那次请求执行。写成条件更新 + 影响行数，不能"先读再改" —— 读与改之间另一个
        标签页可能已经把同一份草稿的门重开或顶掉，那时按读到的旧状态执行，同一扇门就
        被批了两次、破坏性动作跑了两遍（R8 D9 的靶子）。

        调用方必须在**执行动作之前**把这个单元的提交走完，第二次批准才看不见 `open`。
        占不到门的请求不得执行动作，改为重发当前门让人话对齐。
        """
        claimed = transition(db, gate_id, "executing", allowed_from=("open",))
        if claimed:
            # 批准顺带记下"这批步骤也归这次批准"（P3）。记不上**不影响批准本身**：
            # 授权管的是"老师少点几次卡"，许可永远只有上面那一行 —— 让一次授权
            # 记账失败把批准打回去，等于用优化项否决主流程。
            try:
                GateService.record_run_scope(db, gate_id)
            except Exception:  # noqa: BLE001
                logger.warning("gate scope record failed gate=%s", gate_id,
                               exc_info=True)
        return bool(claimed)

    @staticmethod
    def resolve_in_doubt(db, gate_id: str, retry: bool) -> bool:
        """老师对「结果未知」卡裁定（gate-lifecycle 2.7b）：`in_doubt` → 终局。

        - `retry=False`（**确认已执行**）→ `executed`。给"动作其实跑成功了、进程死在
          收口之前"那种情况：老师核对过真实世界（学生确实收到了计划），把账对齐。
        - `retry=True`（**重试**）→ 退回 `open`，可重新批准与重放。给"动作其实没跑成"
          那种情况。退回 `open` 而不是另造一条执行路径 ——
          `claim_gate → 重放 → finish_gate` 那条通路已经有了。

        只认 `in_doubt`，别的状态一律不碰：一扇**还在等老师决定**的门被"确认已执行"，
        等于把一个从没执行过的动作记成做过。

        不做任何自动化：in-doubt 的定义就是"没人知道跑没跑"，只有人能核对真实世界 ——
        这也正是当初决定把它做成一张卡、而不是让系统猜的原因。
        """
        done = (transition(db, gate_id, "open", allowed_from=("in_doubt",),
                           decided_at=_DT_CLEAR) if retry
                else transition(db, gate_id, "executed", allowed_from=("in_doubt",)))
        return bool(done)

    @staticmethod
    def sweep_stuck_executing(db) -> int:
        """**启动扫描**：把仍停在 `executing` 的门落成 `in_doubt`（gate-lifecycle 2.7b）。

        判据是**进程边界**，不是超时。`claim_gate` 与 `finish_gate` 之间永远发生在
        同一次请求、同一个进程里（`_direct_replay_events` 一个 async 函数走完），
        所以任何**熬过一次进程重启**的 `executing` 必然是上次崩溃留下的残留：
        执行它的那个进程已经不在了，结果不会再有回音。

        不用超时的理由：阈值得大于最慢的工具（`assign_adaptive_practice` 里头调 LLM，
        `_TURN_TIMEOUT` 是 180s），定短了正在正常执行的门会冒出一张「结果未知」卡，
        定长了真挂了要等很久才告诉老师。**进程边界是事实，超时是估计。**

        **绝不退回 `open`**：那等于允许盲目重试，而重试跑的正是那个不知道跑没跑过的
        不可逆动作。`in_doubt` 是独立状态，等老师对着「结果未知」卡裁定。

        ⚠️ 只在服务启动时调。进程还活着的时候调是错的 —— 会把正在执行的门误判成残留。
        """
        return transition_bulk(db, ("executing",), "in_doubt")

    @staticmethod
    def finish_gate(db, gate_id: str, ok: bool) -> bool:
        """执行结束，落定三态（gate-lifecycle 2.7）：成功 → `executed`，
        失败 → **退回 `open`**（老师可以重试），返回有没有落上。

        `claim_gate` 说的是「这活儿归我了」，只有这里才说「做完了没」—— 原来是同一个
        状态，于是「执行失败」等于「这件事永远做不成了」：门已被消费，老师拿着确认卡
        点什么都只会得到「已经处理过了」，而那个动作一次都没跑成。

        只从 `executing` 收口：`open`（没被占过）与各终态都不归它管。
        失败时把 `decided_at` 一并清掉 —— 门回到待确认，不该带着上一次的决定时刻。
        """
        done = (transition(db, gate_id, "executed", allowed_from=("executing",))
                if ok
                else transition(db, gate_id, "open", allowed_from=("executing",),
                                decided_at=_DT_CLEAR))
        return bool(done)

    @staticmethod
    def replay_target(db, conversation_id: str) -> Optional[Dict]:
        """重放依据（gate 实体携带 tool+kwargs+plan_step，重启后仍可重建）。"""
        open_gate = GateService.get_open_gate(db, conversation_id)
        if not open_gate:
            return None
        return {"tool_name": open_gate["tool_name"],
                "kwargs": open_gate["kwargs"],
                "plan_step": open_gate["plan_step"], "gate": open_gate}

    @staticmethod
    def verify_replay_payload(db, gate_id: str, tool_name: str,
                              kwargs: Dict) -> Optional[str]:
        """重放前校验：要执行的还是开门时钉住的那一份吗（gate-lifecycle 2.6）。

        返回 `None` = 放行；返回字符串 = 拒绝原因（调用方 fail-closed，不执行）。

        **不看 `status`**，理由同 `gate_plan_step`：调用方在 `claim_gate` 之后才调它，
        那时门已经是 `executing`，按 open 去查会永远查不到、把校验变成空转。

        两道都要过，各挡一种形态：
          ① 门行自己还是开门时那一份吗（`args_hash` vs 门行现值的重算）
             —— 挡「卡片的来源被改过」；
          ② 即将执行的那一份就是批准的那一份吗（`args_hash` vs 调用方要执行的值）
             —— 挡「卡片与执行脱节」。
        ①单放下不能少：门行是老师看到的卡片的来源，它被改过就说明看到的和执行的可能
        已经是两回事；②在今天尤其要：重放执行的是内存 pending，①管不到它。

        存量门（`args_hash` 为 NULL）放行 —— NULL 是「未记录」这个有效语义，
        把它当"不符"会当场作废库里所有开着的门（老师拿着卡点下去只会得到失败）。
        """
        from app.models.database import AgentGate as _Gate
        if not gate_id:
            return "确认门不存在"
        row = (db.query(_Gate)
               .filter(_Gate.gate_id == gate_id)
               .with_entities(_Gate.args_hash, _Gate.schema_version,
                              _Gate.tool_name, _Gate.kwargs).first())
        if row is None:
            return "确认门不存在"
        stored_hash, stored_schema, row_tool, row_kwargs = row
        if not stored_hash:
            return None                      # 存量门：未记录 → 回退旧行为
        if int(stored_schema or 0) != REPLAY_PAYLOAD_SCHEMA:
            return (f"确认门的载荷格式版本不认识（记录 {stored_schema}，"
                    f"当前 {REPLAY_PAYLOAD_SCHEMA}）")
        if stored_hash != replay_payload_hash(row_tool, row_kwargs or {}):
            return "这张确认卡的内容在开门之后被改过"
        if stored_hash != replay_payload_hash(tool_name, kwargs or {}):
            return "要执行的和您批准的不是同一份内容"
        return None

    @staticmethod
    def gate_plan_step(db, gate_id: str) -> int:
        """这扇门绑在哪一步（0 = 查无此行 / 未记录）。

        **刻意不看 `status`**：重放路径先 `claim_gate` 把门占成 `executed`，之后
        `get_open_gate` 就看不见它了 —— 而那正是最需要知道步归属的时刻。
        重放要落终态的是这一步，与门的 `tool_name` 可以不同名（生成即门）。
        """
        from app.models.database import AgentGate as _Gate
        if not gate_id:
            return 0
        row = (db.query(_Gate).filter(_Gate.gate_id == gate_id)
               .with_entities(_Gate.plan_step).first())
        return int(row[0] or 0) if row else 0

    @staticmethod
    def bind_step(db, gate_id: str, step_no: int) -> bool:
        """把一扇门绑到它所在的计划步（gate-lifecycle 批次 1）——门的**唯一**步归属。

        为什么是「门记步」而不是「步记门」：步那边已经有 `gate_draft_id` 这个反向
        指针，但它是**草稿门专属**的（布尔门没有 draft_id），且只落在 DB 的
        `plan_runs.steps` JSON 里，内存态拿不到。门这边记步号则两种门通用。

        为什么在这里提交（而 `open_gate` / `close_gate` 只 flush）：调用方是
        `plan_run_service.project_transitions`，那条链是 commit 制（ADR-0021 的
        工作单元只管确认门那条链，plan_run_service 自管提交，见 `mark_step`）。
        别把它"修"成 `require_unit` —— 会在这条链上抛 NoWorkUnit。

        只绑开着的门：已 executed/superseded 的行不该再被改写归属。
        """
        from app.models.database import AgentGate as _Gate
        if not gate_id or not step_no:
            return False
        bound = db.query(_Gate).filter(
            _Gate.gate_id == gate_id, _Gate.status == "open",
        ).update({"plan_step": int(step_no)}, synchronize_session=False)
        if bound:
            db.commit()
        return bool(bound)


    @staticmethod
    def close_conversation(db, conversation_id: str) -> None:
        """会话重置/删除 → 该会话所有门关闭（不留幽灵门，issue #18 阶段 3）。

        一并作废 `authorized` 的批量授权（P3）：会话被重置/删掉之后，上一轮那句
        "这批步骤已经批过了"没有会话可依附，留着就是一张重启后仍然生效的通行证 ——
        新会话里同一个学生的同类动作会被它静默放行。
        """
        require_unit(db)
        if not conversation_id:
            return
        transition_bulk(db, ("open", SCOPE_STATUS), "superseded",
                        *_conv_filter(conversation_id))


gate_service = GateService()
