"""ChemAI Agent Guard Infrastructure — the guardrail seam for the v2 agent.

GuardState provides per-invocation safety: deduplication, call limits,
prerequisite checks, and approval gating for destructive tools.

Originally extracted from langgraph_agent.py so v1 and v2 could share a
neutral module; v1 has since been deleted (see ADR-0019), leaving v2 as the
sole consumer.
"""

import asyncio
import json
import logging
import os

from langchain.agents.middleware import AgentMiddleware

logger = logging.getLogger(__name__)

# ── Tool call limits (lazy-loaded from TOOL_META to avoid circular imports) ──
TOOL_CALL_LIMITS: dict[str, int] = {}
_LIMITS_INITIALIZED = False

def _ensure_call_limits():
    global TOOL_CALL_LIMITS, _LIMITS_INITIALIZED
    if _LIMITS_INITIALIZED:
        return
    try:
        from agent.tools import TOOL_META
        TOOL_CALL_LIMITS = {t.__name__: m["call_limit"] for t, m in TOOL_META.items()}
    except ImportError:
        pass
    _LIMITS_INITIALIZED = True


# ── Approval gating ──
# 破坏性工具：assign_adaptive_practice / delete_bank 首次调用即被本护栏拦下、
# 登记确认门，老师在确认卡上批准后由 channel 确定性重放本次调用。
# write/edit/bash 不在其列——它们自带工作区判定（区内直接执行、区外按次审批，
# bash 另有白名单+危险命令过滤），一刀切前置审批会让"工作区内写文件"也要
# 弹卡（2026-08 全工具链路验收实测：写 test.txt 被 requires_approval 拦截，
# 与 DSH 工作区语义冲突）。
# share_artifact：对外分发必须过门（R6/ADR-0015 门体系）；门卡批准后才真正投递
TOOL_APPROVAL_REQUIRED = {"assign_adaptive_practice", "delete_bank", "share_artifact"}

# 模型/规划器常用同义词调工具（2026-10 真实计划实测：question_type / count）。
# 这些不是幻觉而是改名，直接丢会把「10 道选择题」降级成默认 5 道单选取而代之，
# 所以先按表改名，剩下的未知键才丢（见 _guarded 的签名过滤）。
# 重放式发送工具：入参（draft_id/student_id）由**门实体**携带，批准后确定性重放。
# 计划参数注入绝不能改写它们 —— 2026-10 三端联动实测：注入把「step_N 引用解析不到」
# 的整条参数丢掉，send_report_to_parent 拿不到 draft_id，家长报告一条都没发出去。
# 只读查询工具：不写库、不开门、不推进输入。确认门开着时它们**照常执行** ——
# 「全拦」的目的是不让模型在老师没点头时把**有副作用**的动作推下去；拦住一次读
# 什么也没保住，只会让模型重试（2026-10 真机实测：障碍诊断连出两张「已暂缓」卡，
# 老师看不懂在等什么）。新增只读工具时请一并归类到这里。
#
# 判据是「这个函数自己会不会写库」，不是「名字听起来像不像查询」。
_READ_ONLY_TOOLS = frozenset({
    "memory_student_get", "memory_teacher_get", "diagnose_barrier",
    "show_students", "show_diagnosis", "show_exam_workbench",
    "query_assignment_progress", "query_ocr_progress", "query_grading_queue",
    "query_batch_grades", "list_teacher_exams", "list_banks",
    "search_exam_bank", "parent_mastery_levels", "parent_barrier_distribution",
    "parent_class_comparison", "parent_weekly_summary", "parent_artifact_log",
})


_REPLAY_SEND_TOOLS = {"send_learning_plan", "send_report_to_parent", "parent_send_task"}

# HITL 中间件工具集（D+ 切片 3，原「原生 interrupt 试点」的接棒者）：
# 这些工具的确认门不再走"工具内 interrupt()"（试点形态，已删），也不走
# "返回 approval_needed 等事后重放"（legacy 形态），而是 create_agent 的
# HumanInTheLoopMiddleware(per_call) 在**模型→工具边界**挂起整图：
# 老师批准前工具一行不执行；批准后中间件原地执行同一次调用，无需重放。
# guard 侧对这些工具不再做门拦截（_make_guarded_tool 以 requires_approval=False
# 注册），去重/限额/留痕照旧；门行登记与决定收口在 channel（挂起检测/决定端点）。
# 先只放一个工具：它没有草稿配对、没有 replay-send 的许可耦合，能单独验完整条链。
_HITL_TOOLS = ({"assign_adaptive_practice"}
               if os.getenv("CHEMAI_GATE_INTERRUPT", "") == "1"
               else set())
# ↑ 迁移期开关：默认**关**，legacy 机制原样生效，仓库保持绿。开
#   `CHEMAI_GATE_INTERRUPT=1` 才让这批工具走 HITL 中间件挂起 —— 浏览器复跑与
#   回归测试可以各走各的。铺开到全部布尔门时把这个开关删掉。

_KWARG_ALIASES = {
    "question_type": "question_types",
    "count": "quantity",
    "num_questions": "quantity",
    "question_count": "quantity",
    "knowledge_point": "knowledge_points",
    "kp": "knowledge_points",
}

# 生成即门（ADR-0013/#13 冒烟补丁）：这些工具成功即自带门卡（返回 approval_needed
# + draft_id），guard 登记对应「发送」调用而非工具自身——生成不可重放。
_GATE_OPEN_TOOLS = {"generate_learning_plan", "generate_parent_report",
                    "parent_assign_task"}  # 家长派任务同模式（批 3 P2）
# 生成→发送的显式配对（批 3 审查修正：parent_assign_task 曾被 else 兜底静默
# 配到教师的 send_learning_plan，批准会重放教师发送）：
_GATE_OPEN_PAIRS = {
    "generate_learning_plan": "send_learning_plan",
    "generate_parent_report": "send_report_to_parent",
    "parent_assign_task": "parent_send_task",
}

# ── 连续重复调用护栏（2026-08，DSH repeat-tool-reminder 同款）──
_REPEAT_THRESHOLDS = (3, 5, 8)


def _replay_target_constructible(tool_name: str, kwargs: dict) -> bool:
    """这扇门将来**重放得起来**吗 —— 工具名能命中 + kwargs 能序列化（gate-lifecycle 2.5）。

    重放走的是 `_TOOL_BY_NAME[tool_name](**kwargs)`。两个条件缺一个，老师批准之后
    只会得到一句「重放失败」，而门已经开过、会话已经被全拦过一轮了。

    `open_gate` 存不下序列化不了的 kwargs（JSON 列 flush 当场炸），**但工具名它照存**
    —— 那正是这条校验要挡的。
    """
    from agent.langgraph_agent_v2 import _TOOL_BY_NAME
    if tool_name not in _TOOL_BY_NAME:
        return False
    try:
        json.dumps(kwargs)
    except (TypeError, ValueError):
        return False
    return True


def _repeat_reminder(name: str, count: int) -> str:
    """生成重复调用提醒文案：第 3 次温和，第 5/8 次严厉（只提醒不阻断）。"""
    if count == 3:
        return (
            f"提醒：你正在连续第 {count} 次用完全相同的参数调用 {name}。"
            "如果它没有按预期工作，请换一种思路——修正参数、换其他工具、"
            "或直接向老师说明情况，不要盲目重复。"
        )
    return (
        f"警告：已连续 {count} 次用完全相同参数调用 {name}，继续重复大概率不会成功。"
        "请立即停止重复：分析失败原因、改用其他工具或参数，"
        "或直接向老师说明情况并请求指导。"
    )

# ── 一次批准覆盖这批步骤（P3：连着几步撞门不再连点 N 次）──────────────
# 判据（哪些算"同类同对象"）只在 `gate_service`：`gate_object_key` +
# `BATCH_MERGE_TOOLS` + `scope_covers` 的 run_id 比对。这里只负责两件事：
# 问一句"这次调用批过了吗"，以及把"这次批准会连带放行什么"写进卡里。


def _scope_note(covers) -> str:
    """确认卡上那句话：这次批准**顺带覆盖**了计划里哪几步。

    合并省掉的是"点几次卡"，不是"老师看没看过"。所以被覆盖的那几步必须写在他点的
    这张卡上 —— 否则后面几张卡凭空消失，老师无从知道这次批准连带放行了什么
    （这也是它必须留在服务端文案生成处、而不是交给前端自己拼的原因）。
    """
    steps = [str(n) for n in (covers or [])]
    if not steps:
        return ""
    return (f"（本次批准覆盖本计划第 {'、'.join(steps)} 步的同类确认动作 —— "
            f"除本张卡之外不会再有单独的确认卡）")



# ── Recursion limits ──
RECURSION_LIMIT_V1 = 8
RECURSION_LIMIT_V2 = 12

# 确认词表与判定已收拢到 app/services/decision_intake.py（决定入口 · S1）：
# 分类只有一处之后，词表不能再在 guard 留第二份（漂开即"两处对打字语义各执一词"）。
# 这里只保留再导出，接住既有 `from agent.guard import is_confirmation` 的调用方。
from app.services.decision_intake import is_confirmation  # noqa: F401,E402


# ═══════════════════════════════════════════════════════════════════════
# GuardState
# ═══════════════════════════════════════════════════════════════════════

class GuardState:
    """Per-invocation safety state shared across all tool calls within one request.

    Tracks: dedup (same tool+args within 5s = skip), call limits (per-tool max),
    prerequisite checks (required args present), and approval gating (destructive
    tools are intercepted on their first call and registered as a pending gate,
    replayed by the channel once the teacher decides).
    """

    def __init__(self):
        self.seen_calls: set[str] = set()
        self.call_counts: dict[str, int] = {}
        self.approved: bool = False
        self.last_route: dict | None = None
        self.last_component: dict | None = None
        self.panel_opened: bool = False
        # 这里曾有 `pending_approval`（拦截时登记 fn/kwargs，批准后由 channel 重放）。
        # **gate-lifecycle 2.4 收尾删掉了**：载荷的真源是门那一行（ADR-0030），
        # 内存这份是第二个来源 —— 两者一旦漂开，老师批的和他拿到手的就不是一回事。
        # 要问"有没有门在等"用 `gate_is_open()`，"等的是哪个工具/要重放什么"问
        # `gate_service.get_open_gate` / `replay_target`。别再把它加回来。
        # 2026-08 计划状态持久化：plan_task 生成的步骤跨流保存（批准/恢复后
        # 由 channel 补发 plan_progress，避免计划卡永远停在"执行中"）
        self.plan_steps: list = []
        # 2026-08 连续重复调用护栏（DSH repeat-tool-reminder 同款）：
        # 同工具+同参数连续调用计数，阈值 [3,5,8] 时注入提醒（只提醒不阻断）
        self.last_call_key: str | None = None
        self.consecutive_repeats: int = 0
        # 2026-09-21 门类工具串行锁：并行 tool_calls（同一条 AI 消息多个调用，
        # ToolNode 并发执行）时，「pending 检查 → 登记门」之间存在整个工具执行的
        # await 窗口，两个门类工具可同时通过检查 → 双门齐开（实测）。门类工具的
        # 检查+登记必须持锁串行，拿锁后重验 pending。
        self._gate_lock: "asyncio.Lock | None" = None
        # 门开出（或本次调用被已开的门拦下）→ 本轮立刻收尾（2026-10，见 _GatePausedModel）。
        # 置位的下一次模型调用会被短路成「无 tool_calls 的收尾消息」，ReAct 循环没有
        # 下一步可走，图自然结束——不再让模型按计划一步步去撞 waiting_for_gate。
        self.paused: bool = False
        # D+ 合并门冻结快照（2026-10-26，HITL when 谓词专用）：见 freeze_hitl_scope。
        self.hitl_scope_freeze: dict = {"run_id": None, "by_object": {}}

    def gate_lock(self) -> "asyncio.Lock":
        if self._gate_lock is None:
            self._gate_lock = asyncio.Lock()
        return self._gate_lock

    def gate_is_open(self) -> "bool | None":
        """本会话此刻有没有一扇算数的开门。`None` = **读不出来**（调用方 fail-closed）。

        **只问实体。** 判据曾经是内存里的一个字段，而内存随进程一起消失 ——
        只认内存的代价（gate-lifecycle 2.4，2026-10-17 实测复现）：重启之后老师看着
        一张还在等他的确认卡（卡片来自库，照显示），而 C1-Q3 那条「有未决门时 agent
        的一切工具调用都被拒」整段失效，agent 能把卡片还等着的动作先做了。

        （曾有一条"内存先答"的快路；2.4 收尾删掉了那个字段，现在每次都问库。
        本地 SQLite 读，实测无感 —— 换来的是"答案只有一个来源"。）

        无 `conversation_id`（纯内存评测）直接返回 False —— 那条路压根没有库。
        """
        cid = getattr(self, "conversation_id", "")
        if not cid:
            return False
        try:
            from app.services.gate_service import gate_service
            from app.models import database as _db_mod
            db = next(_db_mod.get_db())
            try:
                return gate_service.get_open_gate(db, cid) is not None
            finally:
                db.close()
        except Exception:
            logger.warning("gate_is_open read failed cid=%s", cid, exc_info=True)
            return None

    def freeze_hitl_scope(self):
        """把「哪些对象已被哪次批准覆盖」冻结成内存快照（HITL `when` 谓词专用）。

        LangChain 对 `when` 谓词的硬要求是**确定性**：同一调用在挂起前与恢复
        重放时必须得到同一结果，否则恢复的 reject 会被静默忽略（文档明说的坑）。
        直接读库违反这条——批准动作本身就会写 scope 行（claim_gate 内部调
        record_run_scope），恢复那一刻的库已经和挂起那一刻不一样。所以每次
        **图运行开始前**冻结一份，运行期间谓词只读这份；决定流在批准之后、
        模型续接之前重冻（那是另一次图运行）。这样合并门卡上那句
        「本次批准覆盖第 N、M 步」对 HITL 工具也兑现（2026-10-26 8 步实测：
        批了计划门之后布置练习又弹一张卡，承诺与行为不一致）。

        读库失败冻结为空 = 一律弹卡（fail-closed，多一张卡好过误放行）。
        """
        cid = getattr(self, "conversation_id", "")
        frozen: dict = {"run_id": None, "by_object": {}}
        if cid:
            try:
                from app.models import database as _db_mod
                from app.services.gate_service import SCOPE_STATUS
                from app.services.plan_run_service import plan_run_service
                db = next(_db_mod.get_db())
                try:
                    run = plan_run_service.get_active_run(db, cid)
                    frozen["run_id"] = (run or {}).get("run_id")
                    if run:
                        from app.models.database import AgentGate as _Gate
                        rows = (db.query(_Gate)
                                .filter(_Gate.conversation_id == cid,
                                        _Gate.status == SCOPE_STATUS).all())
                        for r in rows:
                            obj = (r.kwargs or {}).get("object") or ""
                            if obj:
                                frozen["by_object"][obj] = dict(r.kwargs or {})
                finally:
                    db.close()
            except Exception:  # noqa: BLE001
                logger.warning("hitl scope freeze failed cid=%s", cid, exc_info=True)
        self.hitl_scope_freeze = frozen

    def hitl_scope_covers(self, tool_name: str, kwargs: dict) -> bool:
        """冻结快照版的 scope_covers（纯内存，确定性）。

        语义与 `gate_service.scope_covers` 对齐：工具可合并（BATCH_MERGE_TOOLS）
        + 对象可查（单学生/单班级）+ 授权行记的 run 与冻结时的活跃 run 同一个。
        没冻结/读不出 = 没覆盖（fail-closed）。
        """
        from app.services.gate_service import BATCH_MERGE_TOOLS, gate_object_key
        if tool_name not in BATCH_MERGE_TOOLS:
            return False
        obj = gate_object_key(kwargs or {})
        if not obj:
            return False
        row = (self.hitl_scope_freeze or {}).get("by_object", {}).get(obj)
        if not row or not row.get("run_id"):
            return False
        return row.get("run_id") == (self.hitl_scope_freeze or {}).get("run_id")

    def gate_scope(self, tool_name: str, kwargs: dict) -> dict:
        """这次调用批过了吗 —— 以及这次批准将来会覆盖哪几步（P3 合并门）。

        返回 `{"covered": 授权摘要|None, "covers": [步号...]}`。

        `covered` 为 None 一律照旧弹卡；**读不出来也返回 None**（fail-closed）：
        读不出"有没有授权"时多弹一张卡，代价是老师多点一次；猜错的代价是这个学生的
        发送被一行没人批过的记录放行。

        `covers` 只用来写卡上那句话（"本次批准同时覆盖第几步"），不参与放行判定 ——
        放行只认 `covered`，它来自已经存在的授权行。
        """
        empty = {"covered": None, "covers": []}
        cid = getattr(self, "conversation_id", "")
        if not cid:
            return empty
        try:
            from app.services.gate_service import (compute_covered_steps,
                                                   gate_object_key, gate_service)
            from app.services.plan_run_service import plan_run_service
            from app.models import database as _db_mod
            db = next(_db_mod.get_db())
            try:
                covered = gate_service.scope_covers(db, cid, tool_name, kwargs)
                object_key = gate_object_key(kwargs or {})
                run = plan_run_service.get_active_run(db, cid) if object_key else None
                covers = (compute_covered_steps(run.get("steps") or [], object_key)
                          if run else [])
            finally:
                db.close()
            return {"covered": covered, "covers": covers}
        except Exception:  # noqa: BLE001
            logger.warning("gate scope read failed cid=%s tool=%s", cid, tool_name,
                           exc_info=True)
            return empty

    async def acquire_gate(self, **register_kw) -> str:
        """W4a：持门类工具串行锁，**锁内完成「复查 + 登记实体」**。

        返回 `"ok"` / `"busy"`（已有门在等）/ `"failed"`（登记写不进去）。

        为什么登记必须进锁（gate-lifecycle 2.4 收尾）：这把锁的意义是
        「**检查与登记之间不许有窗口**」—— 并行 tool_calls（同一条 AI 消息多个调用，
        ToolNode 并发）时，两个门类工具各自 `await`，会在窗口里互相看不见。

        原先锁内写一个内存字段当"预订"：第二个进门就看见了。2.4 删掉那个字段之后，
        锁内什么都不留，两个并发门都能通过复查、各自登记 —— **双门齐开**，
        正是 W4a 当初要防的（repro-zhou-0920）。所以把登记搬进锁里：
        复查（问实体）与登记在同一个临界区，窗口不存在。

        ⚠️ 别再把它拆回"锁内只检查、锁外登记" —— 那样会静默退回这个竞态。
        """
        async with self.gate_lock():
            _open = self.gate_is_open()
            if _open is None or _open:
                return "busy"
            return "ok" if self.register_gate_entity(**register_kw) else "failed"

    def register_gate_entity(self, **kw) -> bool:
        """W4b：登记 gate 实体（ADR-0015 重启可重建）。无 conversation_id（纯内存
        评测）视为无需实体、放行；登记失败留痕并返回 False——调用方据此撤回本次
        拦截，不发一张重启后重建不出的幽灵门卡（B1）。

        **开门前先验重放目标可构造**（gate-lifecycle 2.5）。否则开出来的是一张
        **永远重放不了**的门：老师点了批准才发现没东西可执行，而它在这中间已经把
        整个会话全拦住了（guard 见"本会话有算数的开门"即拒 agent 的一切工具调用）。
        校验放在这里而不是 `open_gate`：`agent/` 才拿得到 `_TOOL_BY_NAME`，
        反过来让 `app/services/` import `agent/` 是把分层倒过来。
        """
        if not getattr(self, "conversation_id", ""):
            return True
        if not _replay_target_constructible(kw.get("tool_name", ""),
                                            kw.get("kwargs") or {}):
            logger.warning("gate target not constructible, refuse to open: tool=%s",
                           kw.get("tool_name"))
            return False
        try:
            from app.services.gate_service import gate_service
            from app.services.work_unit import new_work_unit
            # 这一扇门的登记就是本次写入的全部，所以单元开在这里（前置一）：
            # `open_gate` 只 flush，出口提交一次。
            with new_work_unit() as db:
                gate_service.open_gate(db, self.conversation_id, **kw)
            return True
        except Exception:
            logger.exception("gate entity registration failed: tool=%s",
                             kw.get("tool_name"))
            return False

    def begin_turn(self) -> None:
        """新一轮用户输入开始：重置连续重复计数（跨轮不累计，DSH 同语义）。"""
        self.last_call_key = None
        self.consecutive_repeats = 0
        self.paused = False  # 新的一轮（含批准后继续）：门的事上一轮已经交代完了

    def track_repeat(self, name: str, kwargs: dict) -> int:
        """登记一次工具调用，返回连续重复次数（1 = 首次或换了个调用）。"""
        key = f"{name}:{json.dumps(kwargs, sort_keys=True, ensure_ascii=False)}"
        if key == self.last_call_key:
            self.consecutive_repeats += 1
        else:
            self.last_call_key = key
            self.consecutive_repeats = 1
        return self.consecutive_repeats

    def check_dedup(self, name: str, kwargs: dict) -> bool:
        """Return True if this exact call was already made (dedup hit)."""
        key = f"{name}:{json.dumps(kwargs, sort_keys=True, ensure_ascii=False)}"
        if key in self.seen_calls:
            return True
        self.seen_calls.add(key)
        return False

    def forget_call(self, name: str, kwargs: dict) -> None:
        """撤销一次调用登记——调用未实际执行时（如返回 approval_needed）调用，
        批准后重试不会被「检测到重复调用」拦截。"""
        key = f"{name}:{json.dumps(kwargs, sort_keys=True, ensure_ascii=False)}"
        self.seen_calls.discard(key)

    def remember_call(self, name: str, kwargs: dict) -> None:
        """补一次调用登记——批准重放绕过 _guarded 直接执行裸 fn，登记 dedup 键，
        LLM 重调同参工具时命中「重复调用」拦截（issue #18 阶段 2：不再双执行）。"""
        key = f"{name}:{json.dumps(kwargs, sort_keys=True, ensure_ascii=False)}"
        self.seen_calls.add(key)

    def check_limit(self, name: str) -> str | None:
        """Return error message if call limit exceeded, else None."""
        _ensure_call_limits()
        limit = TOOL_CALL_LIMITS.get(name)
        if limit is None:
            return None
        count = self.call_counts.get(name, 0) + 1
        self.call_counts[name] = count
        if count > limit:
            return f"已调用 {name} {count} 次（上限 {limit} 次），请基于已有结果继续。"
        return None

    def undo_unexecuted_call(self, name: str, kwargs: dict) -> None:
        """调用**没有实际执行**时，把它留下的两处痕迹一起撤掉（dedup 键 + 调用计数）。

        必须一起撤，只撤一处等于没撤：`check_limit` 在 `check_dedup` **之前**跑，
        所以只 `forget_call` 的话，重试仍会撞上「已调用 N 次（上限 M 次），
        **请基于已有结果继续**」—— 而那句是**直接劝退模型**的指令。

        实测代价（2026-10-17 端到端）：`assign_adaptive_practice` 在门开着时被拦一次、
        计了 1 次；门关后模型重试得到「已调用 2 次（上限 1 次）」，于是照那句指令
        放弃这个工具、改调 `generate_questions` + `save_to_bank` 去凑「布置练习」，
        计划第 5 步永远做不成、家长通知跟着落空。

        `call_counts` 按**工具名**计（不是 name+kwargs），与 `check_limit` 的口径一致。
        """
        key = f"{name}:{json.dumps(kwargs, sort_keys=True, ensure_ascii=False)}"
        self.seen_calls.discard(key)
        if self.call_counts.get(name, 0) > 0:
            self.call_counts[name] -= 1

    def check_prerequisites(self, name: str, kwargs: dict) -> str | None:
        """Return error message if required params are missing, else None."""
        # 自动出题双路径（用户决策，2026-08）：generate_questions 不再强制先开面板——
        # 对话里给全 知识点+题型+数量 可直接生成；面板路径仍可先 show_exam_workbench。
        # show_exam_workbench 不设前置：面板的意义就是让用户在面板内补数据。
        prereqs = {
            "search_exam_bank": ["keyword"],
            "diagnose_barrier": ["student_id_or_class_id"],
            "weekly_report": ["student_id_or_class_id"],
            "assign_adaptive_practice": ["student_id_or_class_id"],
        }
        for field in prereqs.get(name, []):
            if field == "student_id_or_class_id":
                if not any((kwargs.get(k, "") or "").strip() for k in
                           ("student_id", "student_name", "class_id", "class_name")):
                    return "缺少必要信息：请先向用户确认学生姓名/学号或班级。"
                continue
            if field == "keyword":
                val = (kwargs.get("keyword", "") or "").strip()
                if not val or len(val) <= 2:
                    return "搜索关键词太宽泛。请先向用户确认：需要哪个具体知识点的真题？"
                continue
            if field == "knowledge_points":
                val = kwargs.get("knowledge_points", "") or ""
                if isinstance(val, list):
                    empty = not any(str(x).strip() for x in val)
                else:
                    empty = not str(val).strip()
                if empty:
                    return (
                        "缺少必要信息：知识点为空。请先向用户确认本次要考察哪些知识点"
                        "（例如：物质的量、氧化还原反应）。"
                    )
                continue
            if not (kwargs.get(field, "") or "").strip():
                return f"缺少必要信息: {field}。请先向用户确认。"
        return None


# ═══════════════════════════════════════════════════════════════════════
# Tool Wrappers
# ═══════════════════════════════════════════════════════════════════════

def _make_guarded_tool(fn, guard_state: GuardState, requires_approval: bool = False):
    """Wrap a tool function with GuardState safety checks.

    Applies: prerequisite check → call limit → dedup → approval gating.
    Strips _route and _component fields from result before returning.
    """
    from langchain_core.tools import tool as lc_tool, StructuredTool

    base_tool = lc_tool(fn)

    async def _guarded(**kwargs) -> str:
        name = fn.__name__
        # 2026-08 连续重复调用护栏：登记并生成提醒（只提醒不阻断，DSH 同款）。
        # 必须放在 dedup 之前——被去重拦截的重复尝试也算连续重复，第 3/5/8 次附提醒
        _reminder = None
        try:
            _rep = guard_state.track_repeat(name, kwargs)
            if _rep in _REPEAT_THRESHOLDS:
                _reminder = _repeat_reminder(name, _rep)
        except Exception:
            pass

        def _out(payload: dict) -> str:
            if _reminder:
                payload = dict(payload)
                payload["_reminder"] = _reminder
            return json.dumps(payload, ensure_ascii=False)

        _WAITING_BRIEF = ("当前有一张确认卡等待老师决定，此操作已暂缓。"
                          "老师处理完确认卡后，任务会自动继续。")

        def _waiting_out(llm_brief: str = _WAITING_BRIEF,
                         ui_label: str = "等待确认") -> str:
            """门已开着 → 本次暂缓（第三人称中性 brief，防 LLM 复述给用户）。

            撤掉本次调用留下的痕迹：它**没执行**，不该占调用上限、也不该进去重表
            —— 否则老师处理完确认卡之后，模型重试同一个调用会被自己的影子挡住。
            """
            guard_state.undo_unexecuted_call(name, kwargs)
            guard_state.paused = True  # 本轮到此为止：别再往下一步撞
            return _out({"error": "waiting_for_gate", "llm_brief": llm_brief,
                         "ui_label": ui_label, "gate_open_blocked": True})

        def _send_needs_draft_out(tool: str) -> str:
            """replay-send 工具被拒、且库里没有任何开着的门：不建门，给可继续的路。

            与 `_waiting_out` 的区别：那是「已有门，先等」；这里是「压根没有可批的
            草稿」，模型该回去生成，而不是原地重试（重试只会得到同一句话）。
            """
            guard_state.undo_unexecuted_call(tool, kwargs)
            guard_state.paused = True  # 没有草稿可发：本轮收尾，回去生成再来
            return _out({
                "error": "no_draft_to_send",
                "llm_brief": ("没有任何待确认的草稿，这次发送没有执行，也**不要重复调用**。"
                              "先按目标生成草稿（generate_learning_plan / generate_parent_report），"
                              "系统会在草稿生成后弹确认卡给老师决定。"),
                "ui_label": "没有可发送的草稿",
                "recoverable": True,
            })

        def _register_failed_out() -> str:
            """B1 fail-closed：门实体落库失败 → 撤回 pending，返回可恢复错误。

            同样撤掉痕迹：这次调用没有执行完（门都没登记成），重试不该被配额挡住。
            """
            guard_state.undo_unexecuted_call(name, kwargs)
            return _out({"error": "gate_register_failed",
                         "message": "确认门登记失败，请重试本次操作。",
                         "recoverable": True})

        # C2 跨步引用注入（实体依赖）：模型从计划里抄来的 ${step_N.field} 在这里
        # 换成库里已落库的步骤产物 —— 早期产物被 trim_context 压掉也不影响。
        # 分成两套 kwargs：数据范围与前置校验看注入后的（否则计划注入的 student_id
        # 会绕过 ADR-0014），去重键与门实体的 kwargs 快照仍用模型原始参数
        # （同一次调用在两处的判据必须一致）。
        _kw = kwargs
        _cid = getattr(guard_state, "conversation_id", "")
        if _cid and name not in _REPLAY_SEND_TOOLS:
            try:
                from app.models.database import get_db as _get_db
                from app.services.plan_run_service import plan_run_service as _prs
                _db = next(_get_db())
                try:
                    _kw = _prs.resolve_tool_args(_db, _cid, name, kwargs)
                finally:
                    _db.close()
            except Exception as _e:  # noqa: BLE001
                logger.warning("plan arg injection failed tool=%s: %s", name, _e)
                _kw = kwargs
        # 数据范围校验（ADR-0014）：接缝统一执行 TOOL_META 里声明的范围规则。
        #
        # 取代原先那段「只做默认填充」的身份回退 —— 它挡不住模型自报身份
        # （`teacher_id` 传了就用传的），也完全没覆盖 `student_id` / `class_id`。
        # 现在：ID 直接判范围、姓名在范围内搜、`teacher_id` 以服务端身份为准。
        # 无身份则 fail-closed（评测之外的路径不会有这情况）。
        from agent.tools.data_scope import check_tool_scope
        if (denied := check_tool_scope(fn, _kw)) is not None:
            return _out(denied)
        if (msg := guard_state.check_prerequisites(name, _kw)):
            return _out({"error": msg, "missing_prerequisites": True})
        if (dep := plan_dep_block(guard_state, name)) is not None:
            # 前置步还没做完就不执行（D1）。撤掉痕迹：这次没跑，做完前置步后重试
            # 不该被调用配额挡住。这也是 recoverable=True 的含义。
            guard_state.undo_unexecuted_call(name, kwargs)
            return _out({"error": "plan_dependency_unmet",
                         "llm_brief": dep["llm_brief"],
                         "blocked_by": dep["blocked_by"],
                         "recoverable": True})
        if (msg := guard_state.check_limit(name)):
            return _out({"error": msg, "limit_exceeded": True})
        if guard_state.check_dedup(name, kwargs):
            return _out({"error": "检测到重复调用，已跳过", "dedup_skipped": True})
        # C1-Q3 全拦（issue #18 阶段 1）：确认门开着（有未决门）时，agent 的一切
        # 工具调用都被拒——LLM 自然转为文字说明。根治「第二个门覆盖第一个 pending」
        # 与「门没批就推进后续步骤」。重放路径走 pending["fn"] 裸函数，不经过本包装。
        # 2026-09-21 文案契约分离（架构审查 C）：llm_brief 是给模型的中性事实
        # （第三人称，无祈使句——祈使句会被 LLM 鹦鹉学舌念给用户）；ui_label 是
        # 给前端工具卡的展示字段。error 保留为机器判定用，不再是用户可见文案。
        #
        # 判据走 `gate_is_open()`（gate-lifecycle 2.4）：**问实体**，内存只是快路。
        # 只认内存的话，重启一次这条闸就整段失效 —— 见该方法的注释。
        _open = guard_state.gate_is_open()
        if _open is None:
            # 读不出来 ≠ 没门。和 `_replay_payload_mismatch` 同一口径：
            # 宁可让老师重试，不可静默放行一个说不清有没有门的状态。
            return _waiting_out(
                llm_brief="确认门状态暂时读不到，本次操作已暂缓；请稍后重试。",
                ui_label="状态未知")
        # 只读工具不受这条闸限制：它既不写库也不开门，拦下来只换来一次无意义的
        # 重试，老师还会看到一张不知道在等什么的暂缓卡。
        if _open and name not in _READ_ONLY_TOOLS:
            return _waiting_out()
        # P3 合并门：这次调用若已被**同一次批准**覆盖（判据全在数据层，见
        # gate_service.scope_covers），就直接执行本次调用，不再开第二扇门。
        # `covered` 为 None（没批过 / 读不出来）照旧走下面那条拦下登记的路。
        _scope = (guard_state.gate_scope(name, kwargs) if requires_approval
                  else {"covered": None, "covers": []})
        # ADR-0015：粘性 approved 已退役——首次调用一律拦下登记门
        if requires_approval and not _scope["covered"]:
            # 与 approval_needed 同待遇：登记待重放 —— 老师在确认卡上批准后
            # 由 channel 确定性重放本次调用。
            # （2026-09-14 修：原先只有 approval_needed 那条路登记，而
            #  assign_adaptive_practice 这类工具是被**护栏直接拦下**的，
            #  结果批准后没有东西可重放 → 任务断在"等待确认"那一步。）
            # ADR-0015 阶段 2：布尔门实体化——拦下即登记 gate 实体（重启可重建）
            # 并返回 approval_needed 门卡载荷（与草稿门同构，adapter 据此发卡，
            # 不再依赖 request_approval / interrupt）。
            from app.services.gate_service import boolean_gate_id
            gate_id = boolean_gate_id(
                getattr(guard_state, "conversation_id", ""), name, kwargs)
            # 锁内复查 + 登记（并行 tool_calls 时不得覆盖已开的门，双门齐开）
            # 覆盖面那句话**同一份**进两处：门行（4a 待确认列表读它）与卡载荷
            # （聊天里那张卡读 message_gate）。两处各拼一次就会漂开。
            _note = _scope_note(_scope["covers"])
            _gate_msg = "此操作需要老师确认后执行" + _note
            # D+ 切片 3：HITL 工具的门拦截整段跳过（中间件在模型→工具边界挂起，
            # 这里再做拦截就是双门/死锁）。legacy 工具原样：锁内复查 + 登记实体，
            # 再返回门卡载荷等 channel 重放。
            # （本轮曾把这段误删——后果是所有 requires_approval 工具都不再登记门，
            #  4 条契约测试当场变红。删这段之前先看 test_phase2_gate_unification。）
            _acq = await guard_state.acquire_gate(
                kind="boolean", tool_name=name, kwargs=kwargs, action="",
                message=_gate_msg)
            if _acq == "busy":
                return _waiting_out()
            guard_state.forget_call(name, kwargs)
            # B1 fail-closed：门实体登记失败 → 不发重启后重建不出的幽灵卡
            if _acq == "failed":
                return _register_failed_out()
            return _out({
                "approval_needed": True,
                "message": "此操作需要老师确认，请在确认卡上批准/打回/放弃。",
                "message_gate": "此操作需要老师确认：请确认是否执行。" + _note,
                "gate_id": gate_id,
                "action": "",
                "awaiting_gate": True,
            })
        if requires_approval and _scope["covered"]:
            # 走到这里 = 这次调用被上一次批准覆盖了：直接执行，与批准后重放等价
            # （原生 interrupt 试点批准后也落到执行路径，但那条路 `covered` 为空，
            #   这里必须同时判 `covered`，否则 None.get 直接抛、工具根本跑不起来）
            # （重放走的也是这个裸 fn）。留一条记录，事后能回答"这次为什么没弹卡"。
            logger.info("gate_batch_covered cid=%s tool=%s from_gate=%s steps=%s",
                        getattr(guard_state, "conversation_id", ""), name,
                        _scope["covered"].get("from_gate") or "", _scope["covers"])
        # 计划注入 / 模型幻觉都可能带来工具签名里没有的键（2026-10 实测：计划第 3 步
        # 给 show_diagnosis 塞了不存在的 diagnosis_id → TypeError 直接打死整轮，
        # 计划卡永久停在「执行中」，第 4、5 步再不推进）。按签名过滤未知键并留痕：
        # 让工具用它自己的默认值，好过整条链断在这里。
        try:
            import inspect as _inspect
            _sig = _inspect.signature(fn).parameters
            if not any(p.kind == p.VAR_KEYWORD for p in _sig.values()):
                _renamed = {}
                for k in list(_kw):
                    alias = _KWARG_ALIASES.get(k)
                    if k not in _sig and alias and alias in _sig and alias not in _kw:
                        _renamed[k] = alias
                if _renamed:
                    logger.info("rename kwargs tool=%s map=%s", name, _renamed)
                    for src, dst in _renamed.items():
                        _kw[dst] = _kw.pop(src)
                _unknown = [k for k in _kw if k not in _sig]
                if _unknown:
                    logger.warning("drop unknown kwargs tool=%s keys=%s", name, _unknown)
                    _kw = {k: v for k, v in _kw.items() if k in _sig}
        except (TypeError, ValueError):
            pass
        result = await fn(**_kw)
        if name == "show_exam_workbench":
            guard_state.panel_opened = True
        if isinstance(result, str):
            try:
                data = json.loads(result)
                if isinstance(data, dict):
                    # 按次审批（工作区外访问，pi 式权限门）：批准是 guard 层的事，
                    # 不是 LLM 的事——工具返回 approval_needed 时一律登记待重放 +
                    # 门实体，老师在确认卡上决定后由 channel 确定性重放本次调用
                    # （ADR-0015 阶段 2：粘性 approved 的「已批准→自动放行重跑」
                    #  已退役，H4 会让一次批准解锁同轮后续一切按次审批；
                    #  set_elevate_once 只在重放那一次真单次消费）。
                    # 也不在工具内直接 interrupt()——langgraph 1.x 的 astream_events
                    # 会静默吞掉工具内 interrupt，既没有卡片、又会在带旧中断状态的
                    # 线程上永久挂死流（2026-08「read 卡死」实测）。
                    #
                    # 例外（ADR-0013 生成即门，2026-09 真实冒烟发现）：草稿生成工具
                    # 返回 approval_needed 时草稿已落库、无破坏性副作用，任何情况下
                    # 都不得重放生成（会 regenerate 自身）；登记的是对应的「发送」调用
                    # ——老师批准 → 确定性重放发送；打回 → channel 确定性修订。
                    # 这保证「生成后停等确认」不依赖模型自觉调发送工具。
                    if data.get("approval_needed") and name in _GATE_OPEN_TOOLS:
                        pending_name = _GATE_OPEN_PAIRS[name]
                        send_args = {"student_id": data.get("student_id", ""),
                                     "draft_id": data.get("draft_id", "")}
                        # 新增内容的发送**永不**走批量直发（2026-10 真机实测）：这里原来有
                        # 一条“发送若已被同一次批准覆盖就直接发”的捷径，结果是刚生成、
                        # 老师一眼没看过的家长报告被上一张卡的授权顺手发了出去，草稿当场
                        # 消费掉 → 后面那步「发送家长报告」查不到可发草稿 → 永远停在执行中。
                        # 批量授权覆盖的是“同一份内容再发一次”，不是“还没出生的一份内容”：
                        # 生成即门是老师看见内容的唯一入口，不能被子类化成“看见过这类动作”。
                        _send_scope = (guard_state.gate_scope(pending_name, send_args)
                                       if send_args["draft_id"]
                                       else {"covered": None, "covers": []})
                        if send_args["draft_id"]:
                            # 覆盖面那句话同一份进两处：卡载荷（message_gate，聊天里那张卡
                            # 读它）与门行（4a 待确认列表读它）。两处各拼一次就会漂开。
                            _draft_note = _scope_note(_send_scope["covers"])
                            _draft_msg = (str(data.get("message_gate")
                                              or data.get("message") or "请确认操作")
                                          + _draft_note)
                            data["message_gate"] = _draft_msg
                            # 拿锁后重验（2026-09-21）：并行 tool_calls 时本工具执行的
                            # await 窗口内，另一个门类工具可能已登记 pending——
                            # 不得覆盖（否则双门齐开，实测 repro-zhou-0920）
                            # 2026-09-21 门实体登记收权（架构审查 A）：草稿门实体
                            # 由 guard 统一落库（此前 adapter 也开一扇 → 双实体双
                            # gate_id，前端两张卡、批准后僵尸门）。adapter 只发卡。
                            # 2.4 起「复查 + 登记」在锁内一次完成（`acquire_gate`）。
                            _acq = await guard_state.acquire_gate(
                                kind="draft", tool_name=pending_name,
                                kwargs=send_args,
                                draft_id=str(send_args["draft_id"]),
                                version=int(data.get("version") or 0),
                                action=str(data.get("action") or ""),
                                message=_draft_msg)
                            if _acq == "busy":
                                return _waiting_out(
                                    llm_brief="当前有一张确认卡等待老师决定，本次操作已暂缓；"
                                              "草稿已生成不会丢失，老师处理完确认卡后可再次发起。",
                                    ui_label="等待确认（草稿已生成，当前门处理完可再发起）")
                            guard_state.forget_call(name, kwargs)
                            # B1 fail-closed：登记失败撤回拦截，不发幽灵门卡。
                            if _acq == "failed":
                                return _register_failed_out()
                            # 门已开出：本轮到此收尾（2026-10）。模型原本会继续按计划
                            # 往下调，一次次撞 waiting_for_gate，老师看到的是"没停、还在推进"。
                            guard_state.paused = True
                        # 无 draft_id（生成失败等）→ 原样返回，不弹门
                    elif data.get("approval_needed"):
                        # 重放式发送工具（send_*）**不再单独开门**：它的许可来自
                        # 「生成即门」那扇草稿门（_GATE_OPEN_PAIRS 配对重放）。
                        # 2026-10 三端联动实测：模型在生成之后又直接调 send_report_to_parent，
                        # 这里又登记一扇 g-tool- 布尔门 → 同一动作两条门路径 →
                        # 要么重放两遍（家长收 2 条），要么两次都拿不到 draft_id（0 条）。
                        # 判据必须**查库**：草稿门的待重放登记发生在另一个请求里，
                        # 本请求的 guard_state 是新建的（pending_approval 恒为空）。
                        # 2026-10 实测：内存判据永远为假 → 照样登记第二扇门 →
                        # 同一动作两条门路径 → 家长收 0 条或 2 条。
                        if name in _REPLAY_SEND_TOOLS:
                            # 三态：True=有门 False=没门 None=查不出来。
                            # 查不出来时按「有门」处理（fail-closed）——猜错的代价是
                            # 多等一轮，不是把没批过的发送放出去。
                            _open = None
                            _cid = getattr(guard_state, "conversation_id", "")
                            if _cid:
                                try:
                                    from app.models.database import get_db as _gdb
                                    from app.services.gate_service import gate_service as _gsvc
                                    _db = next(_gdb())
                                    try:
                                        _open = bool(_gsvc.get_open_gate(_db, _cid))
                                    finally:
                                        _db.close()
                                except Exception as _e:  # noqa: BLE001
                                    logger.warning("open gate probe failed cid=%s: %s", _cid, _e)
                            if _open is None:
                                return _waiting_out()
                            if _open:
                                logger.info("send tool deferred: gate already open cid=%s tool=%s", _cid, name)
                                guard_state.undo_unexecuted_call(name, kwargs)
                                return _waiting_out()
                            # 没有待处理的草稿门时，**不得**给 replay-send 工具另开一扇
                            # 布尔门：它的许可只来自草稿门（ADR-0013）。这扇布尔门的
                            # action 为空、卡上没有可点的发送对象，可它照样算「有一张
                            # 待确认卡」——门一直开着，后面每一次写操作都被挡（2026-10
                            # 实测：周翔那条链，老师按钮是灰的、模型越重试越死）。
                            # 正确形态是回一句普通结果，让模型回去生成/确认草稿。
                            return _send_needs_draft_out(name)
                        # 按次审批（工作区外访问等）。ADR-0015 阶段 2：粘性
                        # approved 的「已批准→自动放行重跑」退役（H4：一次批准
                        # 解锁同轮后续一切按次审批）——一律登记待重放 + 门实体，
                        # 批准后由 channel 确定性重放（set_elevate_once 真单次消费）。
                        from app.services.gate_service import boolean_gate_id
                        _gid = boolean_gate_id(
                            getattr(guard_state, "conversation_id", ""), name, kwargs)
                        # 拿锁后重验：本工具 await 执行窗口内另一扇门可能已登记，
                        # 不得覆盖（此前此分支无重验 → 布尔门可覆盖草稿门，双开）
                        _acq = await guard_state.acquire_gate(
                            kind="boolean", tool_name=name, kwargs=kwargs,
                            action="",
                            message=str(data.get("message") or "此操作需要老师确认"))
                        if _acq == "busy":
                            return _waiting_out(
                                "当前有一张确认卡等待老师决定，本次操作已暂缓。"
                                "老师处理完确认卡后，任务会自动继续。")
                        guard_state.forget_call(name, kwargs)
                        # B1 fail-closed：登记失败撤回拦截，不发幽灵门卡
                        if _acq == "failed":
                            return _register_failed_out()
                        data["gate_id"] = _gid
                        data.setdefault("message_gate", data.get("message") or "此操作需要老师确认")
                        data["awaiting_gate"] = True
                        guard_state.paused = True  # 布尔门也是门：本轮收尾等决定
                    if "_route" in data:
                        guard_state.last_route = data.pop("_route")
                    if "_component" in data:
                        guard_state.last_component = data.pop("_component")
                    if _reminder:
                        data["_reminder"] = _reminder
                    return json.dumps(data, ensure_ascii=False)
            except (json.JSONDecodeError, TypeError):
                pass
        elif isinstance(result, dict):
            if "_route" in result:
                guard_state.last_route = result.pop("_route")
            if "_component" in result:
                guard_state.last_component = result.pop("_component")
            if _reminder:
                result["_reminder"] = _reminder
            return result
        if _reminder and isinstance(result, str):
            return json.dumps({"_reminder": _reminder, "result": result[:2000]}, ensure_ascii=False)
        return result

    return StructuredTool(name=base_tool.name, description=base_tool.description,
                          args_schema=base_tool.args_schema, coroutine=_guarded)


# 这里曾是 `_make_request_approval_tool(guard_state)` —— 一个返回 request_approval
# 工具（内部调 LangGraph `interrupt()`）的工厂，2026-08 时为"布置练习反复需要批准"
# 引入。**ADR-0015 阶段 2 把它退役了**：确认门改由数据层强制（破坏性工具首次调用即
# 被 `_guarded` 拦下并登记门实体，批准后由 channel 确定性重放），模型侧不再有
# 独立的确认工具可调 —— `agent/langgraph_agent_v2.py` 的提示词里那句
# 「禁止再调用 request_approval（已退役）」就是这件事。
#
# 退役之后它**零调用**，只是没人清。gate-lifecycle 2.4 收尾删掉它，是因为它读的
# `guard_state.pending_approval` 这个字段在同一轮被删了 —— 留着就是一段指向不存在
# 属性的坏代码（真被调到会 AttributeError）。同类的历史字串判定
# （SSE 适配器里的 `if name == "request_approval"`）留着无妨，它们只是防御性分支。


# ═══════════════════════════════════════════════════════════════════════
# Checkpointer (shared SqliteSaver singleton)
# ═══════════════════════════════════════════════════════════════════════

import aiosqlite as _aiosqlite  # noqa: E402
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver  # noqa: E402

_checkpointer = None


async def _get_checkpointer():
    """Return the shared AsyncSqliteSaver singleton for agent conversation persistence."""
    global _checkpointer
    if _checkpointer is None:
        # 钉死绝对路径（2026-08-16）：相对路径随进程 cwd 漂移，曾导致重启后
        # 历史对话"消失"（写库与 conversation.py 读库指向不同文件）。路径现在只有一个定义处，
        # 见 `settings.CHECKPOINT_DB_PATH`。
        from app.core.config import settings as _settings
        conn = await _aiosqlite.connect(_settings.CHECKPOINT_DB_PATH)
        _checkpointer = AsyncSqliteSaver(conn)
        await _checkpointer.setup()
    return _checkpointer


# ═══════════════════════════════════════════════════════════════════════
# LLM Model Factory
# ═══════════════════════════════════════════════════════════════════════

_PLAN_DEP_TERMINAL = ("completed", "cancelled", "failed", "skipped")


def plan_dep_block(guard_state, tool_name: str):
    """计划依赖序校验（D1）：前置步还没终态 → 返回拦截载荷，否则 None。

    只拦「前置步**还没做完**」，不拦「前置步被放弃/失败」：后者已是终态，再拦就把
    老师的一次放弃变成后面所有步骤的永久死锁（那一步永远不会再变成 completed）。

    2026-10-20 演示现场：第 5 步 generate_questions 被标 cancelled，依赖它的第 6 步
    assign_adaptive_practice 却完成了 —— 卡片上就是「出题被放弃、练习却布置了」。
    此前执行侧从不拦，只有在状态转移那一层不做归属。

    纯函数，不查库：读 `guard_state.plan_steps` —— channel 已把 run 行/适配器那份同步
    进来，与计划卡显示的是同一份状态。
    """
    steps = getattr(guard_state, "plan_steps", None) or []
    if not steps:
        return None
    target = next((s for s in steps
                   if s.get("skill") == tool_name
                   and s.get("status") in ("pending", "running",
                                           "awaiting_confirmation")), None)
    if target is None:
        return None
    by_no = {s.get("step"): s for s in steps}
    # 依赖指向计划里不存在的步（计划被裁过）→ 不算未满足：无从满足的等待只会卡死
    blocked = [d for d in (target.get("depends_on") or [])
               if d in by_no and by_no[d].get("status") not in _PLAN_DEP_TERMINAL]
    if not blocked:
        return None
    nums = "、".join(f"第 {d} 步（{by_no[d].get('skill')}）" for d in blocked)
    return {
        "blocked_by": blocked,
        "llm_brief": (f"计划里 {nums} 还没做完，本次调用没有执行。"
                      f"按计划先完成它，再调 {tool_name}；不要重复调用。"),
    }


class GateTurnEndMiddleware(AgentMiddleware):
    """门开出后把**下一次模型调用**短路成收尾回执，让本轮自然结束（D+ 切片 2）。

    `_GatePausedModel` 的中间件形态（原类已删）：语义一比一——门一开
    （`GuardState.paused`），下一次模型调用不打 LLM，直接回一条**不含
    tool_calls 也不含正文**的回执，ReAct 循环没有下一步可走，图收尾，
    控制权回到老师手上。`begin_turn()` 复位后照常工作。

    为什么是中间件而不是模型包装：`create_agent` 的正规扩展点是
    AgentMiddleware；包模型要处理 bind_tools 透传、Runnable 协议，
    而且把「门暂停」这个图级关注点塞进了模型层。短路不打模型 →
    流上不会有任何模型 token，聊天里不再出现无意义的暂停文案。
    继承 AgentMiddleware 但**必须**显式写 awrap_model_call：langchain 1.4.7
    的默认实现不委托给同步版（直接 NotImplementedError，issue #33474 的坑），
    而 channel 的主流话全是异步流。
    """

    def __init__(self, guard_state=None):
        super().__init__()
        self.guard_state = guard_state

    @staticmethod
    def _pause_receipt():
        from langchain_core.messages import AIMessage
        return AIMessage(content="",
                         additional_kwargs={"_gate_paused": True})

    def wrap_model_call(self, request, handler):
        if getattr(self.guard_state, "paused", False):
            return self._pause_receipt()
        return handler(request)

    async def awrap_model_call(self, request, handler):
        if getattr(self.guard_state, "paused", False):
            return self._pause_receipt()
        return await handler(request)


def get_langchain_model(provider: str):
    """Return a ChatOpenAI model for the given provider (deepseek/mimo/dashscope)."""
    from langchain_openai import ChatOpenAI
    from app.config import config as _cfg
    from app.services.llm_transport import (
        LIVE, OFFLINE_KEY_PLACEHOLDER, REPLAY, build_model_client, resolve_mode,
    )

    api_keys = _cfg.provider_keys
    key = api_keys.get(provider, "")
    if not key and resolve_mode() == REPLAY:
        # 重放不需要凭据：请求根本不出本机，而 langchain 在**构造**客户端时就要查 key（它比
        # 这条缝还早）。不给占位值，"离线重放零凭据"这句话在 agent 这条路上不成立 —— 评估的
        # `trajectory_*` 条目在 CI 里就是这么红的：报错写着 Missing credentials，看着像
        # "没配模型"，实际是"磁带压根没被问到"。
        #
        # 只给 replay。record 是真要打出去的，缺 key 就该在这里炸；换成占位值只会收到一发
        # 401，而 401 会被当成一次正常应答录进磁带 —— 一批"看起来很绿"的坏带就是这么来的。
        key = OFFLINE_KEY_PLACEHOLDER
    configs = {
        "deepseek": {"model": _cfg.DEEPSEEK_MODEL, "base_url": _cfg.DEEPSEEK_BASE_URL},
        "mimo": {"model": _cfg.MIMO_MODEL, "base_url": _cfg.MIMO_BASE_URL},
        "dashscope": {"model": _cfg.DASHSCOPE_MODEL, "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1"},
    }
    cfg = configs.get(provider)
    if cfg is None:
        # 以前未知 provider 会静默退回 deepseek。删掉一个 provider 之后这个默认值会咬人：
        # 谁的 `.env` 里还留着 LLM_PROVIDER=zhipu，他会以为自己配的是智谱，实际在打 deepseek，
        # 而且没人报错。
        raise ValueError(f"未知 provider {provider!r}，可用：{sorted(configs)}")
    # stream_usage=True: 流式响应末尾携带 usage(DeepSeek 等 OpenAI 兼容端点支持),
    # 供 context_meter 计算上下文占用;不支持 include_usage 的端点自动忽略
    # max_retries 三种模式一律 2：录制留着重试才会把 429→200 记成一盘带的两条，重放若关成 0
    # 就会拿第一条 429 直接抛 RateLimitError —— 录进去的带放不出来（ADR-0023 决策 10）。
    #
    # 输出封顶走 extra_body 的 `max_tokens`，不用 ChatOpenAI 的 max_tokens 属性：
    # langchain 1.4.2 把那个属性映射成 `max_completion_tokens`，而 deepseek-flash 不认它
    # （缝上录到的真实请求体就是 `max_completion_tokens: 4096`，实测单轮吐出 8941 输出
    # token —— 原来这道封顶从来没生效过）。extra_body 里的 `max_tokens` 实测封顶生效。
    #
    # 16384 与 180s 是配套的：对话讲评这条留思考（这是选 flash 的理由），而思考 token 算在
    # 封顶之内，实测吞吐约 220 token/s → 16384 token 最坏约 74s，60s 读超时会把它掐断在一
    # 段没说完的话上。流式下首字仍是 0.3s 量级，180s 只是"整轮真卡住"的兜底。
    # http_async_client 只在 record|replay 下注入，理由见 ADR-0023 决策 9。
    return ChatOpenAI(model=cfg["model"], api_key=key,
                      base_url=cfg["base_url"], temperature=0.3,
                      stream_usage=True, max_retries=2, request_timeout=180.0,
                      extra_body={"max_tokens": 16384},
                      http_async_client=None if resolve_mode() == LIVE else build_model_client())
