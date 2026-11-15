"""ChemAI LangGraph Channel — /api/agent/chat/langgraph 端点.

ReAct agent + interrupt/resume + SSE 兼容。
"""
import asyncio
import json
import logging
import os
import sys
import uuid as _uuid
from contextlib import asynccontextmanager, contextmanager
from types import SimpleNamespace
from fastapi import APIRouter, Depends, Request, UploadFile, File, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker
from app.middleware.conversation import require_owned
from app.models.database import get_db, UploadSession
from agent.channel.conversation import (
    _INTERNAL_NOTE_PREFIX, humanize_step_exhaustion, step_exhausted_reply,
)

logger = logging.getLogger(__name__)
router = APIRouter()


def _log_error(tag: str):
    """把流式异常完整落盘（2026-08：异常之前只进服务窗口，无法追溯）。
    写 chemai_agent_errors.log（backend 根目录）。"""
    import traceback as _tb
    try:
        log_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
            "chemai_agent_errors.log")
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(f"\n===== [{tag}] =====\n")
            _tb.print_exc(file=f)
    except Exception:
        pass

# 单轮执行超时（2026-08 卡死治理：工具/模型挂起时兜底终止，避免流永不结束）
_TURN_TIMEOUT = 180

# 超时兜底的 SSE 错误文案（chat / resume 两条流共用）
_TIMEOUT_ERROR_MSG = "处理超时，本次执行已中止，请重试或换个方式提问。"

# C 类自动续跑（2026-10）：计划由模型自己按序执行，它会在中途收尾（实测「准备一次月考」
# 5 步只跑了 3 步）。收尾前若还有未执行的步骤，注入一条继续指令再跑一轮——**有界**：
# 超过 _PLAN_CONTINUE_ROUNDS 轮仍不齐，就交回 _plan_tail_events 标 cancelled 并告知用户，
# 绝不无限续跑（工具配额与 _TURN_TIMEOUT 都会先撞顶）。
_PLAN_CONTINUE_ROUNDS = 2
_PLAN_CONTINUE_MSG = (
    _INTERNAL_NOTE_PREFIX
    + "的计划还没执行完，还差：{steps}。"
    "请立刻继续调用这些步骤对应的工具把它们做完，不要只回复说明文字；"
    "全部执行完再给出最终答复。)"
)

# 批准重放后的 LLM 续接提示。重放结果已由 _replace_pending_tool_result 替换进
# 上文 ToolMessage（C 层），这里只写简短指引——不再把完整结果重复塞进 user 消息
# （2026-08 修复「用户发送端显示巨文」根因：12KB 注入消息被历史接口渲染成用户消息）。
# 注入消息统一以 _INTERNAL_NOTE_PREFIX 开头（conversation.py 历史过滤依赖该约定）。
_REPLAY_CONTINUE_MSG = (
    _INTERNAL_NOTE_PREFIX
    + "的操作已获得批准，并已自动重放完成，"
    "完整结果见上文的工具结果（工具「{name}」）。"
    "请基于该结果继续完成老师的请求，给出明确的最终答复。"
    "如果结果中包含错误，请向老师说明失败原因并给出下一步建议，不要重复调用刚才的工具。）"
)
# 兜底：ToolMessage 替换失败时保留旧行为（结果直接塞进消息），绝不丢结果
_REPLAY_CONTINUE_MSG_FALLBACK = (
    _INTERNAL_NOTE_PREFIX
    + "的操作已获得批准，并已自动重放完成。"
    "重放结果如下：\n{result}\n\n请基于这个结果继续完成老师的请求，给出明确的最终答复。"
    "如果结果中包含错误，请向老师说明失败原因并给出下一步建议，不要重复调用刚才的工具。）"
)


async def _get_app_store():
    """Get the SqliteStore singleton from app.main."""
    try:
        from app.main import get_store
        return await get_store()
    except Exception:
        return None

# ── 复用 ChatRequest / Gateway 分类器 ──
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))
from agent.channel.gateway import (  # noqa: E402
    ChatRequest, _classify_and_narrow
)
from agent.memory import MemoryStack  # noqa: E402
from app.services import decision_intake  # noqa: E402  # 决定入口（架构候选 A）
from agent.langgraph_sse_v2 import _json_safe_payload  # noqa: E402


_agent_cache: dict[str, object] = {}
_guard_state_cache: dict[str, object] = {}  # 按 conversation_id 缓存 guard_state, resume 时复用
# 线程迁移映射（ADR-0016 §4）：conversation_id → 迁移后的 checkpoint thread_id。
# 进程内存缓存即可——重启后首回合重新检测毒化并再迁移一次，代价是纯状态复制。
_THREAD_OVERRIDES: dict[str, str] = {}

# 每会话执行锁（ADR-0016 §5）：同一会话第二条流（双击批准/双标签页）→ 409，不排队
_CONV_LOCKS: dict[str, asyncio.Lock] = {}


async def acquire_conversation_lock(cid: str) -> asyncio.Lock:
    """取会话锁并真正获取；已被占用立即 409（不排队——排队会让第二次点击
    延迟生效，老师以为没点上再点一次，感知成幽灵双发）。"""
    lock = _CONV_LOCKS.setdefault(cid or "default", asyncio.Lock())
    if lock.locked():
        raise HTTPException(status_code=409, detail="已有进行中的回复，请稍候再操作。")
    await lock.acquire()
    return lock


@asynccontextmanager
async def conversation_scope(cid: str):
    """会话锁生命周期收口（W5，ADR-0015 阶段 3）。

    acquire 之后的 eager await（建 agent、线程清场、trim_context）一旦抛异常，
    旧写法在交给 `_locked_stream` 收尾之前崩掉 → 锁永不释放 → 该会话永久 409。
    这里用 async with 把「acquire→交给 _locked_stream」这段 eager span 包住：
    - 正常退出：**不**释放——流式响应尚未开始，锁已交由 `_locked_stream` 的
      生成器 finally 在流结束后释放（此处提前放会让第二条流挤进来）；
    - eager span 抛异常：在此释放并原样上抛，杜绝泄漏。
    两条路径（/stream、/resume）共用同一把生命周期。"""
    lock = await acquire_conversation_lock(cid)
    try:
        yield lock
    except BaseException:
        lock.release()
        raise


def _locked_stream(lock, gen_factory, headers=None):
    """StreamingResponse 包装：流结束（含断线取消）时释放会话锁。gen_factory
    是异步生成器函数，返回响应时才调用（与 StreamingResponse 惰性语义一致）。"""
    async def _stream():
        try:
            # ⚠ 必须 async for——gen_factory() 返回异步生成器，普通 for 会
            # TypeError: 'async_generator' object is not iterable（2026-09-20
            # 修复：该 bug 使 /stream 与 /resume 的所有流式回复一进来即 500）
            async for chunk in gen_factory():
                yield chunk
        finally:
            lock.release()
    return StreamingResponse(_stream(), media_type="text/event-stream",
                             headers=headers or {"Cache-Control": "no-cache"})


def build_config(cid: str) -> dict:
    """会话 → LangGraph config（经线程迁移映射，ADR-0016 §4）。"""
    return {"configurable": {"thread_id": _THREAD_OVERRIDES.get(cid or "default", cid or "default")},
            "recursion_limit": 24}


class _DecisionAborted(Exception):
    """决定在单元里走不下去（service 返回人话 error）。

    抛出来只为了两件事：让 `work_unit` 回滚这一次决定的全部写入，以及把人话文案
    原样送到老师面前。它不代表系统故障，所以与下面的宽except分开处理。
    """


def _close_gate_solo(gate_id: str, status: str) -> bool:
    """在自己的单元里关一扇门，返回有没有关上。

    这一路只有这一次写入，所以"没关上"不会造成半截状态；关上与否由调用方决定
    怎么交代（打回/批准的重开与批准记录另有其单元，见 D9 的整单回滚）。
    """
    from app.services.gate_service import gate_service
    from app.services.work_unit import new_work_unit
    with new_work_unit() as db:
        return gate_service.close_gate(db, gate_id, status)


@contextmanager
def _entity_db():
    """门 / 计划 / 草稿实体的一次性数据库会话，退出即关。

    端点内的会话由 FastAPI 的 `Depends(get_db)` 管；端点之外（本文件的这些 helper）
    只能自己 `next(get_db())` 拿。写 `db = next(get_db())` 而不关，要等 CPython
    引用计数回收生成器时才走到 get_db 的 finally——那是运行时实现细节，不是契约，
    而这些读写全在每条 SSE 流的高频路径上。统一走这里，调用方不必再写 try/finally。

    两个刻意的取舍：
    · 每次调用时从模块上取 `get_db`（延迟绑定）——换测试库靠的就是替换
      `app.models.database.get_db` 这个属性，用文件顶部 import 进来的名字会绕过它。
    · 退出时关**会话**而不是关生成器：`gen.close()` 会向 get_db 抛 GeneratorExit，
      撞上「块内提前 return/raise」时解释器的收尾路径就冒出
      `generator ignored GeneratorExit`，顶掉真正的业务异常。`Session.close()` 幂等，
      生成器随后被回收时 get_db 的 finally 再关一次也无害。"""
    from app.models import database as _db_mod
    db = next(_db_mod.get_db())
    try:
        yield db
    finally:
        db.close()


def _mark_run_paused_safe(cid: str) -> None:
    """断线/超时 → run 标 paused（ADR-0016 §3）。无活跃 run 时 service 侧是无异常
    空转，所以这里能抛出的都是真故障——留痕而不是静默，否则 paused 契约无声失效。"""
    if not cid:
        return
    try:
        from app.services.plan_run_service import plan_run_service
        with _entity_db() as db:
            plan_run_service.mark_run_paused(db, cid)
    except Exception as e:  # noqa: BLE001
        logger.warning("mark run paused failed cid=%s: %s", cid, e)


async def _migrate_thread(agent, old_config: dict, cid: str) -> dict:
    """毒化/孤儿中断线程迁移（ADR-0016 §4）：新线程复制 messages 历史，
    绝不调用模型（回归红线⑤）。旧线程原地作废。"""
    from langchain_core.messages import AIMessage
    state = await agent.aget_state(old_config)
    msgs = list((state.values or {}).get("messages", []) or [])
    # 孤儿 tool_call 一并丢弃——迁移后没有重放给它补 ToolMessage，留着必 LLM 400
    clean = [
        m for m in msgs
        if not (isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
                and not all(
                    any(getattr(n, "tool_call_id", None) == tc.get("id") for n in msgs)
                    for tc in m.tool_calls))
    ]
    new_tid = f"{cid or 'default'}-r{_uuid.uuid4().hex[:8]}"
    new_config = {"configurable": {"thread_id": new_tid}, "recursion_limit": 24}
    if clean:
        await agent.aupdate_state(new_config, {"messages": clean})
        # 迁移后计数校验（ADR-0016 §4，story 22）：复制条数必须一致
        _verify = await agent.aget_state(new_config)
        _copied = len(list((_verify.values or {}).get("messages", []) or [])) if _verify else -1
        if _copied != len(clean):
            raise RuntimeError(f"迁移校验失败：原 {len(clean)} 条，新线程 {_copied} 条")
    _THREAD_OVERRIDES[cid or "default"] = new_tid
    logger.warning("thread migrated for conversation %s: %s -> %s",
                   cid or "default", old_config.get("configurable", {}).get("thread_id"),
                   new_tid)
    return new_config


async def _repair_thread_state(agent, config, cid: str) -> tuple[bool, str, dict]:
    """线程状态清场（ADR-0016：恢复永不自动、零模型调用）。返回 (可继续, 阻断原因, config)。

    - 任务带 error（毒化，生产日志实证的 OpenAI 400 等）→ 迁移到新线程；
    - 未决中断：interrupt() 已随 ADR-0015 退役，残留中断即孤儿 → 迁移
      （2026-08 的 `ainvoke(Command(resume="cancelled"))` 偷跑完整回合、事件全丢、
      副作用照跑，且再中断会挂死流——整体删除）；
    - 无未决任务时的孤儿 tool_call：RemoveMessage 摘除（安全，无重放会引用它）。
    """
    from langchain_core.messages import AIMessage, RemoveMessage
    state = await agent.aget_state(config)
    if not state:
        return True, "", config

    # 任务已带错误 → 毒化线程 → 自动迁移（老师无感，ADR-0016 §4）
    for task in getattr(state, "tasks", ()) or ():
        if getattr(task, "error", None):
            try:
                return True, "", await _migrate_thread(agent, config, cid)
            except Exception as _e:  # noqa: BLE001
                logger.warning("thread migration failed: %s", _e)
                return False, "该对话的状态已损坏且自动修复失败，请点击「新对话」开始新的对话。", config

    msgs = list(state.values.get("messages", []) or [])
    orphans = [
        m.id for m in msgs
        if isinstance(m, AIMessage) and getattr(m, "tool_calls", None)
        and not all(
            any(getattr(n, "tool_call_id", None) == tc.get("id") for n in msgs)
            for tc in m.tool_calls
        )
    ]

    pending = bool(getattr(state, "next", None))

    if pending:
        # 孤儿中断（interrupt 退役前的残留 checkpoint）：纯状态迁移，零模型调用
        try:
            return True, "", await _migrate_thread(agent, config, cid)
        except Exception as _e:  # noqa: BLE001
            logger.warning("orphan-interrupt migration failed: %s", _e)
            return False, "该对话包含未完成的审批操作，状态已损坏，请点击「新对话」开始新的对话。", config
    if orphans:
        # 无未决任务：摘孤儿是安全的（不会再有重放给它配 ToolMessage）
        try:
            await agent.aupdate_state(
                config, {"messages": [RemoveMessage(id=o) for o in orphans]},
            )
            logger.warning("removed %d orphan tool_call message(s) for thread %s",
                           len(orphans), cid or "default")
        except Exception as _e:  # noqa: BLE001
            logger.warning("orphan cleanup failed: %s", _e)

    return True, "", config


async def _get_or_create_agent(persona: str = "tutor", provider: str = "deepseek",
                        student_profile=None, intent_hints="",
                        store=None, guard_state=None, teacher_id: str = ""):
    """Get or create agent. Returns (graph, guard_state, adapter_class).

    v2 单 Agent 是唯一架构（v1 多 Agent 已于 2026-09 退役，见 ADR-0019）。
    2026-08 修复「布置练习反复需要批准」：必须透传 guard_state ——
    resume 时若不传，create_v2 会新建 GuardState（approved=False），
    agent 工具绑定新状态 → 用户批准后 LLM 再次 request_approval 又中断。"""
    from agent.langgraph_agent_v2 import create_chemai_agent as create_v2
    from agent.langgraph_sse_v2 import LangGraphSSEAdapterV2
    agent, guard = await create_v2(
        persona=persona, provider=provider,
        student_profile=student_profile, intent_hints=intent_hints,
        store=store, guard_state=guard_state, teacher_id=teacher_id,
    )
    return agent, guard, LangGraphSSEAdapterV2




# 角色 → 可用 persona（服务端映射）：persona 决定 system prompt 与工具白名单
# （langgraph_agent_v2 按 persona 过滤 TOOL_META），是权限而不只是展示。
# 请求体自报的 persona 只能落在本角色允许集里，越权一律降级为角色默认——
# 否则学生一个 body 字段就能拿到教师工具（show_students 全库查学生等）。
_PERSONAS_BY_ROLE = {
    "student": ("student",),
    "parent": ("parent",),
    "teacher": ("teacher", "tutor"),
    "subject_lead": ("teacher", "tutor"),
    "academic_admin": ("teacher", "tutor"),
    "admin": ("teacher", "tutor"),
    "super_admin": ("teacher", "tutor"),
}


def _resolve_persona(request, current_user: dict) -> str:
    """对话 persona：按 token 角色收窄（三个对话端点共用，少一处就等于少一道门）。"""
    role = current_user.get("role")
    allowed = _PERSONAS_BY_ROLE.get(role)
    if not allowed:
        raise HTTPException(status_code=403, detail="当前角色不能使用对话功能")
    requested = getattr(request, "persona", None) or allowed[0]
    return requested if requested in allowed else allowed[0]


def _resolve_student_identity(request, current_user: dict, db) -> str:
    """对话里的学生身份：学生认 token（user_id 即学号），其余角色的自报 id 须在范围内。

    三个对话端点（stream / 非流式 / resume）共用——少一处就等于少一道门。
    """
    if current_user.get("role") == "student":
        return current_user.get("user_id") or ""
    student_id = request.student_id or ""
    if student_id:
        from app.middleware.scope import RequestScope
        if not RequestScope.from_user(current_user, db).allows_student(student_id):
            raise HTTPException(status_code=403, detail="无权访问该学生")
    return student_id


@router.post("/chat/langgraph/stream")
async def agent_chat_langgraph_stream(
    request: ChatRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("agent", "create", allow_self=True)),
):
    """流式对话 (SSE) — LangGraph ReAct agent + interrupt/resume.

    会话必须属于调用者（阶段 4.6 批次 E）—— `conversation_id` 由客户端自报且可枚举。
    """
    from dotenv import load_dotenv
    load_dotenv()

    from agent.deps import ChemAIDeps
    from langgraph.errors import GraphInterrupt

    # 文件访问档位 + 老师自定义工作区随请求下发（前端设置）
    from agent.tools.filesystem import set_file_access_level, set_workspace_root
    set_file_access_level(request.file_access)
    set_workspace_root(request.workspace)

    # 身份注入（ADR-0014）：直接用 PermissionChecker 已校验过的 current_user。
    # 原先这里手动再解一遍 Bearer token、而且**只取 user_id**（role / school_id 丢掉），
    # 接缝要靠它们算 Data Scope。current_user 三字段齐全、来源同一份签名 token，
    # 不必再解析一次（少一条会漂的代码路径）。
    from agent.tools.context import set_current_identity
    _teacher_id = current_user.get("user_id", "") or ""
    set_current_identity(
        _teacher_id,
        current_user.get("role", ""),
        current_user.get("school_id"),
    )

    cid = request.conversation_id

    # 会话归属（阶段 4.6 批次 E）：缺 id 或不属于调用者一律 403
    require_owned(db, cid, current_user)

    # 学生身份（P5 对话作答回流）：学生认 token 身份（user_id 即学号），
    # 客户端自报的 student_id 不采信；家长/教师自报的须落在自己的数据范围内
    student_id = _resolve_student_identity(request, current_user, db)
    persona = _resolve_persona(request, current_user)

    # Session handling — only track student_id, no message_history
    from agent.channel.gateway import _conversations
    if cid and cid in _conversations:
        if not student_id:
            student_id = _conversations[cid].get("student_id")
    elif cid:
        _conversations[cid] = {"student_id": student_id}

    # 学生身份注入（P5 对话作答回流）：服务端身份优先，工具据此落学情
    from agent.tools.context import set_current_student
    set_current_student(student_id or "")

    deps = ChemAIDeps(
        student_id=student_id,
        persona=persona,
        provider_name=request.provider,
    )

    if student_id:
        mem = MemoryStack()
        mem.load_student(student_id)
        deps.student_profile = mem.student_profile

    # Gateway classifier: 意图分类 + 工具收窄
    tool_names, intent = await _classify_and_narrow(
        request.message, persona, None
    )

    # navigate shortcut
    if intent and intent.type == "navigate" and intent.page:
        async def _nav_gen():
            yield f"data: {json.dumps({'type': 'navigate', 'page': intent.page, 'params': {}}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
        return StreamingResponse(_nav_gen(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})

    # 每会话执行锁（ADR-0016 §5）：双击批准/双标签页的第二条流立即 409。
    # async with 收口 eager span 的异常释放（W5）——见 conversation_scope。
    async with conversation_scope(cid) as conv_lock:
        # Phase 3: extract pre-routed agent from Gateway classifier
        # D+ 换骨架（2026-10-24）：`pre_routed_agent` / `suggested_tools` 从 v1 多代理
        # 路由时代遗留下来的图输入键，全仓无消费方；create_agent 的输入 schema
        # 只收 messages，这两个键随骨架切换一并退役（分类结果 intent 本身照常留痕）。

        # Build hints for sub-agent (used regardless of routing)
        hints = ""
        if tool_names:
            hints = (
                f"推荐工具: {', '.join(tool_names[:5])}\n"
                f"用户消息: {request.message}\n使用推荐工具或其他工具，自行判断。"
            )

        # 门开着时打字的语义（ADR-0015 决策通道唯一化）：确认词 → 引导（零副作用）；
        # 其他文字 → 草稿门=打回+反馈 / 布尔门=作废。必须在缓存覆盖前读取上一轮 guard。
        _previous_guard = _guard_state_cache.get(cid) if cid else None
        # 问实体：门开着没开是**门那一行**的事，不是内存里那个变量的事
        # （gate-lifecycle 2.4 收尾）。`_previous_guard` 仍然要取 —— 下面打回/修订
        # 那条路要复用它带着的会话态。
        gate_pending_stream = _pending_from_entity(cid)
        if gate_pending_stream:
            stream_is_plan_gate = decision_intake.is_plan_gate(
                cid, gate_pending_stream)  # DB kind 权威，无门才回退工具名
            _kind, _typed_text = decision_intake.classify_typed(
                cid, request.message or "", gate_pending_stream, stream_is_plan_gate)
            if _kind == "guidance":
                async def _guide_gen():
                    _g1 = json.dumps({"type": "text", "content": _typed_text},
                                     ensure_ascii=False)
                    _g2 = json.dumps({"type": "done"}, ensure_ascii=False)
                    yield f"data: {_g1}\n\n"
                    yield f"data: {_g2}\n\n"
                    yield "data: [DONE]\n\n"
                return _locked_stream(conv_lock, _guide_gen)
            if _kind == "reject":
                # ADR-0013：草稿门开着时打字 = 打回+反馈 → 确定性修订循环
                # （旧逻辑在这里作废审批、把反馈当全新消息流转，闭环断裂）
                fb_agent, fb_guard, FBAdapter = await _get_or_create_agent(
                    persona, request.provider,
                    store=await _get_app_store(), guard_state=_previous_guard,
                    teacher_id=_teacher_id,
                )
                fb_adapter = FBAdapter(conversation_id=cid or "")
                _restore_plan_state(fb_adapter, _previous_guard)
                fb_cfg = build_config(cid)
                fb_ok, fb_blocked, fb_cfg = await _repair_thread_state(fb_agent, fb_cfg, cid)
                if not fb_ok:
                    async def fb_blocked_gen():
                        _err0 = json.dumps(
                            {'type': 'error', 'message': fb_blocked, 'recoverable': False},
                            ensure_ascii=False,
                        )
                        yield f"data: {_err0}\n\n"
                        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                        yield "data: [DONE]\n\n"
                    return _locked_stream(conv_lock, fb_blocked_gen)
                from agent.context_manager import trim_context as trim_fb
                await trim_fb(fb_agent, fb_cfg)
                fb_decision = {"action": "reject", "feedback": request.message or "", "draft_id": ""}
                return _locked_stream(
                    conv_lock,
                    lambda: _gate_decision_stream(cid, fb_decision, _previous_guard,
                                                  fb_agent, fb_adapter, fb_cfg))
            # 落下来的第三种（"void"：布尔门 + 修改指令）= 旧审批作废、按新消息正常
            # 流转。**作废必须落到门上**：2.4 让 C1-Q3 问实体之后，光清内存早就不
            # 管事了 —— 不清门，老师打了「先别管这个」，卡片还在、工具照样被 C1-Q3
            # 全拦，那句话等于没说。
            _void_open_gate(cid)

        store = await _get_app_store()
        agent, guard_state, AdapterClass = await _get_or_create_agent(
            persona, request.provider,
            student_profile=deps.student_profile, intent_hints=hints,
            store=store,
            teacher_id=_teacher_id,
        )
        # 新的一轮用户输入：重置连续重复调用计数（2026-08 重复提醒护栏）
        if guard_state is not None:
            try:
                guard_state.begin_turn()
            except Exception:
                pass
            # 图运行前冻结合并门快照（HITL when 谓词确定性要求，见 guard 注释）
            guard_state.freeze_hitl_scope()

        # Cache guard_state for resume — 避免 resume 时创建全新 GuardState 丢失 approved 状态
        if cid and guard_state:
            _guard_state_cache[cid] = guard_state
            guard_state.conversation_id = cid  # 布尔门实体登记需要（ADR-0015 阶段 2）

        adapter = AdapterClass(conversation_id=cid or "")
        # 续接执行器（_run_continuation）从这里取 guard 重冻合并门快照——
        # 批准后的续接轮要把新写的 scope 行冻进去（HITL when 谓词数据源）。
        adapter._guard_state_ref = guard_state
        # 每回合都从持久化 PlanRun 恢复计划态（缺陷 #3）：正常新消息若遇活跃
        # run（如上一轮布尔门暂停），计划卡要继续推进而非凭空消失；本轮若重新
        # plan_task，适配器会用新步骤覆盖，二者不冲突。
        _restore_plan_state(adapter, guard_state)
        full_reply = []
        config = build_config(cid)

        # 线程状态清场（毒化/孤儿中断 → 迁移，ADR-0016 §4；零模型调用）
        _ok, _blocked_reason, config = await _repair_thread_state(agent, config, cid)
        if not _ok:
            async def _blocked_gen():
                _err = json.dumps(
                    {'type': 'error', 'message': _blocked_reason, 'recoverable': False},
                    ensure_ascii=False,
                )
                yield f"data: {_err}\n\n"
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            return _locked_stream(conv_lock, _blocked_gen)

        # Smart context management: TrimMessages + LLM summarization
        from agent.context_manager import trim_context
        await trim_context(agent, config)

    async def generate():
        nonlocal full_reply
        try:
            yield f"data: {json.dumps({'type': 'phase', 'phase': 'thinking'}, ensure_ascii=False)}\n\n"

            # 上下文占用:收集最后一次模型调用的 provider usage(2026-08 context 环)
            from agent.context_meter import build_context_event, extract_prompt_tokens
            from app.config import config as _cfg
            _last_prompt_tokens = None

            try:
                async with asyncio.timeout(_TURN_TIMEOUT):
                    async for event in agent.astream_events(
                        {"messages": [{"role": "user", "content": request.message}]},
                        config,
                        version="v2",
                    ):
                        if event.get("event") == "on_chat_model_end":
                            _pt = extract_prompt_tokens(event.get("data", {}).get("output"))
                            if _pt is not None:
                                _last_prompt_tokens = _pt
                        for result in adapter.feed(event):
                            try:
                                evt = json.loads(result)
                                if evt.get("type") == "text":
                                    full_reply.append(evt.get("content", ""))
                            except (json.JSONDecodeError, TypeError):
                                pass
                            yield f"data: {result}\n\n"

            except TimeoutError:
                _mark_run_paused_safe(cid)  # ADR-0016：超时即暂停，不自动重跑
                _err = json.dumps(
                    {'type': 'error', 'message': _TIMEOUT_ERROR_MSG, 'recoverable': True},
                    ensure_ascii=False,
                )
                yield f"data: {_err}\n\n"
                yield "data: [DONE]\n\n"
                return
            except asyncio.CancelledError:
                _mark_run_paused_safe(cid)  # 客户端断线：run 立刻标 paused
                raise
            except GraphInterrupt:
                # ADR-0015：interrupt 已退役——迁移间隙残留兜底：如实告知 + 标 paused
                _mark_run_paused_safe(cid)
                payload = {"type": "run_paused", "reason": "interrupted",
                           "message": "上一回合被中断，运行已暂停；重新发送消息即可继续。"}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return
            except Exception as _exc:  # noqa: BLE001
                # 兜底：异常不得穿透（穿透 = SSE 半路断掉、计划卡悬在「执行中」、
                # 收尾对账被绕过）。这里补做三件事：步骤落终态、明确报错、正常收帧。
                logger.exception("stream failed cid=%s: %s", cid, _exc)
                try:
                    if guard_state is not None and getattr(adapter, "_plan_steps", None):
                        guard_state.plan_steps = adapter._plan_steps
                except Exception:  # noqa: BLE001
                    pass
                for _ev in _plan_abort_events(guard_state, cid):
                    yield f"data: {_ev}\n\n"
                _err = json.dumps({
                    "type": "error",
                    "message": f"本轮执行出错已中止：{str(_exc)[:200]}",
                    "recoverable": True,
                }, ensure_ascii=False)
                yield f"data: {_err}\n\n"
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return

            # ── 原生 interrupt 试点：图在工具里挂起 = 本轮到此为止 ──
            # 判定读**图状态**而不是工具返回值：挂起时工具根本没有返回，
            # 旧路径那条「工具返回 approval_needed → adapter 发卡」不会触发。
            # 旧机制不受影响：它不产生挂起点，interrupts 恒为空。
            try:
                _susp_ints = getattr(await agent.aget_state(config), "interrupts", ()) or ()
            except Exception as _e:  # noqa: BLE001
                logger.warning("suspend probe failed cid=%s: %s", cid, _e)
                _susp_ints = ()
            if _susp_ints:
                for _ev in _suspended_gate_events(cid, _susp_ints, guard_state):
                    yield f"data: {_ev}\n\n"
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return

            # 计划状态持久化到 guard_state（批准/恢复后补发 plan_progress，2026-08）
            try:
                if guard_state is not None and getattr(adapter, "_plan_steps", None):
                    guard_state.plan_steps = adapter._plan_steps
            except Exception as e:  # noqa: BLE001
                logger.warning("plan steps write-back failed cid=%s: %s", cid, e)

            # C 类自动续跑：模型中途收尾时，把未执行的计划步骤推回去再跑（有界）
            for _rnd in range(_PLAN_CONTINUE_ROUNDS):
                # 续跑中撞 HITL 门挂起（_run_continuation 已发卡+立旗）：
                # 必须停——再注入新输入会把老师的决定点顶掉。
                if getattr(adapter, "_suspended_mid_turn", False):
                    break
                _left = _plan_pending_desc(guard_state)
                if not _left:
                    break
                logger.info("[PlanContinue] cid=%s round=%s left=%s", cid, _rnd + 1, _left)
                async for _ev in _run_continuation(
                        agent, adapter, config,
                        _PLAN_CONTINUE_MSG.format(steps=_left), "plan-continue"):
                    yield f"data: {_ev}\n\n"
                try:
                    if getattr(adapter, "_plan_steps", None):
                        guard_state.plan_steps = adapter._plan_steps
                except Exception as _e:  # noqa: BLE001
                    logger.warning("plan continue write-back failed cid=%s: %s", cid, _e)

            # 收尾字段取自 checkpoint 图状态（唯一路径，ADR-0019）
            try:
                final_state = await agent.aget_state(config)
                state_values = final_state.values if final_state else {}
            except Exception:
                state_values = {}
            # 步骤耗尽兜底语不经模型流式（图节点伪造的 AIMessage），直播只能
            # 在此查状态补发中文人话，否则本轮空气泡收场（2026-10-05 实录）
            _step_zh = step_exhausted_reply(state_values)
            if _step_zh:
                yield f"data: {json.dumps({'type': 'text', 'content': _step_zh}, ensure_ascii=False)}\n\n"
            gs = guard_state
            result_text = state_values.get("last_result_text")
            # Fallback: use collected streaming text if state has no result_text (v2)
            if not result_text and full_reply:
                result_text = "".join(full_reply)
            result_text = humanize_step_exhaustion(result_text)
            # 上下文占用事件(provider usage 锚定 + 三段启发式构成)
            _ctx_event = build_context_event(
                _last_prompt_tokens, state_values.get("messages"), _cfg.CONTEXT_WINDOW,
            )
            for _ev in _plan_tail_events(gs, cid):
                yield f"data: {_ev}\n\n"
            for result in adapter.finalize(
                route=state_values.get("last_route") or (gs.last_route if gs else None),
                component=state_values.get("last_component") or (gs.last_component if gs else None),
                result_text=result_text,
            ):
                if result == "[DONE]":
                    yield f"data: {json.dumps(_ctx_event, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                else:
                    yield f"data: {result}\n\n"

        except Exception as e:
            _log_error("stream")
            import traceback
            traceback.print_exc()
            _mark_run_paused_safe(cid)  # ADR-0016：模型/系统报错 → run 如实标暂停
            err_msg = str(e)
            if 'recursion' in err_msg.lower():
                err_msg = '处理超时，Agent 重试次数用尽。请重试或换个方式提问。'
            err_payload = json.dumps(
                {'type': 'error', 'message': err_msg, 'recoverable': True},
                ensure_ascii=False,
            )
            yield f"data: {err_payload}\n\n"
            yield "data: [DONE]\n\n"

    return _locked_stream(
        conv_lock, generate,
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive", "X-Accel-Buffering": "no"})


@router.post("/chat/langgraph")
async def agent_chat_langgraph(
    request: ChatRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("agent", "create", allow_self=True)),
):
    """非流式对话 — LangGraph ReAct agent。会话必须属于调用者。"""
    from dotenv import load_dotenv
    load_dotenv()

    from agent.deps import ChemAIDeps
    from langgraph.errors import GraphInterrupt

    from agent.tools.filesystem import set_file_access_level, set_workspace_root
    set_file_access_level(request.file_access)
    set_workspace_root(request.workspace)

    cid = request.conversation_id
    # 会话归属（阶段 4.6 批次 E）：缺 id 或不属于调用者一律 403
    require_owned(db, cid, current_user)

    # 身份注入（ADR-0014）：非流式端点**此前完全没有注入** —— 走这条路径时
    # 工具拿不到身份，接缝会 fail-closed 拒绝。这里补齐，三个端点行为一致。
    # （合并说明：main 侧这里原本还调 `set_current_teacher`，现在由本函数的
    #   `set_current_identity` 一并带上 user_id + role + school_id，不再重复注入。）
    from agent.tools.context import set_current_identity
    set_current_identity(
        current_user.get("user_id", ""),
        current_user.get("role", ""),
        current_user.get("school_id"),
    )

    # 学生身份：同 /stream —— token 优先，自报 id 须在数据范围内（否则可注入他人画像）
    student_id = _resolve_student_identity(request, current_user, db)
    persona = _resolve_persona(request, current_user)
    from agent.tools.context import set_current_student
    set_current_student(student_id or "")

    deps = ChemAIDeps(persona=persona, provider_name=request.provider)
    if student_id:
        mem = MemoryStack()
        mem.load_student(student_id)
        deps.student_profile = mem.student_profile

    tool_names, intent = await _classify_and_narrow(
        request.message, persona, None
    )

    if intent and intent.type == "navigate" and intent.page:
        return {"content": "", "navigate": {"page": intent.page, "params": {}}}

    hints = ""
    if tool_names:
        hints = (
            f"推荐工具: {', '.join(tool_names[:5])}\n"
            f"用户消息: {request.message}\n使用推荐工具或其他工具，自行判断。"
        )

    # Phase 3: pre-routed agent from Gateway —— D+ 换骨架退役（同 /stream 处注释）

    store2 = await _get_app_store()
    agent, guard_state2, AdapterClass = await _get_or_create_agent(
        persona, request.provider,
        student_profile=deps.student_profile, intent_hints=hints,
        store=store2,
    )
    if guard_state2 is not None:
        try:
            guard_state2.begin_turn()  # 新的一轮用户输入：重置重复计数
        except Exception:
            pass

    config = build_config(cid)

    from agent.context_manager import trim_context
    await trim_context(agent, config)

    try:
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": request.message}]},
            config,
        )
    except GraphInterrupt:
        return {"content": "", "interrupt": True, "message": "Agent waiting for approval"}

    # Extract final text
    msgs = result.get("messages", [])
    text = ""
    for m in reversed(msgs):
        content = getattr(m, "content", None)
        if content and isinstance(content, str) and content.strip():
            text = humanize_step_exhaustion(content)
            break

    # 路由与组件：优先图状态（checkpoint 里的 last_route），取不到回退本轮 guard_state
    final_state = await agent.aget_state(config)
    state_values = final_state.values if final_state else {}
    response = {"content": text}
    last_route = state_values.get("last_route") if state_values else None
    last_component = state_values.get("last_component") if state_values else None
    if not last_route and guard_state2:
        last_route = guard_state2.last_route
    if not last_component and guard_state2:
        last_component = guard_state2.last_component
    if last_route and last_route.get("navigate"):
        route = last_route
        response["navigate"] = {"page": route.get("page"), "params": route.get("params", {})}
        if route.get("populate"):
            response.setdefault("populate", []).append(route["populate"])
        for act in route.get("actions", []):
            response.setdefault("actions", []).append(act)
    if last_component:
        response["component"] = last_component

    return response


async def _direct_replay_events(guard_state, approved: bool, cid: str = "",
                                gate_id: str = "", actor_id: str = "") -> tuple:
    """无中断时的确定性重放（2026-08）：模型可能不调 request_approval——
    工具结果带 approval_needed 时 adapter 已直接发审批卡，批准后这里
    直接重放 guard 登记的待批调用，返回（合成 tool_result 事件, 重放信息 dict 或 None），
    供调用方恢复 LLM 继续回答——批准后不再只有一张卡而没有下文。
    重放信息含 name / raw（原始完整结果，供替换进 ToolMessage）/ result（SSE 截断版）。

    ADR-0015 阶段 2：粘性 approved 退役（不再置位——一次批准永不解锁后续工具）；
    重放**成功**后补 dedup 登记（LLM 重调同参工具命中「重复调用」，不双执行）；
    重放失败则撤销登记，保证老师的重试不被误拦；
    内存 pending 丢失时从 gate 实体重建（cid 非空时）。

    gate_id（grilling 决策 A）：布尔门 legacy 批准也走强绑定——老师点的旧卡
    gate_id 与当前开放门不符时，replay_target 会从**当前**门重建并执行（跨发射）。
    不符 → 拒执行 + 重发当前门让 UI 对齐（草稿门在 _gate_decision_stream 已如此）。
    空 gate_id（旧客户端单门）沿用 check_binding 兼容放行契约。

    actor_id：批准人。布尔门（assign_adaptive_practice 这类）只走这条重放路径，
    `_gate_decision_stream` 里 `if gate_did` 那段记账覆盖不到它 —— 决定账本
    「谁批的」只能在这里落，透传空则账本 actor 为空。"""
    if not guard_state:
        return [], None
    # 重放目标**以门实体为准**（gate-lifecycle 2.4）：`gate_service.replay_target`
    # 是唯一入口，`_rebuild_pending_from_gate` 走的就是它。原先反过来 —— 内存优先、
    # 实体只当兜底 —— 于是"要执行的东西"有两个来源，两者一旦漂开，老师批的和他
    # 拿到手的就不是一回事（2.6 的载荷指纹已经能把这种漂开拦下来，但**让它不可能
    # 发生**比拦下来更好）。
    pending = _pending_from_entity(cid)
    if not pending:
        # 「读不到可重放的门」有两种，调用方要分开对待，所以这里必须分辨：
        #   · 客户端**提交了 gate_id、那一行还在**（只是已经不是 open 了）
        #     ⇒ 老师批的是一扇已经翻篇的门，回"这次确认已经处理过了"让他对齐；
        #   · 其余（压根没有待批）
        #     ⇒ 返回**空**。这是既有契约：`_resume` 那条路 `if _direct:` 非空就会
        #       走"直接重放"分支并 return，把空返回撑成非空会把**正常 resume**
        #       也拐进那条路（发一句假的"已处理过"、还不跑续接）。
        #   ——2026-10-17 实测：撑成非空那版就这么把第二个调用点改坏了。
        # 限定 `approved`：这条只解释"点了批准却没什么可放"。放弃/作废（approved=False）
        # 走的是另一条收尾路径，那里返回空是它自己的约定，别顺手改了。
        if approved and cid and gate_id and _gate_row_exists(cid, gate_id):
            return _gate_taken_events(cid), None
        return [], None
    if not approved:
        return [], None
    if cid:
        mismatch = _replay_binding_mismatch(cid, gate_id)
        if mismatch is not None:
            return mismatch, None
    # ── 先占门再执行（R8 D9=A）─────────────────────────────────────
    # 批准后跑的是不可逆动作（删题库、执行命令、发送），所以"许可"必须是"占到这扇
    # 门"这件事本身：条件更新 + 立即提交，第二次批准就看不见 open 行了。事后关门
    # 挡不住双执行 —— 那中间的窗口里，同一扇门仍可被再点一次。
    from app.services.gate_service import gate_service as _gs
    from app.services.work_unit import new_work_unit as _unit
    _gid = gate_id
    if cid and not _gid:
        with _entity_db() as db:      # 旧客户端不带 gate_id：占当前那扇开着的门
            _gid = (_gs.get_open_gate(db, cid) or {}).get("gate_id") or ""
    if cid and not _gid:
        # 有会话却解析不出门 = 拿不到许可 ⇒ **拒绝执行**（gate-lifecycle 2.3，fail-closed）。
        # 上面那段刚说过「许可必须是占到这扇门这件事本身」—— 此前 `_gid` 为空时
        # `if cid and _gid:` 整块被短路跳过，**claim 没做、动作照跑**：等于没有门
        # 也能执行不可逆操作，这正是"同一扇门被批两次"的入口。
        # `pending` 非空即意味着 guard 当初登记门成功过；门此刻不见了，只能是
        # 已被另一次批准占掉、或被判死。
        #
        # 限定在 `cid` 非空：判据是「**某会话**有待批准的动作 ⟹ 该会话必须有一扇门」，
        # 没有会话时压根没有门可言 —— 决定的流式入口（`_gate_decision_stream`）
        # 一律带 cid，那条无会话的路只在直接驱动重放的测试里走到。
        logger.warning("replay_no_gate cid=%s tool=%s", cid, pending["name"])
        return _gate_taken_events(cid), None
    if cid and _gid:
        try:
            with _unit() as db:
                _claimed = _gs.claim_gate(db, _gid)
        except Exception as _e:  # noqa: BLE001
            logger.warning("gate claim errored cid=%s gate=%s: %s", cid, _gid, _e)
            return _gate_taken_events(cid), None
        if not _claimed:
            # 结构化留痕：同一扇门被第二次批准时必走这里，是"双执行已被挡住"的证据
            logger.warning("close_miss event=gate_already_decided cid=%s gate=%s",
                           cid, _gid)
            return _gate_taken_events(cid), None
        # 载荷指纹校验（gate-lifecycle 2.6）：占住门之后、执行之前 —— 此刻门已经是
        # `executing`，所以拒绝的自己收口交给 helper（见它的注释）。
        payload_gone = _replay_payload_mismatch(
            cid, _gid, pending["name"], pending.get("kwargs") or {})
        if payload_gone is not None:
            return payload_gone, None
    from agent.tools.filesystem import set_elevate_once
    set_elevate_once()
    try:
        _replayed = await pending["fn"](**pending["kwargs"])
    except Exception as _e:  # noqa: BLE001
        # 载荷里那句人话只到得了页面，服务端必须另有一条能 grep 的记录 —— 否则事后
        # 问「这扇门到底跑没跑」查无此人（与 :1388 那条 persist 失败同一纪律）。
        logger.warning("gate_replay_failed cid=%s gate=%s tool=%s: %s",
                       cid, _gid, pending["name"], _e)
        _replayed = json.dumps({"error": f"重放失败: {_e}"}, ensure_ascii=False)
    _payload = json.loads(_replayed) if isinstance(_replayed, str) else _replayed
    if not isinstance(_payload, dict):
        _payload = {"result": _payload}
    _success = "error" not in _payload
    # 三态收口（gate-lifecycle 2.7）：`claim_gate` 说的是「这活儿归我了」，
    # 只有这里才说「做完了没」—— 原来是同一个状态。失败**必须退回 open**：
    # 否则门已被消费，老师拿着确认卡点什么都只会得到「已经处理过了」，
    # 而那个动作一次都没跑成。成功才落 `executed`。
    if cid and _gid:
        try:
            with _unit() as db:
                _gs.finish_gate(db, _gid, _success)
                # 决定账本与关门**同一次提交**（前置一）。只记**布尔门**：草稿门批准
                # 已由 `_gate_decision_stream` 的 `if gate_did` 那处记过（kind="draft"，
                # 带 draft_id/version），而它批准后同样走到这里 —— 同一张卡记两行，
                # 一次点击就成了两笔决定。布尔门（assign_adaptive_practice 这类）没有
                # 别的记账点，不在这里补「谁批的这次布置练习」就查无此人。
                # 失败不记：门已退回 open、动作一次都没跑成，没有决定可言。
                # 门的类别问实体，别拿 `get_open_gate`（`claim_gate` 已把它占成
                # executing，那只看 open）—— 与 `gate_plan_step` 同一纪律。
                if _success:
                    from app.models.database import AgentGate as _GateLedger
                    _gate_kind = (db.query(_GateLedger.kind)
                                  .filter(_GateLedger.gate_id == _gid).scalar())
                    if _gate_kind == "boolean":
                        _gs.record_decision_log(
                            db, _gid, "approved", conversation_id=cid,
                            actor_user_id=actor_id, tool_name=pending["name"],
                            kind="boolean")
        except Exception as _e:  # noqa: BLE001
            logger.warning("gate finish errors cid=%s gate=%s: %s", cid, _gid, _e)
    # dedup 登记只在重放**成功**时补：它唯一的目的是防同参双执行（guard.py:187），
    # 而失败的那次什么都没跑。无条件登记会把老师的重试拦成「检测到重复调用，
    # 已跳过」，且 seen_calls 随会话缓存跨回合存活 → 任务永久卡在失败态。
    # 门却在执行前就占了：两者管的不是同一件事——登记管「这一次调用算不算重复」，
    # 门管「这一轮决定还在不在」。
    try:
        if _success:
            guard_state.remember_call(pending["name"], pending["kwargs"])
        else:
            guard_state.forget_call(pending["name"], pending["kwargs"])
    except Exception as _e:  # noqa: BLE001
        # 登记失败会改变重试形状（该记的没记 → 同参再跑一遍；该清的没清 → 老师的重试
        # 被「检测到重复调用」拦掉），静默不可接受。
        logger.warning("gate_dedup_update_failed cid=%s gate=%s tool=%s: %s",
                       cid, _gid, pending["name"], _e)
    events = [json.dumps({
        "type": "tool_result",
        "name": pending["name"],
        "tool": pending["name"],
        "success": _success,
        "result": _json_safe_payload(_payload),
    }, ensure_ascii=False)]
    # 重放成功后补发计划步骤完成事件（2026-08：计划卡不再永远停在"执行中"）。
    # 步归属**按 gate_id 从门实体读**：上面 `claim_gate` 已经把它占成 executed，
    # 所以不能用 `get_open_gate`。内存 pending 是 guard 拦下时登记的，不认识计划步；
    # 按它的 tool_name 回退也找不到（门是 send_learning_plan，绑的步却是
    # generate_learning_plan）—— 2026-10-16 实测里步就卡在 running 收不了尾。
    events.extend(_plan_completion_events(guard_state, pending["name"], _success,
                                          _replay_plan_step(cid, _gid, pending), cid))
    # 这扇门已经在执行前被 `claim_gate` 占成 executed，这里不再关第二次。
    # 历史形状是"跑完再关"：中间那段窗口里第二次批准能从仍 open 的行重建待批
    # （`_rebuild_pending_from_gate`），同一破坏性工具就再跑一遍（练习布置两遍）。
    replay_info = {
        "name": pending["name"],
        "raw": _replayed,
        "result": _json_safe_payload(_payload, cap=12000),
        "success": _success,
    }
    return events, replay_info


def _pending_from_entity(cid: str) -> dict | None:
    """本会话当前待批调用 —— **一律问实体**（gate-lifecycle 2.4 收尾）。

    形状与原先内存里那份 `pending_approval` 对齐（`name` / `fn` / `kwargs` /
    `plan_step`），所以原来读内存的调用点换过来几乎不用动别的。
    实现就是 `_rebuild_pending_from_gate` —— 那个函数本来就是"从门实体重建"，
    2.4 让它从**兜底**升成**首选**之后，它就是这里唯一该有的来源。

    为什么必须收口到它：内存那份随进程消失，而门是实体（ADR-0015 重启可重建）。
    "有没有门在等""等的是哪个工具""要重放什么"这三个问题，答案都只在门那一行。
    """
    if not cid:
        return None
    return _rebuild_pending_from_gate(cid)


def _void_open_gate(cid: str) -> bool:
    """作废本会话当前那扇开门（布尔门 + 老师打字给了修改指令 = 放弃这次待批）。

    语义来源：ADR-0015「决策通道唯一化」—— 门开着时打字**永不构成批准**，
    在布尔门上只能是"这次待批作废，按新消息继续"。

    **为什么必须是关行而不是清内存**（gate-lifecycle 2.4 收尾）：重放与 C1-Q3
    全拦都改成问门那一行之后，进程内存里的东西已经不是判据了。不关门，
    老师那句「先别管这个」不会有任何效果：卡片还在，工具照样被全拦。

    失败只记日志、不抛：作废是善后，不该把老师这条消息打断在半路
    （与 `_converge_orphan_gates` 同一条纪律）。
    """
    if not cid:
        return False
    try:
        from app.services.gate_service import gate_service
        with _entity_db() as db:
            og = gate_service.get_open_gate(db, cid)
        if not og:
            return False
        done = _close_gate_solo(og["gate_id"], "cancelled")
        if done:
            logger.info("void: 旧确认门已作废 cid=%s gate=%s tool=%s",
                        cid, og["gate_id"], og.get("tool_name"))
        return done
    except Exception as e:  # noqa: BLE001
        logger.warning("void: 关旧门失败 cid=%s: %s", cid, e)
        return False


def _gate_row_exists(cid: str, gate_id: str) -> bool:
    """这扇门（**任意状态**）还在不在这个会话上。

    用来分辨「老师批的是一扇已经翻篇的门」与「压根没有待批」—— 两者在
    `_direct_replay_events` 里都表现为"读不到可重放的门"，但调用方要分开对待
    （见那里的注释）。按会话一起查：只凭 gate_id 会认下别人会话上的门。

    读不出来当 False（= 没有这扇门）：这里判的是**回哪句人话**，不是放不放行，
    读失败时退到"没有待处理的确认请求"即可，不该把一次决定打断在半路。
    """
    from app.models.database import AgentGate
    try:
        with _entity_db() as db:
            return db.query(AgentGate).filter(
                AgentGate.gate_id == gate_id,
                AgentGate.conversation_id == cid,
            ).first() is not None
    except Exception:  # noqa: BLE001
        logger.warning("gate row lookup failed cid=%s gate=%s", cid, gate_id,
                       exc_info=True)
        return False


def _rebuild_pending_from_gate(cid: str) -> dict | None:
    """从 gate 实体重建待批调用（ADR-0015 阶段 2）。

    gate-lifecycle 2.4 起它是**重放目标的首选来源**（不再是兜底），所以这里
    那个裸 `except Exception: return None` 换成了留痕：读不出来时静默返回 None，
    调用方会悄悄退回内存那一份 —— 而"悄悄退回另一个来源"正是本步要消掉的东西。
    返回 None 仍然保留（真的没有门时它是正常答案），但**读失败**必须留下话。
    """
    try:
        from app.services.gate_service import gate_service
        with _entity_db() as db:
            target = gate_service.replay_target(db, cid)
    except Exception:
        logger.warning("rebuild pending from gate failed cid=%s", cid, exc_info=True)
        return None
    if not target:
        return None
    from agent.langgraph_agent_v2 import _TOOL_BY_NAME
    fn = _TOOL_BY_NAME.get(target["tool_name"])
    if fn is None:
        logger.warning("rebuild pending: unknown tool cid=%s tool=%s",
                       cid, target.get("tool_name"))
        return None
    return {"name": target["tool_name"], "fn": fn, "kwargs": target["kwargs"],
            # 门绑住的计划步号（gate-lifecycle 批次 1）：重放要落终态的是**这一步**，
            # 与门的 tool_name 可以不同名。缺省 0 = 存量门未记录 → 走旧查找。
            "plan_step": target.get("plan_step") or 0}


def _converge_orphan_gates(cid: str) -> None:
    """把本会话「已不算数」的门行落成 superseded（gate-lifecycle 2/④）。

    收敛器**早就存在**（`gate_service.supersede_stale_gates`，自称"唯一的假门写库点"），
    缺的是**情况变了的时候有人去跑它** —— 草稿被别处（学生管理页直发、面板直发）
    发出去之后，这扇门按判据不再算数，却没有任何一条路径会去收敛。

    专门给"发现时门已经死了"那条路用（重放前的早退、判据对不上之类）。
    正常的终态化不靠它：正常收场在 `send_draft` 里当场收敛（gate-lifecycle 2.2）。
    失败只记日志不抛 —— 收敛是善后，不该把一次老师决定打断在半路。
    """
    if not cid:
        return
    try:
        from app.services.gate_service import gate_service
        from app.services.work_unit import new_work_unit
        with new_work_unit() as db:
            gate_service.supersede_stale_gates(db, cid)
    except Exception as e:  # noqa: BLE001
        logger.warning("orphan gate convergence failed cid=%s: %s", cid, e)


def _gate_taken_events(cid: str) -> list:
    """这扇门已被另一次批准占掉（或已不存在）时回给前端的事件：不执行，只对齐。

    与 `_replay_binding_mismatch` 是同一条人话文案家族；区别在这里连"当前开放门"
    都可能已经没有了 —— 刚被占到它的那次批准关掉。
    """
    from app.services.gate_service import gate_open_event, gate_service
    events = [json.dumps(
        {"type": "error", "message": "这次确认已经处理过了，请按最新确认卡操作。",
         "recoverable": True}, ensure_ascii=False)]
    try:
        with _entity_db() as db:
            og = gate_service.get_open_gate(db, cid)
    except Exception as e:  # noqa: BLE001
        logger.warning("gate resync read failed cid=%s: %s", cid, e)
        og = None
    if og:
        events.append(json.dumps(gate_open_event(
            og.get("gate_id") or "", og.get("action") or "",
            og.get("message") or "", draft_id=og.get("draft_id") or "",
            version=og.get("version"), tool_name=og.get("tool_name") or ""),
            ensure_ascii=False))
    return events


def _replay_binding_mismatch(cid: str, gate_id: str):
    """布尔重放的强绑定校验。返回 None=放行；返回 list[str]（SSE 事件）=拒执行。

    决定带的 gate_id 与当前开放门不符 → 重发当前门让 UI 对齐（批准门 A 永不
    执行门 B）。校验本身出错 fail-closed：明确报错，绝不静默放行（对齐草稿门）。
    空 gate_id + 单门开着 → check_binding 返回 None（旧客户端兼容）。"""
    from app.services.gate_service import gate_open_event, gate_service
    try:
        with _entity_db() as db:
            binding = gate_service.check_binding(db, cid, gate_id or "")
    except Exception as e:  # noqa: BLE001
        logger.warning("replay binding check failed cid=%s: %s", cid, e)
        return [json.dumps(
            {"type": "error", "message": "确认门状态校验失败，请重试",
             "recoverable": True}, ensure_ascii=False)]
    if not binding:
        return None
    og = binding["open_gate"]
    events = [
        json.dumps({"type": "error",
                    "message": "确认请求已更新，请按最新确认卡重新决定。",
                    "recoverable": True}, ensure_ascii=False),
        json.dumps(gate_open_event(
            og.get("gate_id") or "", og.get("action") or "",
            og.get("message") or "", draft_id=og.get("draft_id") or "",
            version=og.get("version"), tool_name=og.get("tool_name") or ""),
            ensure_ascii=False),
    ]
    return events


def _replay_payload_mismatch(cid: str, gate_id: str, name: str, kwargs: dict):
    """重放载荷指纹校验（gate-lifecycle 2.6）。返回 None=放行；list[str]=拒执行。

    挡的是「老师批的是 A、执行的是 B」：卡片的内容来自门那一行，而这里真正要跑的是
    内存 pending 带的那一份（`pending["fn"](**pending["kwargs"])`）。两者在四条开门
    路径上一致，但一致性此前没有任何东西守着。

    **调用点在 `claim_gate` 之后**，那一步已经把门占成 `executing`。所以拒绝时必须
    自己把门收口 —— 丢下不管的门会一直停在 `executing`，下次启动被
    `sweep_stuck_executing` 扫成 `in_doubt`，给老师冒出一张"结果未知"的卡，
    而那次执行**根本没发生**。落 `superseded` 而不是退回 `open`：这张卡的内容已经
    不可信，再批一次只会再撞一次同样的墙，重来一次比修它便宜。

    校验本身出错也 fail-closed（同 `_replay_binding_mismatch`）：宁可让老师重来，
    不可静默放行一份说不清来源的载荷。
    """
    from app.services.gate_service import gate_service
    try:
        with _entity_db() as db:
            bad = gate_service.verify_replay_payload(db, gate_id or "", name, kwargs or {})
    except Exception as e:  # noqa: BLE001
        logger.warning("replay payload check errored cid=%s gate=%s: %s", cid, gate_id, e)
        bad = "确认门校验出错"
    if not bad:
        return None
    logger.warning("replay_payload_mismatch cid=%s gate=%s tool=%s: %s",
                   cid, gate_id, name, bad)
    try:
        from app.services.work_unit import new_work_unit as _u2
        with _u2() as db:
            gate_service.close_gate(db, gate_id or "", "superseded")
    except Exception as e:  # noqa: BLE001
        logger.warning("replay payload mismatch close errored cid=%s gate=%s: %s",
                       cid, gate_id, e)
    return [json.dumps(
        {"type": "error",
         "message": f"这次确认没有执行：{bad}。请重新发起这次操作。",
         "recoverable": True}, ensure_ascii=False)]


def _replay_plan_step(cid: str, gate_id: str, pending: dict) -> int:
    """这次重放属于计划里的哪一步 —— **门实体是唯一真源**（gate-lifecycle 批次 1/2）。

    按 `gate_id` 读、不看门的状态（重放前 `claim_gate` 已把它占成 executed）。
    门查不到才退到内存 pending 带的（`_rebuild_pending_from_gate` 会填 ——
    进程重启后就是靠它），再没有就是 0 = 未记录，调用方回退旧查找。
    """
    if gate_id:
        try:
            from app.services.gate_service import gate_service
            with _entity_db() as db:
                step = gate_service.gate_plan_step(db, gate_id)
            if step:
                return step
        except Exception as e:  # noqa: BLE001
            logger.warning("replay plan_step lookup failed cid=%s gate=%s: %s",
                           cid, gate_id, e)
    return int((pending or {}).get("plan_step") or 0)


def _plan_abort_events(guard_state, cid: str) -> list[str]:
    """异常中止时给计划收尾：running → failed、pending → cancelled。

    2026-10 实测：工具抛 TypeError 穿透后整条 SSE 断在半路，计划卡永久停在
    「执行中」、第 4/5 步永远「未开始」，用户只看到一句报错。异常路径必须自己
    把账做完，否则「收尾对账」只在成功路径上成立。
    """
    steps = getattr(guard_state, "plan_steps", None) or []
    if not steps:
        return []
    from app.services.plan_run_service import (compute_abort_transition,
                                               project_transitions)
    transitions = compute_abort_transition(steps)
    return project_transitions(cid, transitions) if transitions else []


def _plan_pending_desc(guard_state) -> str:
    """未执行的计划步骤 → 「第4步 generate_questions、第5步 …」；无残留返回空串。

    与 _plan_tail_events 取同一个判据（pending/running 才算没执行），
    避免自动续跑和收尾对账对「哪几步没做」出现两套口径。
    """
    steps = getattr(guard_state, "plan_steps", None) or []
    left = [s for s in steps if s.get("status") in ("pending", "running")]
    return "、".join(f"第{s.get('step')}步 {s.get('skill')}" for s in left)


def _suspended_gate_events(cid: str, interrupts, guard_state=None) -> list[str]:
    """图在确认门处挂起（HITL 中间件 per_call interrupt）→ 登记门行 + 发门卡。

    与旧路径只差**判定点**：旧的是工具返回 `approval_needed`、由 adapter 发卡；
    新的是图挂起、工具根本没有返回。卡片载荷仍是门行那一份（ADR-0030 单一真源）。
    一次挂多个（并行 tool_calls）时按 gate_id 去重，一扇门只发一张卡。

    D+ 切片 3：载荷换成中间件的 `tool_approval` 形态（type/name/args/
    tool_call_id，不带 gate_id）——gate_id 由 channel 按 `boolean_gate_id`
    从 (cid, name, args) 算出，与决定端点反查用同一函数，天然对上。
    门行在这里登记（挂起时）而非挂起前：中间件在模型→工具边界暂停，
    guard 侧没有任何先于挂起的副作用要补偿。

    guard_state 供步骤投影：挂起的步骤要标 `awaiting_confirmation`——
    legacy 是工具返回 approval_needed 时 adapter 推的，HITL 工具没有返回，
    不标的话收尾对账会把「等门」误判成「没执行」（A 类实测就这么红的）。
    """
    from app.services.gate_service import (boolean_gate_id, gate_open_event,
                                            gate_service)
    gate = {}
    try:
        with _entity_db() as db:
            gate = gate_service.get_open_gate(db, cid) or {}
    except Exception as e:  # noqa: BLE001
        logger.warning("suspend gate lookup failed cid=%s: %s", cid, e)
    from app.services.work_unit import new_work_unit
    out, seen = [], set()
    for it in interrupts or ():
        val = getattr(it, "value", None) or {}
        tool = str(val.get("name") or "")
        if not tool:
            continue  # 非门挂起（面板 interrupt 等）不在这里发卡
        args = dict(val.get("args") or {})
        gid = boolean_gate_id(cid, tool, args)
        if gid in seen:
            continue
        seen.add(gid)
        try:
            if not gate or gate.get("gate_id") != gid:
                with new_work_unit() as db:
                    gate_service.open_gate(
                        db, cid, kind="boolean",
                        tool_name=tool,
                        kwargs=args,
                        action="", message="此操作需要老师确认后执行")
                with _entity_db() as db:
                    gate = gate_service.get_open_gate(db, cid) or gate
        except Exception as e:  # noqa: BLE001
            logger.warning("suspend gate register failed cid=%s gate=%s: %s", cid, gid, e)
        out.append(json.dumps(gate_open_event(
            gid, str(gate.get("action") or ""),
            str(gate.get("message") or "此操作需要老师确认后执行"),
            draft_id=str(gate.get("draft_id") or ""), version=gate.get("version"),
            tool_name=tool), ensure_ascii=False))
        # 挂起步标「等待确认」（同一次把 plan_progress 事件发出去 + 落库）
        if guard_state is not None:
            try:
                out.extend(_gate_step_events(
                    cid, guard_state, "awaiting_confirmation", tool, 0))
            except Exception as e:  # noqa: BLE001
                logger.warning("suspend step mark failed cid=%s tool=%s: %s",
                               cid, tool, e)
    return out


def _plan_tail_events(guard_state, cid: str) -> list[str]:
    """本轮收尾对账：计划里没执行的步骤标 cancelled，并**明说哪几步没做**。

    计划由模型自己执行（没有编排器，见 agent/tools/plan.py 铁律），它可以在任意
    一步后收尾；未执行的步原本永远停在 pending —— 整轮结束既无完成事件也无提示，
    这是 C 类「一句话多产出」被静默截断的根因（2026-10 实测：「准备一次月考」
    5 步只跑 3 步）。这里不改执行策略（不自动续跑），只把静默变成可见。

    转移规则在 plan_run_service.compute_tail_transition，投影走
    project_transitions —— 与 _plan_completion_events 同一个入口，避免第二套
    事件/落库口径。
    """
    steps = getattr(guard_state, "plan_steps", None) or []
    if not steps:
        return []
    # 计划**停在确认门等批准**时不能对账：那不是"没做完"，是"暂停中"。
    # 2026-10 三端联动实测：首次流在 step4 awaiting_confirmation 结束，收尾对账
    # 把 step5/6/7 全标 cancelled；老师随后批准、三步都真的执行了，计划卡却显示
    # 「已放弃」——上面写"已送达 2 位家长"，下面写"已放弃"。
    if any(s.get("status") == "awaiting_confirmation" for s in steps):
        logger.info("plan tail skipped: waiting for gate cid=%s", cid)
        return []
    leftover = [s for s in steps if s.get("status") in ("pending", "running")]
    if not leftover:
        return []
    from app.services.plan_run_service import (compute_tail_transition,
                                               project_transitions)
    events = project_transitions(cid, compute_tail_transition(steps))
    names = "、".join(f"第{s.get('step')}步 {s.get('skill')}" for s in leftover)
    events.append(json.dumps({
        "type": "text",
        "content": f"本轮有 {len(leftover)} 步没有执行：{names}。要继续的话告诉我。",
    }, ensure_ascii=False))
    return events


def _plan_completion_events(guard_state, tool_name: str, success: bool = True,
                            plan_step: int = 0, cid: str = "") -> list[str]:
    """批准重放后，把步骤转移**投影并落库**（转移规则在
    plan_run_service.compute_replay_transition：approve→running（重放中）→
    重放成功才 completed 并启动下一步）。

    `plan_step` 来自门实体（`agent_gates.plan_step`）：要落终态的是**门绑住的那一步**，
    它与门的 tool_name 可以不同名 —— 门是 `send_learning_plan`，它绑的步却是
    `generate_learning_plan`（生成即门）。按工具名找会找不到，那一步就永远停在 running。

    **走 `project_transitions`**：本模块文件头写着「收权：所有调用面只算转移（纯函数），
    投影与持久化走**这一个入口**」——2026-09-21 那次架构审查收敛出来的规矩。
    这里原先自己 append 事件、从不落库，是唯一没走那个入口的调用面：内存态
    `completed`、SSE 也发了，`plan_runs.steps` 里那一步还是 `running`，
    刷新页面与端到端断言读库，于是判它没做完（2026-10-17 从影子库挖出来）。
    """
    steps = getattr(guard_state, "plan_steps", None) or []
    if not steps and cid:
        # 门重放走 pending["fn"]，不经 _guarded —— 那个请求可能是**新的进程内实例**，
        # guard_state.plan_steps 是空的，于是这里直接 return []，步骤转移一条都不落。
        # 结果：计划卡把「已确认发送」的三步标成「已放弃」（2026-10 三端联动实测，
        # 同屏上方写着已送达 2 位家长，下方写着已放弃）。空内存态时从 DB 回填。
        try:
            from app.models.database import get_db as _get_db
            from app.services import plan_run_service as _prs
            _db = next(_get_db())
            try:
                _run = _prs.get_active_run(_db, cid) or {}
                steps = _run.get("steps") or []
            finally:
                _db.close()
            if steps:
                guard_state.plan_steps = steps
                logger.info("plan steps restored from db cid=%s n=%d", cid, len(steps))
        except Exception as _e:  # noqa: BLE001
            logger.warning("plan steps restore failed cid=%s: %s", cid, _e)
    if not steps:
        return []
    from app.services.plan_run_service import (compute_replay_transition,
                                               project_transitions)
    transitions = compute_replay_transition(steps, tool_name, success, plan_step)
    return project_transitions(cid, transitions)


async def _replace_pending_tool_result(agent, config, tool_name: str, raw_result) -> bool:
    """C 层：把 checkpoint 里最后一条「{tool_name}」的 ToolMessage 内容替换为
    批准重放后的真实结果（保留 tool_call_id，LangGraph 配对校验不受影响）。
    成功后 LLM 续接上下文里就有完整工具结果，续接提示无需再重复塞结果。"""
    from langchain_core.messages import RemoveMessage, ToolMessage
    try:
        state = await agent.aget_state(config)
        msgs = list((state.values.get("messages", []) or []) if state else [])
        if not msgs:
            return False
        target = None
        for m in reversed(msgs):
            if getattr(m, "type", "") == "tool" and getattr(m, "name", "") == tool_name:
                target = m
                break
        if target is None or not getattr(target, "id", None) or not getattr(target, "tool_call_id", None):
            return False
        content = raw_result if isinstance(raw_result, str) else json.dumps(raw_result, ensure_ascii=False)
        new = ToolMessage(content=content, name=tool_name, tool_call_id=target.tool_call_id)
        await agent.aupdate_state(config, {"messages": [RemoveMessage(id=target.id), new]})
        return True
    except Exception as _e:  # noqa: BLE001
        logger.warning("replace pending tool result failed: %s", _e)
        return False


async def _remove_injected_replay_message(agent, config) -> None:
    """A 层：续接完成后从 checkpoint 摘除注入的续接提示（role=user 的内部消息），
    历史彻底不残留内部提示。失败静默（B 层历史过滤兜底）。"""
    from langchain_core.messages import RemoveMessage
    try:
        state = await agent.aget_state(config)
        msgs = list((state.values.get("messages", []) or []) if state else [])
        for m in reversed(msgs):
            if getattr(m, "type", "") in ("human", "user"):
                if not getattr(m, "id", None):
                    continue
                content = str(getattr(m, "content", "") or "")
                if content.lstrip().startswith(_INTERNAL_NOTE_PREFIX):
                    await agent.aupdate_state(config, {"messages": [RemoveMessage(id=m.id)]})
                    return
    except Exception as _e:  # noqa: BLE001
        logger.warning("remove injected replay message failed: %s", _e)


async def _repair_tail_toolcall_gaps(agent, config, filler: str) -> bool:
    """续接前修补「AI(tool_calls) 未紧跟其 tool 应答」的序列错位。

    机理（2026-09-20 实测 repro-zhou-0920）：门中断发生在工具应答写入之前，
    AI(assign_call) 后面没有 ToolMessage；门暂停期间 LLM 又产生新消息，
    批准后重放结果只能追加在尾部——AI 调用与其 tool 消息被隔开，
    OpenAI 校验即 400（"insufficient tool messages following tool_calls"）。

    修法：从后向前找第一条「紧随性违规」的 AIMessage（其 tool 应答没有
    紧跟在后，注意不能用全局应答集合判定——迟到消息会漏检）；
    摘除其后尾部 → 把 tail 中属于该调用的 ToolMessage 提到最前（没有则用
    filler 补位）→ 其余消息按原序跟回（RemoveMessage + 同 id 重加）。"""
    from langchain_core.messages import RemoveMessage, ToolMessage
    try:
        state = await agent.aget_state(config)
        msgs = list((state.values.get("messages", []) or []) if state else [])
        if not msgs:
            return False
        anchor_idx, anchor_ids = None, []
        for i in range(len(msgs) - 1, -1, -1):
            tcs = getattr(msgs[i], "tool_calls", None) or []
            if not tcs:
                continue
            expected = {tc.get("id") for tc in tcs if tc.get("id")}
            followed = set()
            j = i + 1
            while j < len(msgs) and type(msgs[j]).__name__ == "ToolMessage":
                followed.add(getattr(msgs[j], "tool_call_id", ""))
                j += 1
            if followed != expected:
                anchor_idx, anchor_ids = i, list(expected)
                break
        if anchor_idx is None:
            return False
        tail = msgs[anchor_idx + 1:]
        pulled, rest = [], []
        for m in tail:
            if (getattr(m, "type", "") == "tool"
                    and getattr(m, "tool_call_id", "") in anchor_ids):
                pulled.append(m)
            else:
                rest.append(m)
        for cid in anchor_ids:
            if not any(getattr(t, "tool_call_id", "") == cid for t in pulled):
                pulled.append(ToolMessage(content=filler, name="tool", tool_call_id=cid))
        ops = [RemoveMessage(id=m.id) for m in tail if getattr(m, "id", None)]
        # 重加的消息必须剥掉旧 id：LangGraph 的 add_messages 对同 id 是
        # 「原位更新」（Remove+同 id 重加=净零操作，实测），剥 id 才会
        # 作为新消息按新顺序追加到尾部
        for m in pulled + rest:
            c = m.model_copy(deep=True)
            c.id = None
            ops.append(c)
        await agent.aupdate_state(config, {"messages": ops})
        return True
    except Exception as _e:  # noqa: BLE001
        logger.warning("repair tail toolcall gaps failed: %s", _e)
        return False


async def _run_continuation(agent, adapter, config, msg: str, log_tag: str):
    """续接执行器公共骨架（2026-09-21 架构审查 D）：注入用户消息驱动 LLM 续接，
    超时/异常兜底出 error 事件（绝不静默），结束后摘除注入消息。
    note（打回/放弃）与 replay（批准）两条路径只差消息构造，执行同权。

    D+ 切片 3：续接中也可能撞 HITL 门（图在模型→工具边界挂起）。挂起时
    (a) 发门卡 + 挂起步标「等待确认」（否则收尾对账把等门误判成没执行），
    (b) 在 adapter 上立 `_suspended_mid_turn` 旗——调用方（PlanContinue 循环、
    决定流）看到就停，别再注入新输入把挂起点顶掉，
    (c) **跳过注入消息摘除**：挂起状态下动 state 会把老师的决定点丢掉。

    每次续接前**重冻**合并门快照：批准（claim）刚写了 scope 行，这里冻结到的
    是含新授权的状态——续接轮里模型再调被覆盖的工具，HITL 谓词判「已覆盖」
    不再弹卡（卡上「本次批准覆盖第 N、M 步」的承诺由此兑现）。"""
    _suspended = False
    _gs_cont = getattr(adapter, "_guard_state_ref", None)
    if _gs_cont is not None:
        try:
            _gs_cont.freeze_hitl_scope()
        except Exception as _e:  # noqa: BLE001
            logger.warning("continuation scope refreeze failed (%s): %s", log_tag, _e)
    try:
        async with asyncio.timeout(_TURN_TIMEOUT):
            async for event in agent.astream_events(
                {"messages": [{"role": "user", "content": msg}]},
                config, version="v2",
            ):
                for r in adapter.feed(event):
                    yield r
        try:
            _st = await agent.aget_state(config)
            _susp_ints = (getattr(_st, "interrupts", ()) or ()) \
                if getattr(_st, "next", None) else ()
        except Exception as _e:  # noqa: BLE001
            logger.warning("continuation suspend probe failed (%s): %s", log_tag, _e)
            _susp_ints = ()
        if _susp_ints:
            _suspended = True
            adapter._suspended_mid_turn = True
            _cid_c = getattr(adapter, "_conversation_id", "") or ""
            if _cid_c:
                _gs_c = SimpleNamespace(
                    plan_steps=getattr(adapter, "_plan_steps", None) or [])
                for _ev in _suspended_gate_events(_cid_c, _susp_ints, _gs_c):
                    yield _ev
    except TimeoutError:
        yield json.dumps({"type": "error",
                          "message": "处理超时，本次执行已中止，请重试或换个方式提问。",
                          "recoverable": True}, ensure_ascii=False)
    except Exception as e:  # noqa: BLE001
        _log_error(log_tag)
        yield json.dumps({"type": "error",
                          "message": f"恢复处理失败: {str(e)[:200]}",
                          "recoverable": True}, ensure_ascii=False)
    finally:
        if not _suspended:
            await _remove_injected_replay_message(agent, config)


async def _stream_replay_continuation(agent, adapter, config, replay_info):
    """批准重放后恢复 LLM：先把重放结果替换进上文 ToolMessage（C 层），
    续接提示只写简短指引；替换失败时回退为把结果塞进消息（不丢结果）。"""
    if not replay_info:
        return
    tool_name = replay_info.get("name", "")
    replaced = await _replace_pending_tool_result(agent, config, tool_name, replay_info.get("raw"))
    if replaced:
        msg = _REPLAY_CONTINUE_MSG.format(name=tool_name)
    else:
        replaced = await _repair_tail_toolcall_gaps(
            agent, config,
            replay_info.get("raw") if isinstance(replay_info.get("raw"), str)
            else json.dumps(replay_info.get("raw"), ensure_ascii=False))
        if replaced:
            msg = _REPLAY_CONTINUE_MSG.format(name=tool_name)
        else:
            msg = _REPLAY_CONTINUE_MSG_FALLBACK.format(result=replay_info.get("result"))
    # 原位替换只改内容不改位置：若 ToolMessage 与其 assistant 调用之间已被
    # 门暂停期间的新消息隔开（2026-09-20 实测），仍会 400——续接前总是
    # 扫一遍错位（序列已合法时这是无操作）
    await _repair_tail_toolcall_gaps(agent, config, "（结果已记录）")
    async for r in _run_continuation(agent, adapter, config, msg, "replay-continuation"):
        yield r


def _restore_plan_state(adapter, guard_state) -> None:
    """把持久化的计划步骤恢复到新适配器（跨流：批准/恢复后计划卡继续推进）。

    真源优先级 = DB PlanRun > 内存 guard_state.plan_steps：PlanRun 行
    （status=running/paused）在服务重启后仍存在，是 ADR-0013/0016「重启可重建」
    对 todolist 的承诺；旧实现只读进程内 guard_state，重启后计划态丢失，后续
    工具调用无法推进计划卡（缺陷 #3）。DB 无活跃 run 时才回退内存。

    双写（adapter + guard_state）是必须的：计划发射器（_plan_completion_events /
    _decision_unlock_events / _gate_step_events）读 guard_state.plan_steps，而
    adapter→guard 的回写只存在于 /stream 的生成循环，/resume 路径没有。
    只写 adapter 会让重启后的**批准**回合推进 DB 却补发不出 plan_progress。"""
    steps = []
    cid = getattr(adapter, "_conversation_id", "") or ""
    if cid:
        try:
            from app.services.plan_run_service import plan_run_service
            with _entity_db() as db:
                run = plan_run_service.get_active_run(db, cid)
            if run:
                steps = run.get("steps") or []
        except Exception as e:  # noqa: BLE001
            # 留痕后回退内存：DB 读不到活跃 run 是本函数的降级路径，但不能无声
            logger.warning("plan run restore failed cid=%s: %s", cid, e)
            steps = []
    if not steps:
        steps = getattr(guard_state, "plan_steps", None) or []
    try:
        if steps:
            adapter._plan_steps = steps
            adapter._plan_active = True
            if guard_state is not None:
                guard_state.plan_steps = steps
    except Exception as e:  # noqa: BLE001
        logger.warning("plan state attach failed cid=%s: %s", cid, e)


# ═══════════════════════════════════════════════════════════════════
# 确认门三值决定（ADR-0013）：批准 / 打回+反馈 / 放弃
# 决定写进草稿数据（门由数据状态强制），打回路由到确定性修订，运行跨回合继续。
# ═══════════════════════════════════════════════════════════════════

# 门工具分类表已收拢 decision_intake（S3：kind 判定与工件路由同一份真源）。
# 这里按原名引用，_gate_artifact 的路由代码不必跟着改名。
from app.services.decision_intake import GATE_DRAFT_TOOLS as _GATE_DRAFT_TOOLS  # noqa: E402


def _gate_artifact(tool_name: str):
    """按门工具名取 (草稿 service 单例, 修订后前端组件名, 工件标签)。"""
    from app.services import plan_draft_service as _p
    from app.services import parent_report_draft_service as _r
    route = _GATE_DRAFT_TOOLS.get(tool_name)
    if route is None:
        route = _GATE_DRAFT_TOOLS["send_learning_plan"]
    svc, component, label = route
    singletons = {
        "plan_draft_service": _p.plan_draft_service,
        "parent_report_draft_service": _r.parent_report_draft_service,
    }
    return singletons[svc], component, label

# 打回后的 LLM 续接提示：说明修改点即可，门已确定性重开，禁止再调发送工具
_REJECT_CONTINUE_MSG = (
    _INTERNAL_NOTE_PREFIX
    + "老师打回了{artifact}（反馈：{feedback}），系统已按反馈修订出第 {version} 版"
    "并更新了下方预览卡片。请用一两句话向老师说明本次修改了什么，"
    "然后等待老师在确认卡上再次决定。不要调用{tool}——"
    "老师批准后系统会自动发送。）"
)
# 放弃后的 LLM 续接提示：本步取消、运行继续
_ABANDON_CONTINUE_MSG = (
    _INTERNAL_NOTE_PREFIX
    + "老师放弃发送{target}的{artifact}（草稿已保留，可稍后再次发送）。"
    "请继续执行计划中的剩余步骤；若没有剩余步骤，请向老师总结当前进展。）"
)










def _gate_draft_id(guard_state, decision, cid: str = "") -> str:
    """决定要打的草稿：decision.draft_id → 待批调用 kwargs → 该生活跃草稿。

    最后一级兜底先问 `decision_intake.is_plan_gate`：布尔门没有草稿工件，抓了会把
    一张布尔门当草稿门处理（状态错位）。"""
    if decision.get("draft_id"):
        return decision["draft_id"]
    # 问实体（2.4 收尾）：待批那件事的草稿 id 记在门那行上。
    # `cid` 缺省时回落到 guard 自己的会话号 —— 调用点漏传一个参数就查不到门，
    # 那种失败是静默的（返回空串、下游把它当"没有草稿"），所以这里兜一层。
    pending = _pending_from_entity(
        cid or getattr(guard_state, "conversation_id", "")) or {}
    kwargs = pending.get("kwargs", {}) or {}
    if kwargs.get("draft_id"):
        return kwargs["draft_id"]
    sid = kwargs.get("student_id") or ""
    if sid and decision_intake.is_plan_gate(cid, pending):
        try:
            svc, _component, _label = _gate_artifact(pending.get("name", ""))
            with _entity_db() as db:
                active = svc.get_active_draft(db, sid)
            return (active or {}).get("draft_id", "")
        except Exception as e:  # noqa: BLE001
            # C2：解包 bug 修复后这里不该再被触发；仍失败则留痕，不再静默吞成空串
            logger.warning("gate draft_id resolve failed (%s): %s", pending.get("name"), e)
            return ""
    return ""


def _open_gate_plan_step(cid: str) -> int:
    """当前开着的门绑在哪一步（0 = 没有门 / 存量门未记录）。

    决定的落点要从**门实体**取（gate-lifecycle 批次 1），不在调用方猜。
    读失败一律回 0 —— 让它走旧的回退路径，比在这里抛出去打断一次老师决定好。
    """
    if not cid:
        return 0
    try:
        from app.services.gate_service import gate_service
        with _entity_db() as db:
            og = gate_service.get_open_gate(db, cid)
        return int((og or {}).get("plan_step") or 0)
    except Exception as e:  # noqa: BLE001
        logger.warning("open gate plan_step lookup failed cid=%s: %s", cid, e)
        return 0


def _decision_unlock_events(cid, guard_state, action: str) -> list[str]:
    """三值决定 → 步过渡事件（approve→running 重放中 / reject→revising /
    abandon→cancelled）。转移规则在 plan_run_service.compute_decision_transition，
    投影与持久化走 project_transitions 单一入口（2026-09-21 架构审查 B）。

    步归属取自门实体，与重放那条走同一个来源 —— 两边各猜各的正是「步收不了尾」的成因。
    """
    steps = getattr(guard_state, "plan_steps", None) or []
    if not steps:
        return []
    from app.services.plan_run_service import compute_decision_transition, project_transitions
    transitions = compute_decision_transition(steps, action, _open_gate_plan_step(cid))
    return project_transitions(cid, transitions)


def _gate_step_events(cid, guard_state, status: str, tool_name: str = "",
                      step_no: int = 0) -> list[str]:
    """门状态变化投影为 plan_progress 事件（等待确认/修订中/取消），并持久化到 run。

    步归属以门实体记的 `plan_step` 为准（与决定/重放同一个来源）。`step_no` 为 0
    才是存量门未记录，回退按 skill 找 —— 门的 tool_name 与它绑的步可以不同名
    （生成即门），只按 tool_name 找会把绑定步漏在原地。
    """
    steps = getattr(guard_state, "plan_steps", None) or []
    allowed = ("pending", "running", "awaiting_confirmation", "revising", "failed")
    if step_no:
        target = next((s for s in steps if s.get("step") == step_no
                       and s.get("status") in allowed), None)
    else:
        target = next((s for s in steps if s.get("skill") == tool_name
                       and s.get("status") in allowed), None)
    if target is None:
        return []
    target["status"] = status
    from app.services.plan_run_service import project_transitions
    return project_transitions(cid, [(target["step"], status)])


# 这里原有一个 `_persist_send_completion(cid, guard_state, tool_name, success)`：
# 重放之后再按 `skill == tool_name` 把**同名步**落一次库。2026-10-17 删掉了它 ——
# 但**不是因为它多余**，而是因为它长错了地方、还用错了过滤器。来龙去脉值得记：
#
# 它诞生时 `_plan_completion_events` **只 append SSE 事件、从不落库**（内存态
# completed、库里还停在 running），它是把 DB 补上的那一下。2.8 让前者走
# `project_transitions` 之后，它就只剩"第二步"这一半职责了 —— 而那一半是**承重的**：
#
#   · 老师批准的是**生成步**（生成即门，1.5 —— 卡上确认的就是那份产物），
#     但重放真正跑的是**发送工具**。一次批准办完两件事，计划也把这两件事写成两步
#     （`PLAN_PROMPT` 明确要求「提到同步家长必须包含 `generate_parent_report`
#     **与** `send_report_to_parent` 步骤」），两步就该都收。
#
# 它错在**只认 `running`/`awaiting_confirmation`**：
#   · 计划里那一步恰好刚被 `_start_next_step` 启动成 running（7 步形状）→ 匹配上，全绿。
#     看着像正常，其实是**巧合**。
#   · 那一步还是 `pending`（8 步形状：`send_learning_plan` 在生成步之后）→ 匹配不上，
#     那一格**永远悬着**、run 收不了官。
#
# 删掉之后端到端当场红在 `send_report_to_parent` 停在 running，才看清这一层。
# **规则本身没删，它搬进了 `compute_replay_transition`**（转移规则的唯一家园）：
# 按 skill 认、`pending` 也算数、并排除已经由门定位的那一步。
# 所以这里不需要再有第二个落库点 —— 要改这条规则，去 `plan_run_service` 改。


async def _stream_note_continuation(agent, adapter, config, note: str):
    """注入内部提示让 LLM 续接（打回说明/放弃续跑共用）。
    输出带 data: 前缀（调用方直接 yield；批注路径的 SSE 包装约定）。"""
    # 打回/放弃意味着被门拦住的工具不会执行——其 tool_calls 永远不会有
    # 真实结果，先补取消说明占位（含尾部重写），序列合法后再续接
    await _repair_tail_toolcall_gaps(
        agent, config, "（该操作已被老师打回或取消，未实际执行）")
    async for r in _run_continuation(agent, adapter, config, note, "gate-continuation"):
        yield f"data: {r}\n\n"


def _gate_draft_snapshot(draft_id: str):
    """按 id 取草稿快照（计划/报告两类工件都试）。"""
    try:
        from app.services.plan_draft_service import plan_draft_service
        from app.services.parent_report_draft_service import parent_report_draft_service
        with _entity_db() as db:
            d = plan_draft_service.get_draft(db, draft_id)
            return d or parent_report_draft_service.get_draft(db, draft_id)
    except Exception:
        return None


def _parent_of_student(actor_id: str, student_id: str) -> bool:
    """家长门的数据范围 = 亲子绑定（active）。"""
    try:
        from app.services.parent_task_service import parent_of_student
        with _entity_db() as db:
            return parent_of_student(db, actor_id, student_id)
    except Exception:
        return False


def _teacher_in_scope(actor_id: str, student_id: str) -> bool:
    try:
        from app.services.plan_draft_service import plan_draft_service
        with _entity_db() as db:
            return plan_draft_service.teacher_teaches_student(db, actor_id, student_id)
    except Exception:
        return False


async def _gate_decision_stream(cid, decision, guard_state, agent, adapter, config):
    """三值决定统一处理器（/resume decision 分支与 /stream 打字反馈共用）。

    approve → 草稿记批准 → 确定性重放发送 → 续接 LLM → todolist 继续
    reject  → 草稿确定性修订 → 推 v2 面板 + 重开门卡 → 续接 LLM 说明修改点
    abandon → 门步骤取消、运行继续（草稿保留）
    """
    from agent.tools.context import current_identity, current_teacher

    action = decision.get("action", "")
    # 问实体（2.4 收尾）：待批的是哪个工具，记在门那行上 —— 不是进程内存里。
    gate_pending = _pending_from_entity(cid) or {}
    # C1：不再默认 "send_learning_plan"——门状态取不到工具名即视为损坏，
    # 下游 _direct_replay_events 无待批调用会返回空 → 明确报错，绝不拿
    # 默认工具去重放（杜绝「批准 A 卡重放 B 调用」的交叉发射）。
    tool_name = gate_pending.get("name", "")
    gate_did = _gate_draft_id(guard_state, decision, cid)
    actor_id = current_teacher() or ""

    def gate_error(message: str):
        return [
            f"data: {json.dumps({'type': 'error', 'message': message, 'recoverable': True}, ensure_ascii=False)}\n\n",
            f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n",
            "data: [DONE]\n\n",
        ]

    # ── 强绑定（ADR-0015 阶段 2，回归红线④）：决定带的 gate_id 与开着的门
    # 不一致 → 拒绝执行，重发当前门状态让 UI 对齐——批准门 A 永不执行门 B。
    _open_entity = None
    if cid:
        try:
            from app.services.gate_service import gate_open_event, gate_service
            # 两条都是纯读，但裸 `next(get_db())` 的会话没人关，每条 SSE 流都泄一个
            # （S4）；统一走 `_entity_db()`，退出即关。
            with _entity_db() as db:
                _open_entity = gate_service.get_open_gate(db, cid)
                _binding = gate_service.check_binding(
                    db, cid, str(decision.get("gate_id") or ""))
            if _binding:
                _og = _binding["open_gate"]
                _mismatch_evt = {
                    "type": "error",
                    "message": "确认请求已更新，请按最新确认卡重新决定。",
                    "recoverable": True,
                }
                _gate_evt = gate_open_event(
                    _og.get("gate_id") or "", _og.get("action") or "",
                    _og.get("message") or "", draft_id=_og.get("draft_id") or "",
                    version=_og.get("version"), tool_name=_og.get("tool_name") or "")
                for _ev in (
                    f"data: {json.dumps(_mismatch_evt, ensure_ascii=False)}\n\n",
                    f"data: {json.dumps(_gate_evt, ensure_ascii=False)}\n\n",
                    f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n",
                    "data: [DONE]\n\n",
                ):
                    yield _ev
                return
        except Exception as _e:  # noqa: BLE001
            # fail-closed：绑定校验本身出错 → 明确报错，绝不静默跳过（code-review 2026-09-20）
            logger.warning("gate binding check failed: %s", _e)
            for _ev in gate_error("确认门状态校验失败，请重试"):
                yield _ev
            return

    # ADR-0009 fail-closed：批准/打回是不可逆动作与门循环入口，缺身份直接拒绝；
    # 有身份则查 Data Scope——角色感知（批 3 P2 REQ-PAT-002）：家长门走绑定校验
    # （门实体本就挂在家长会话上），教师门维持授课关系检查
    actor_role = (current_identity().get("role") or "").lower()
    if action in ("approve", "reject"):
        if not actor_id:
            for ev in gate_error("缺少用户身份，无法处理确认门决定"):
                yield ev
            return
        if gate_did:
            draft_snapshot = _gate_draft_snapshot(gate_did)
            if draft_snapshot:
                in_scope = (
                    _parent_of_student(actor_id, draft_snapshot["student_id"])
                    if actor_role == "parent"
                    else _teacher_in_scope(actor_id, draft_snapshot["student_id"]))
                if not in_scope:
                    for ev in gate_error(
                        f"你没有处理学生 {draft_snapshot['student_id']} 学习计划的权限（Data Scope 不含该生）"):
                        yield ev
                    return

    # ── reject：确定性修订（闭环靠契约，不靠模型自觉；按工件路由）──
    artifact_svc, component_name, artifact_label = _gate_artifact(tool_name)
    if action == "reject":
        feedback = (decision.get("feedback") or "").strip()
        if not feedback:
            for ev in gate_error("打回必须附修改意见"):
                yield ev
            return
        if not gate_did:
            # 布尔门 reject（S2 / ADR-0031）：不执行、说一句为什么 —— 没有草稿可修订、
            # 不重开，与 LangChain 1.x HITL 的 reject-with-feedback 同构：反馈作为结果
            # 回给模型，模型继续做后面的事。S2 之前这里只会报「找不到草稿」——而布尔门
            # 的 reject 在分类时就被丢成 legacy，根本到不了这里（审查 C1 的同族）。
            _gate_step_no = _open_gate_plan_step(cid)
            _close_gid = (_open_entity or {}).get("gate_id") or ""
            if cid and _close_gid:
                try:
                    from app.services.gate_service import gate_service as _gs_rej
                    from app.services.work_unit import new_work_unit as _nwu_rej
                    with _nwu_rej() as db:
                        if _gs_rej.close_gate(db, _close_gid, "cancelled"):
                            _gs_rej.record_decision_log(
                                db, _close_gid, "rejected", conversation_id=cid,
                                actor_user_id=actor_id, tool_name=tool_name,
                                kind="boolean", detail=feedback)
                except Exception as _e:  # noqa: BLE001
                    logger.warning("gate close (boolean reject) failed gid=%s: %s",
                                   _close_gid, _e)
            for ev in _gate_step_events(cid, guard_state, "cancelled", tool_name,
                                        _gate_step_no):
                yield f"data: {ev}\n\n"
            guard_state.begin_turn()
            note = (_INTERNAL_NOTE_PREFIX
                    + f"老师打回了这次操作（反馈：{feedback}），本次不执行、"
                    "也不会重新弹出确认卡。请根据反馈继续做计划里后面能做的事，"
                    "不要重复调用刚才的工具。）")
            async for ev in _stream_note_continuation(agent, adapter, config, note):
                yield ev
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"
            return
        # 算与写分开（前置一 / R1 D2=A）：模型修订在单元外跑完，单元里只有两条写
        # （新版本行 + 重开的门），并且**不跨 yield 持有单元** —— SSE 往下的 yield
        # 什么时候被消费完不由我们决定，把写事务挂在上面就是无限期持 WAL 写锁。
        from app.services.gate_service import draft_gate_id, gate_open_event, gate_service
        from app.services.work_unit import new_work_unit, session_scope
        try:
            with session_scope() as db:
                computed = artifact_svc.compute_revision(db, gate_did, feedback)
        except Exception as e:  # noqa: BLE001
            computed = {"error": f"修订失败: {e}"}
        if "error" in computed:
            for ev in gate_error(computed["error"]):
                yield ev
            return

        # 步归属先问门实体（重开门会重置行的其余字段，但保留 plan_step）；
        # 放弃路径要在关门**之前**取 —— 门一关就查不到了。
        _gate_step_no = _open_gate_plan_step(cid)
        try:
            with new_work_unit() as db:
                v2 = artifact_svc.record_decision(
                    db, draft_id=gate_did, action="reject",
                    actor_id=actor_id, feedback=feedback, computed=computed)
                if "error" in v2:
                    raise _DecisionAborted(v2["error"])
                # 门实体重开（同 draft_id → 同 gate_id；版本/文案刷新）。
                # 与新版本判决同一次提交：只落版本不落门，就是一张重启后重建不出的卡。
                if cid:
                    gate_service.open_gate(
                        db, cid, kind="draft", tool_name=tool_name,
                        kwargs={"student_id": v2.get("student_id", ""),
                                "draft_id": gate_did},
                        draft_id=gate_did, version=int(v2.get("version") or 0),
                        action=("send_parent" if tool_name == "send_report_to_parent"
                                else "send_student"),
                        message=f"修订版（第 {v2.get('version')} 版）已生成，请确认是否发送")
                # 账本与判决、重开门同一次提交：分开提交会留下「改了版本、没记账」的
                # 半截状态，而 §7.5「谁批的」只能从这张表回答。
                gate_service.record_decision_log(
                    db, draft_gate_id(gate_did), "rejected",
                    conversation_id=cid, actor_user_id=actor_id,
                    tool_name=tool_name, kind="draft", detail=feedback)
        except _DecisionAborted as e:
            for ev in gate_error(str(e)):
                yield ev
            return
        except Exception as e:  # noqa: BLE001
            for ev in gate_error(f"修订失败: {e}"):
                yield ev
            return

        sid = v2.get("student_id", "")
        gate_action = "send_parent" if tool_name == "send_report_to_parent" else "send_student"
        reopen_msg = f"修订版（第 {v2.get('version')} 版）已生成，请确认是否发送"
        for ev in _gate_step_events(cid, guard_state, "revising", tool_name,
                                    _gate_step_no):
            yield f"data: {ev}\n\n"
        # 推 v2 面板（component 按名替换，前端预览原地更新）
        if component_name == "parent_report":
            comp_data = {"student_id": sid, "draft_id": gate_did,
                         "report_data": v2.get("content", {})}
        else:
            comp_data = {"student_id": sid, "draft_id": gate_did,
                         "plan": v2.get("content", {})}
        comp_evt = {"type": "component", "component": component_name,
                    "params": {"student_id": sid, "draft_id": gate_did,
                               "gate_id": draft_gate_id(gate_did),
                               "version": v2.get("version", 0), "data": comp_data}}
        yield f"data: {json.dumps(comp_evt, ensure_ascii=False)}\n\n"
        # 重开门后重新登记待批发送（批准后 _direct_replay_events 原机制重放）；
        # fn 按工具名解析（批 3：家长派任务门 parent_send_task 不在 diagnosis 模块）。
        # 内存 pending 丢失（重启）时 tool_name 为空 → 明确报错，不 getattr("") 崩
        if not tool_name:
            for ev in gate_error("确认门状态已失效（服务可能重启过），请刷新后重新处理"):
                yield ev
            return
        # 这里原有一段"重新登记待批发送"（把 `{name, fn, kwargs}` 塞进内存那份
        # pending）。2.4 收尾删掉了 —— 上面那行 `gate_service.open_gate(...)` 已经把
        # **门那一行**重开了，重放读的就是它（`_pending_from_entity`）。
        # 顺带删掉的还有为它解析 `send_fn` 的那几行（`_TOOL_BY_NAME` / 家长模块兜底）：
        # 现在没有第二份载荷需要它了。
        gate_evt = gate_open_event(draft_gate_id(gate_did), gate_action, reopen_msg,
                                   draft_id=gate_did, version=v2.get("version", 0),
                                   tool_name=tool_name)
        yield f"data: {json.dumps(gate_evt, ensure_ascii=False)}\n\n"
        for ev in _gate_step_events(cid, guard_state, "awaiting_confirmation", tool_name,
                                    _gate_step_no):
            yield f"data: {ev}\n\n"
        # 门已重开，恢复 LLM 说明本次修改点
        guard_state.begin_turn()
        note = _REJECT_CONTINUE_MSG.format(
            artifact=artifact_label,
            tool=tool_name,
            feedback=feedback, version=v2.get("version", 0))
        async for ev in _stream_note_continuation(agent, adapter, config, note):
            yield ev
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return

    # ── abandon：本步取消，运行继续，草稿保留 ────────────────────
    if action == "abandon" and (gate_did or _open_entity):
        # 关真实 gate_id——布尔门没有 draft_id，必须用决定时解析的当前门实体
        from app.services.gate_service import draft_gate_id as _dg
        _gate_step_no = _open_gate_plan_step(cid)
        _close_gid = (_open_entity or {}).get("gate_id") or _dg(gate_did)
        if cid and _close_gid:
            try:
                # 关门与记账同一次提交（原先只有关门，账本永远空着）。
                # `_close_gate_solo` 仍是单写路径，留给 1051 那条「超时收敛」用。
                from app.services.gate_service import gate_service as _gs_abandon
                from app.services.work_unit import new_work_unit as _new_abandon_unit
                with _new_abandon_unit() as db:
                    if _gs_abandon.close_gate(db, _close_gid, "cancelled"):
                        _gs_abandon.record_decision_log(
                            db, _close_gid, "cancelled", conversation_id=cid,
                            actor_user_id=actor_id, tool_name=tool_name,
                            kind=("draft" if gate_did else "boolean"))
            except Exception as _e:  # noqa: BLE001
                logger.warning("gate close (abandon) failed gid=%s: %s", _close_gid, _e)
        sid = (gate_pending.get("kwargs", {}) or {}).get("student_id", "")
        for ev in _gate_step_events(cid, guard_state, "cancelled", tool_name,
                                    _gate_step_no):
            yield f"data: {ev}\n\n"
        guard_state.begin_turn()
        note = _ABANDON_CONTINUE_MSG.format(target=sid or "该学生", artifact=artifact_label)
        async for ev in _stream_note_continuation(agent, adapter, config, note):
            yield ev
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return

    # ── C1 fail-closed 终止守卫：走到此处 reject/abandon 均已各自 return，
    # 只有显式 approve 才允许落到重放发送。任何其它/缺失 action 绝不默认执行。
    if action != "approve":
        for ev in gate_error("无法识别的确认决定，请按最新确认卡重新操作。"):
            yield ev
        return

    # ── approve：草稿记批准 → 确定性重放（沿用既有机制）────────────
    if gate_did:
        # 批准必须先提交，重放里那条 `send_draft` 才拿得到放行钥匙（门由数据强制）。
        from app.services.work_unit import new_work_unit
        try:
            with new_work_unit() as db:
                approved = artifact_svc.record_decision(
                    db, draft_id=gate_did, action="approve", actor_id=actor_id)
                if "error" not in approved:
                    from app.services.gate_service import (
                        draft_gate_id as _dgate, gate_service as _glog)
                    _glog.record_decision_log(
                        db, str(decision.get("gate_id") or _dgate(gate_did)),
                        "approved", conversation_id=cid, actor_user_id=actor_id,
                        tool_name=tool_name, kind="draft",
                        detail=json.dumps({"draft_id": gate_did,
                                           "version": approved.get("version", 0)},
                                          ensure_ascii=False))
            if "error" in approved:
                # 草稿已被处理（如学生管理页已确认发送）——显式告知，不盲目重放。
                # 但这扇门得**一并判死**（gate-lifecycle ④）：它就是被那次发送留下的
                # 孤儿。留着它的后果不是多一行脏数据 —— guard 拿「库里还有 open 的门」
                # 当「有一张确认卡待老师处理」，于是拒掉 agent 的**一切**工具调用，
                # 计划卡永远停在等待态、run 永远 running（2026-10-16 端到端实测）。
                _converge_orphan_gates(cid)
                for ev in gate_error(approved["error"]):
                    yield ev
                return
        except Exception as e:  # noqa: BLE001
            logger.warning("gate approve record failed: %s", e)

    # 决定解锁（ADR-0015：approve → running「重放中」）——todolist 不从
    # awaiting 直接跳 completed，重放结果出来才落终态（issue #18 story 7）
    for ev in _decision_unlock_events(cid, guard_state, "approve"):
        yield f"data: {ev}\n\n"
    direct_events, replay = await _direct_replay_events(guard_state, True, cid, actor_id=actor_id)
    if not direct_events:
        for ev in gate_error("没有待处理的确认请求"):
            yield ev
        return
    for ev in direct_events:
        yield f"data: {ev}\n\n"
    if replay is None:
        # 重放没跑：这扇门已被另一次批准占掉（`claim_gate` 抢不到），或根本没有待批
        # 调用。事件里已带回人话与当前门状态，绝不带着 None 往下走续接。
        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
        yield "data: [DONE]\n\n"
        return
    # 门不需要在这里关：`_direct_replay_events` 在执行前已把它占成 executed。
    # 这里再关一次是"跑完再关"时代的残留，留着只会把双执行的窗口藏在两个关门点之间。
    # 计划步同理：`_direct_replay_events` 里的 `_plan_completion_events` 已经按门的
    # `plan_step` 落过一次了，这里**不再补第二次**（原先那句 `_persist_send_completion`
    # 是 2.8 之前的补漏，2.8 之后成了用旧规则多推一格的双写 —— 见文件里那段注释）。
    # 2026-09-21：批准后续接事件必须带 data: SSE 帧——此前裸 yield，
    # 前端解析器整段丢弃（批准后无下文/"卡住"的真实面目，实测 arch4 复现）
    async for ev in _stream_replay_continuation(agent, adapter, config, replay):
        yield f"data: {ev}\n\n"
    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
    yield "data: [DONE]\n\n"


@router.post("/chat/langgraph/resume")
async def agent_chat_langgraph_resume(
    request: ChatRequest,
    http_request: Request,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("agent", "create", allow_self=True)),
):
    """中断恢复 — 发送 Command(resume=...) 继续被 interrupt 暂停的 graph。会话必须属于调用者。"""
    from dotenv import load_dotenv
    load_dotenv()

    from langgraph.types import Command
    from langgraph.errors import GraphInterrupt

    from agent.tools.filesystem import set_file_access_level, set_workspace_root
    set_file_access_level(request.file_access)
    set_workspace_root(request.workspace)

    # 教师身份注入（同 /stream，2026-08）
    from agent.tools.context import set_current_identity
    _teacher_id2 = current_user.get("user_id", "") or ""
    set_current_identity(
        _teacher_id2,
        current_user.get("role", ""),
        current_user.get("school_id"),
    )

    cid = request.conversation_id
    # 会话归属（阶段 4.6 批次 E）：原来回退到共享的 "default" 线程，等于所有人共用一个会话
    require_owned(db, cid, current_user)

    # 每会话执行锁（ADR-0016 §5）。async with 收口 eager span 异常释放（W5）。
    async with conversation_scope(cid) as conv_lock:
        # 学生身份：批准重放也会跑工具（分步导学作答回流），身份与 /stream 同一口径
        from agent.tools.context import set_current_student
        set_current_student(_resolve_student_identity(request, current_user, db) or "")
        persona = _resolve_persona(request, current_user)

        user_response = request.message or ""

        # ADR-0013 三值决定：decision 对象优先，旧字符串向后兼容。
        # 非草稿门（assign_adaptive_practice 等布尔门）退化为 legacy approved/cancelled。
        # `pending_name` 问实体（2.4 收尾）：待批的是哪个工具，记在门那行上。
        # 三值决定的草稿/布尔分流依赖它（`decision_intake.resume_intent` 的 legacy 分支）。
        _decision, user_response = decision_intake.resume_intent(
            request, (_pending_from_entity(cid) or {}).get("name", ""),
        )

        store3 = await _get_app_store()
        # 复用缓存的 guard_state, 避免 approved 状态丢失（须透传给 create_v2，
        # 否则新建 GuardState 导致批准后 LLM 再 request_approval 又中断）
        cached_guard = _guard_state_cache.get(cid)
        agent, guard_state, AdapterClass = await _get_or_create_agent(
            persona, request.provider, store=store3,
            guard_state=cached_guard, teacher_id=_teacher_id2,
        )
        if guard_state is not None:
            try:
                guard_state.begin_turn()  # 批准/恢复也是新的一轮输入：重置重复计数
            except Exception:
                pass
            guard_state.conversation_id = cid  # 布尔门实体登记需要（ADR-0015 阶段 2）
            # 冻结在**决定处理之前**：HITL 批准会经 claim 写 scope 行，恢复重放
            # 的 when 谓词必须看到与挂起时一致的状态，否则 reject 被静默忽略。
            # 批准后的模型续接走 _run_continuation，那里会重冻（含新 scope）。
            guard_state.freeze_hitl_scope()
        adapter = AdapterClass(conversation_id=cid or "")
        adapter._guard_state_ref = guard_state  # 续接重冻快照用（同 /stream）
        # 恢复计划状态（2026-08：批准/恢复后计划卡继续推进，不永远停在"执行中"）
        _restore_plan_state(adapter, guard_state)
        config = build_config(cid)

        # 批准/取消前只清孤儿（ADR-0016：毒化/孤儿中断 → 迁移，零模型调用）
        # 原生 interrupt 试点（2026-10-21）：**挂起点不是孤儿**。`_repair_thread_state`
        # 的判据是「有未决任务 ⇒ interrupt 退役前留下的残留中断 ⇒ 迁到新线程」，而试点
        # 下这条判据正好命中本轮要恢复的那个挂起点：迁走它 = 老师的决定没有落点。
        # 实测（cid t-1792614466194）：pre next=True ints=1 gids=[g-tool-assign…] →
        # thread migrated → post next=True ints=0，于是下面的 `_by_id` 反查落空、
        # `Command(resume="approved")` 不回到任何挂起点，门行永远 open、账本没有决定、
        # 计划 4-8 步被收尾对账标 cancelled。有挂起点就跳过迁移，让恢复回到原地。
        _live_susp = await agent.aget_state(config)
        if getattr(_live_susp, "next", None) and getattr(_live_susp, "interrupts", ()):
            logger.info("resume: keep live interrupt cid=%s n=%s", cid,
                        len(getattr(_live_susp, "interrupts", ()) or ()))
            _ok2, _blocked_reason2 = True, ""
        else:
            _ok2, _blocked_reason2, config = await _repair_thread_state(agent, config, cid)
        if not _ok2:
            async def _blocked_gen2():
                _err2 = json.dumps(
                    {'type': 'error', 'message': _blocked_reason2, 'recoverable': False},
                    ensure_ascii=False,
                )
                yield f"data: {_err2}\n\n"
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
            return _locked_stream(conv_lock, _blocked_gen2)

        # ADR-0013：草稿门三值决定处理器（无图中断的常态路径——门由数据状态强制）
        if _decision:
            resume_state_gate = await agent.aget_state(config)
            if not getattr(resume_state_gate, "next", None):
                return _locked_stream(
                    conv_lock,
                    lambda: _gate_decision_stream(cid, _decision, guard_state, agent, adapter, config))
            # 真实中断在挂 → 退化 legacy Command 流（user_response 已映射）

        from agent.context_manager import trim_context
        await trim_context(agent, config)

        full_reply = []
        # 批准类 resume：LangGraph 恢复中断时会重放 request_approval 的 on_tool_start，
        # adapter 会重复发 awaiting_approval → 用户批准后不该再收到确认请求。
        # 过滤首个重放事件（2026-08 修复「布置练习反复需要批准」）。
        # ADR-0015：只认协议值 "approved"（decision_intake.resume_intent 已归一化）。
        skip_resume_approval = (user_response or "").strip().lower() == "approved"
        # 阶段 4：过滤按 gate_id 收窄——只吞被批准那个门的旧卡事件，
        # 同一流里后续新开的门照常弹出（不再吞整条流）。
        skip_gate_id = ""
        if skip_resume_approval:
            if isinstance(_decision, dict):
                skip_gate_id = _decision.get("gate_id", "")
            if not skip_gate_id:
                try:
                    from app.services.gate_service import gate_service
                    with _entity_db() as db:
                        _og5 = gate_service.get_open_gate(db, cid)
                    skip_gate_id = (_og5 or {}).get("gate_id", "")
                except Exception:
                    skip_gate_id = ""

        # 2026-08 确定性重放：无未决中断（模型没调 request_approval）时，
        # 直接重放 guard 登记的待批调用，批准后恢复 LLM 继续回答（链路不断）
        # 强绑定用**客户端提交的** gate_id（决策 A）——用当前开放门会永远自匹配。
        _submitted_gate_id = (decision_intake.normalize_decision(request) or {}).get("gate_id", "")
        # HITL 中间件决定的前置事件（reject/abandon 的步骤取消投影）与批准收口
        # 信息，由下面的挂起配对块填充，generate() 开头先发、结尾收口。
        _pre_resume_events: list[str] = []
        _hitl_claim = None  # {"gate_id":…, "tool":…} —— approve 已 claim，待 finish
        _resume_state = await agent.aget_state(config)
        if not (getattr(_resume_state, "next", None)):
            _direct, _replay = await _direct_replay_events(
                guard_state,
                (user_response or "").strip().lower() == "approved",
                cid,
                _submitted_gate_id,
                actor_id=_teacher_id2,
            )
            if _direct:
                async def _direct_gen():
                    for _ev in _direct:
                        yield f"data: {_ev}\n\n"
                    async for _ev in _stream_replay_continuation(agent, adapter, config, _replay):
                        yield f"data: {_ev}\n\n"
                    yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                return _locked_stream(conv_lock, _direct_gen)

        # ── HITL 中间件挂起：按 interrupt id 精确回填老师的决定 ──
        # per_call 模式下 `Command(resume=<裸值>)` 在多个挂起点时会被直接拒绝
        # （必须按 interrupt id 回填）。中间件载荷不带 gate_id，这里按
        # `boolean_gate_id(cid, name, args)` 算出候选 gid 与老师点的那张卡配对
        # —— 与挂起检测（_suspended_gate_events）同一个函数，天然对上。
        # 存量 legacy open 门（无挂起点）不会命中这里，落到下面的旧重放路径。
        _resume_payload = user_response
        _resume_state = await agent.aget_state(config)
        _pending_ints = getattr(_resume_state, "interrupts", ()) or ()
        if getattr(_resume_state, "next", None) and _pending_ints:
            _want = (_decision or {}).get("gate_id") or _submitted_gate_id or ""
            # 回填动作归一收拢在 decision_intake（S1）：布尔门没有三值决定
            # （resume_intent 置 None、意图只在 user_response），照抄字符串会让
            # 恢复成功的那一轮当场判错。
            from app.services.decision_intake import interrupt_action as _ia
            from app.services.gate_service import boolean_gate_id as _bgid
            _act = _ia(_decision, user_response)
            _by_id, _hit = {}, {}
            for _it in _pending_ints:
                _v = getattr(_it, "value", None) or {}
                _tool_v = str(_v.get("name") or "")
                if not _tool_v:
                    continue  # 非门挂起（面板等）不在决定端点处理
                _gid_v = _bgid(cid, _tool_v, dict(_v.get("args") or {}))
                if _want and _gid_v != _want:
                    continue
                _by_id[_it.id] = {
                    "type": "approve" if _act == "approve" else "reject",
                }
                _hit = {"gate_id": _gid_v, "tool": _tool_v}
            if _by_id:
                _approve = _act == "approve"
                _fb = (str(_decision.get("feedback") or "")
                       if isinstance(_decision, dict) else "")
                if not _approve and _fb:
                    # reject 的反馈跟着回填给模型（与 LangChain HITL
                    # reject-with-feedback 同构：反馈作为工具结果回给模型）
                    for _k in _by_id:
                        _by_id[_k]["message"] = _fb or "老师没有批准这次操作"
                _resume_payload = _by_id
                # 门收口与账本**早于** `Command(resume=...)`：决定已做出，
                # 门行必须立刻翻终态/占住，否则挂起期间它还挡着每一次写操作。
                # approve → claim（open→executing，B 的许可原语：占不到就不许
                #   执行）；执行结果由恢复流里的 on_tool_end 决定 finish 落
                #   executed 还是退回 open（三态收口，与 legacy 重放同构）。
                # reject/abandon → close cancelled；计划步在决定时刻就标
                #   cancelled（中间件 reject 不产生工具结果事件，
                #   compute_tool_transition 那条路喂不到）。
                try:
                    from app.services.gate_service import gate_service as _gs_r
                    from app.services.work_unit import new_work_unit as _nwu
                    _gid_r = str(_hit.get("gate_id") or _want)
                    _tool_r = str(_hit.get("tool") or "")
                    _dec_word = ("approved" if _approve
                                 else "rejected" if _act == "reject" else "cancelled")
                    with _nwu() as _db_r:
                        if _approve:
                            _claimed = _gs_r.claim_gate(_db_r, _gid_r)
                            if not _claimed:
                                logger.warning(
                                    "hitl resume claim miss cid=%s gate=%s（可能已被处理）",
                                    cid, _gid_r)
                            else:
                                _hitl_claim = {"gate_id": _gid_r, "tool": _tool_r}
                        else:
                            if not _gs_r.close_gate(_db_r, _gid_r, "cancelled"):
                                logger.warning(
                                    "hitl resume close miss cid=%s gate=%s", cid, _gid_r)
                        # 账本与门收口同一次提交；reject 记 rejected 且反馈进
                        # detail（与 legacy 决定流两本账一个口径）。
                        _gs_r.record_decision_log(
                            _db_r, _gid_r, _dec_word,
                            conversation_id=cid,
                            actor_user_id=_teacher_id2,
                            tool_name=_tool_r,
                            kind="boolean",
                            detail=_fb)
                except Exception as _e:  # noqa: BLE001
                    logger.warning("hitl gate close failed cid=%s: %s", cid, _e)
                if not _approve:
                    # 决定流前置事件：步骤取消投影（计划卡第 N 步 → cancelled）
                    _gate_step_no = _open_gate_plan_step(cid)
                    for _ev in _gate_step_events(cid, guard_state, "cancelled",
                                                 _hit.get("tool") or "",
                                                 _gate_step_no):
                        _pre_resume_events.append(_ev)

    async def generate():
        nonlocal full_reply
        try:
            # 上下文占用:收集最后一次模型调用的 provider usage(2026 context 环)
            from agent.context_meter import build_context_event, extract_prompt_tokens
            from app.config import config as _cfg
            _last_prompt_tokens = None

            # HITL 决定的前置事件先发（reject/abandon 的步骤取消投影）——
            # 必须早于恢复流：老师点完卡第一眼要看到计划步翻「已取消」。
            for _ev in _pre_resume_events:
                yield f"data: {_ev}\n\n"

            # 批准后的执行结果观察（三态收口）：中间件恢复时工具真实执行，
            # on_tool_end 的载荷成功与否决定 finish_gate 落 executed 还是退回 open。
            _hitl_outcome = None

            try:
                async with asyncio.timeout(_TURN_TIMEOUT):
                    async for event in agent.astream_events(
                        Command(resume=_resume_payload),
                        config,
                        version="v2",
                    ):
                        if event.get("event") == "on_chat_model_end":
                            _pt = extract_prompt_tokens(event.get("data", {}).get("output"))
                            if _pt is not None:
                                _last_prompt_tokens = _pt
                        if (_hitl_claim and event.get("event") == "on_tool_end"):
                            _out_msg = event.get("data", {}).get("output")
                            if getattr(_out_msg, "name", None) == _hitl_claim["tool"]:
                                _hitl_outcome = _out_msg
                        for result in adapter.feed(event):
                            if skip_resume_approval:
                                try:
                                    evt = json.loads(result)
                                    _is_old_gate = (
                                        evt.get("type") == "gate_open"
                                        and skip_gate_id
                                        and evt.get("gate_id") == skip_gate_id
                                    )
                                    if _is_old_gate:
                                        continue  # 只跳过被批准门的旧卡
                                except (json.JSONDecodeError, TypeError):
                                    pass
                            try:
                                evt = json.loads(result)
                                if evt.get("type") == "text":
                                    full_reply.append(evt.get("content", ""))
                            except (json.JSONDecodeError, TypeError):
                                pass
                            yield f"data: {result}\n\n"

            except TimeoutError:
                _mark_run_paused_safe(cid)  # ADR-0016：超时即暂停，不自动重跑
                _err = json.dumps(
                    {'type': 'error', 'message': _TIMEOUT_ERROR_MSG, 'recoverable': True},
                    ensure_ascii=False,
                )
                yield f"data: {_err}\n\n"
                yield "data: [DONE]\n\n"
                return
            except asyncio.CancelledError:
                _mark_run_paused_safe(cid)  # 客户端断线：run 立刻标 paused
                raise
            except GraphInterrupt:
                # ADR-0015：interrupt 已退役——迁移间隙残留兜底：如实告知 + 标 paused
                _mark_run_paused_safe(cid)
                payload = {"type": "run_paused", "reason": "interrupted",
                           "message": "上一回合被中断，运行已暂停；重新发送消息即可继续。"}
                yield f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"
                return
            except Exception as _exc:  # noqa: BLE001
                # 兜底：异常不得穿透（穿透 = SSE 半路断掉、计划卡悬在「执行中」、
                # 收尾对账被绕过）。这里补做三件事：步骤落终态、明确报错、正常收帧。
                logger.exception("stream failed cid=%s: %s", cid, _exc)
                try:
                    if guard_state is not None and getattr(adapter, "_plan_steps", None):
                        guard_state.plan_steps = adapter._plan_steps
                except Exception:  # noqa: BLE001
                    pass
                for _ev in _plan_abort_events(guard_state, cid):
                    yield f"data: {_ev}\n\n"
                _err = json.dumps({
                    "type": "error",
                    "message": f"本轮执行出错已中止：{str(_exc)[:200]}",
                    "recoverable": True,
                }, ensure_ascii=False)
                yield f"data: {_err}\n\n"
                yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                return

            # HITL 批准的三态收口（B 的转移表）：claim 说「归我」，这里才说
            # 「做完了没」。成功 → executed；载荷带 error → 退回 open（老师可
            # 重试）；流里根本没看到工具结果 → 留在 executing，交给清算线
            # （T17/in_doubt），这里只留结构化痕迹。
            if _hitl_claim:
                if _hitl_outcome is None:
                    logger.warning(
                        "hitl claim no outcome cid=%s gate=%s tool=%s",
                        cid, _hitl_claim["gate_id"], _hitl_claim["tool"])
                else:
                    _ok_h = True
                    try:
                        _parsed_h = getattr(_hitl_outcome, "content", None)
                        _parsed_h = (json.loads(_parsed_h)
                                     if isinstance(_parsed_h, str) else _parsed_h)
                        _ok_h = not (isinstance(_parsed_h, dict)
                                     and "error" in _parsed_h)
                    except Exception:  # noqa: BLE001
                        _ok_h = True
                    try:
                        from app.services.gate_service import gate_service as _gs_f
                        from app.services.work_unit import new_work_unit as _nwu_f
                        with _nwu_f() as _db_f:
                            _gs_f.finish_gate(_db_f, _hitl_claim["gate_id"], _ok_h)
                    except Exception as _e:  # noqa: BLE001
                        logger.warning("hitl finish failed cid=%s gate=%s: %s",
                                       cid, _hitl_claim["gate_id"], _e)

            # 恢复流本身也可能再撞下一扇 HITL 门（同一轮继续往后走）：同主流
            # 一样探测 + 发卡；图挂着，后面按 next=None 写的收尾逻辑不成立，
            # 收尾对账对 awaiting_confirmation 步本来就会跳过。
            try:
                _st_r = await agent.aget_state(config)
                _susp_r = (getattr(_st_r, "interrupts", ()) or ()) \
                    if getattr(_st_r, "next", None) else ()
            except Exception as _e:  # noqa: BLE001
                logger.warning("resume suspend probe failed cid=%s: %s", cid, _e)
                _susp_r = ()
            for _ev in _suspended_gate_events(cid, _susp_r, guard_state):
                yield f"data: {_ev}\n\n"

            final_state = await agent.aget_state(config)
            state_values = final_state.values if final_state else {}
            # 同上：步骤耗尽兜底语不经流式，直播查状态补发中文
            _step_zh = step_exhausted_reply(state_values)
            if _step_zh:
                yield f"data: {json.dumps({'type': 'text', 'content': _step_zh}, ensure_ascii=False)}\n\n"
            gs = guard_state
            result_text2 = state_values.get("last_result_text")
            if not result_text2 and full_reply:
                result_text2 = "".join(full_reply)
            result_text2 = humanize_step_exhaustion(result_text2)
            # 上下文占用事件(provider usage 锚定 + 三段启发式构成)
            _ctx_event = build_context_event(
                _last_prompt_tokens, state_values.get("messages"), _cfg.CONTEXT_WINDOW,
            )
            for _ev in _plan_tail_events(gs, cid):
                yield f"data: {_ev}\n\n"
            for result in adapter.finalize(
                route=state_values.get("last_route") or (gs.last_route if gs else None),
                component=state_values.get("last_component") or (gs.last_component if gs else None),
                result_text=result_text2,
            ):
                if result == "[DONE]":
                    yield f"data: {json.dumps(_ctx_event, ensure_ascii=False)}\n\n"
                    yield "data: [DONE]\n\n"
                else:
                    yield f"data: {result}\n\n"

        except Exception as e:
            _mark_run_paused_safe(cid)  # ADR-0016：模型/系统报错 → run 如实标暂停
            yield f"data: {json.dumps({'error': str(e)})}\n\n"
            yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

    return _locked_stream(
        conv_lock, generate,
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"})


@router.post("/chat/langgraph/reset")
async def agent_chat_langgraph_reset(
    conversation_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("agent", "delete", allow_self=True)),
):
    """重置对话(v2) — 删除该线程在 chemai_checkpoints.db 的 checkpoint/writes。

    纯 v2 实现:直接清库,不再调用 v1 的 checkpointer。
    会话必须属于调用者（阶段 4.6 批次 E）。
    """
    require_owned(db, conversation_id, current_user)

    # ADR-0015 阶段 3：重置会话顺带关闭 gate 实体（不留幽灵门）。
    # 原先包着 try/except: pass —— 门没关上、会话照清，正是 4a 列表里那条"点不进
    # 去的门"。现在按 fail-loud：关不掉门就让这次重置失败（R7 D8=A）。
    from app.services.gate_service import gate_service
    from app.services.work_unit import work_unit
    with work_unit(db):
        gate_service.close_conversation(db, conversation_id)
    _guard_state_cache.pop(conversation_id, None)
    _THREAD_OVERRIDES.pop(conversation_id, None)

    import sqlite3
    from app.core.config import settings
    db_path = settings.CHECKPOINT_DB_PATH
    if not os.path.exists(db_path):
        return {"success": False, "message": "数据库不存在"}

    conn = sqlite3.connect(db_path)
    try:
        cur = conn.cursor()
        cur.execute("DELETE FROM writes WHERE thread_id = ?", (conversation_id,))
        cur.execute("DELETE FROM checkpoints WHERE thread_id = ?", (conversation_id,))
        conn.commit()
        deleted = cur.rowcount > 0
        cur.close()
        return {"success": True, "message": "对话已重置" if deleted else "对话不存在"}
    except Exception as e:
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════════
# Agent 文件上传
# ═══════════════════════════════════════════════════════════════════

ALLOWED_MIMES = {
    "image/jpeg", "image/png", "image/bmp", "image/webp", "image/gif",
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}
MAX_FILE_SIZE = 10 * 1024 * 1024


@router.post("/upload")
async def agent_upload(
    file: UploadFile = File(...),
    _perm: None = Depends(PermissionChecker("agent", "create")),
):
    """Agent 对话中上传文件 → 预览 → 返回操作建议

    只给员工角色（教师端 `ChatPage.tsx` / `js/agent.js` 是仅有的调用方）。
    学生端将来若要传图，改成 `allow_self=True` 即可 —— 现在是 fail-closed。
    """
    from dotenv import load_dotenv
    load_dotenv()
    mime = file.content_type or ""
    if mime not in ALLOWED_MIMES:
        if not (file.filename or "").lower().endswith(('.doc', '.docx', '.pdf', '.png', '.jpg', '.jpeg')):
            raise HTTPException(400, f"不支持的文件格式: {mime}")

    file_data = await file.read()
    if len(file_data) > MAX_FILE_SIZE:
        raise HTTPException(400, "文件超过 10MB 限制")

    # Save session
    from app.models.database import get_db, UploadSession
    session_id = _uuid.uuid4().hex[:16]
    db = next(get_db())
    try:
        db.add(UploadSession(
            id=session_id,
            file_data=file_data,
            file_name=file.filename or "unknown",
            mime_type=mime,
            status="previewing",
        ))
        db.commit()
    finally:
        db.close()

    # Preview
    from app.services.ocr_service import ocr_service
    import logging as _log
    _log.getLogger(__name__).info(f'agent_upload: mime={mime}, size={len(file_data)}, fn={file.filename}')
    result = await ocr_service.recognize(file_data, mime)

    # Update session
    db = next(get_db())
    try:
        from sqlalchemy import update as sql_update
        db.execute(
            sql_update(UploadSession)
            .where(UploadSession.id == session_id)
            .values(
                status="ready",
                preview_text=result.get("preview_text", ""),
                formula_result=json.dumps(result.get("formula_result", []), ensure_ascii=False),
            )
        )
        db.commit()
    finally:
        db.close()

    preview_text = (result.get("preview_text", "") or "")
    if not preview_text and not result.get("success"):
        err = result.get("error", "unknown")
        logger.warning("agent_upload OCR failed: %s", err)
    # 图片类上传给出带鉴权取回端点（工件面板「附件」tab 的原图预览用）；
    # PDF/Word 没有 inline 预览，不给 url。
    image_url = f"/api/agent/upload/{session_id}/image" if mime.startswith("image/") else None
    return {
        "upload_id": session_id,
        "file_name": file.filename or "unknown",
        "preview_text": preview_text[:500] if preview_text else "",
        "image_url": image_url,
        "error": result.get("error") if not result.get("success") else None,
        "actions": [
            {"id": "import", "label": "导入题库"},
            {"id": "grade", "label": "批改判卷"},
            {"id": "search", "label": "搜题解析"},
        ],
    }


@router.get("/upload/{session_id}/image")
async def agent_upload_image(
    session_id: str,
    _perm: None = Depends(PermissionChecker("agent", "read")),
    db: Session = Depends(get_db),
):
    """取回上传原图（工件面板「附件」tab）。上传件现在没有归属表（任何教师都能
    在对话里引用任何 upload_id，现状契约），所以这里只做员工身份校验，不做
    跨教师收窄——收窄随上传归属化一起做（工件链后续批次）。fail-closed：
    不存在的 id 一律 404，二进制以原 mime 下发。db 走 Depends（测试可覆盖，
    next(get_db()) 会绕过依赖注入直连真实库）。"""
    from fastapi.responses import Response
    row = db.query(UploadSession).filter(UploadSession.id == session_id).first()
    if row is None or not row.file_data:
        raise HTTPException(404, "上传件不存在")
    return Response(content=row.file_data, media_type=row.mime_type or "image/png")
# reload trigger 2026-07-05T02:27:17.669215
