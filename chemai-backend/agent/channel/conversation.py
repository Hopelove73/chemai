"""Conversation management endpoints — list, history, new, reset.

Split from langgraph_channel.py for better organization.

阶段 4.6 批次 E：全部端点按 `conversation_owners` 表校验归属。
改造前它们只按 thread_id 前缀过滤（`t-`/`s-`/`p-`/`m-`），而 thread_id 是
`{prefix}{毫秒时间戳}` —— 可枚举，所以任何人都能列出/读取/删除任意会话。
"""
import logging
import os
import time
import traceback
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker
from app.middleware.conversation import (
    claim_conversation, filter_owned, require_owned,
)
from app.models.database import get_db

router = APIRouter()
logger = logging.getLogger(__name__)


def _extract_msg_role_content(m) -> tuple[str, str]:
    """Extract (role, content) from a message — handles both dict {'role','content'}
    and LangChain objects with .type/.content attributes."""
    # Dict-style (initial checkpoint or JSON-serialized)
    if isinstance(m, dict):
        r = m.get("role", m.get("type", ""))
        c = str(m.get("content", ""))
        return (r, c)
    # LangChain message objects (checkpoint-deserialized)
    role = str(getattr(m, "type", ""))
    content = str(getattr(m, "content", ""))
    return (role, content)


def _extract_messages(checkpoint: dict) -> list:
    """Extract messages from a checkpoint — handles v2 messages channel and
    __start__ channel (initial state before first agent step)."""
    values = checkpoint.get("channel_values", {}) if isinstance(checkpoint, dict) else {}
    if not isinstance(values, dict):
        return []
    msgs = values.get("messages", []) or []
    # Fallback: initial checkpoint may store messages under __start__
    if not msgs and "__start__" in values:
        start_val = values["__start__"]
        if isinstance(start_val, dict):
            msgs = start_val.get("messages", []) or []
    return msgs


def _parse_tool_content(raw: str):
    """解析 ToolMessage 内容 JSON；失败返回 None。"""
    try:
        import json as _json
        return _json.loads(raw)
    except (ValueError, TypeError):
        return None


# 内部注入消息前缀（2026-08 批准重放修复）：langgraph_channel 的续接提示
# （"（系统提示：你刚才请求老师批准…"）以 role=user 写入 checkpoint，历史接口
# 必须过滤，否则前端会把整段重放结果渲染成"用户发送端"的巨文。
# 用完整句式而非宽泛的"（系统提示："，避免误伤用户以该句式开头的真实消息。
_INTERNAL_NOTE_PREFIX = "（系统提示：你刚才请求老师批准"

# langgraph create_react_agent 在剩余步数 <2 且模型还想调工具时，会丢弃模型
# 回答、塞一句英文兜底语当最终消息（langgraph.prebuilt.chat_agent_executor）。
# 这句会经「收尾文本/非流式提取/历史重放」原样流到老师面前——所有出口统一
# 在此换成中文人话（进度在 checkpoint 里，说「继续」即以新预算接着跑）。
_STEP_EXHAUSTED_EN = "Sorry, need more steps to process this request."
_STEP_EXHAUSTED_ZH = ("这一轮的执行步骤预算用完了，我先停在这里。"
                      "已完成的进度不会丢——回复「继续」，我就接着做。")


def humanize_step_exhaustion(text: str) -> str:
    """langgraph 步骤耗尽英文兜底语 → 中文人话（整句精确匹配才替换）。"""
    if (text or "").strip() == _STEP_EXHAUSTED_EN:
        return _STEP_EXHAUSTED_ZH
    return text or ""


def step_exhausted_reply(state_values: dict) -> str:
    """图状态末条 AI 消息若是步骤耗尽兜底语 → 返回中文人话（直播场景用）。

    那句英文是图节点伪造的 AIMessage，不经模型流式，`astream_events` 的文本
    事件里永远不会出现——直播只能在流结束后查 checkpoint 状态补发。"""
    msgs = (state_values or {}).get("messages") or []
    if msgs:
        content = getattr(msgs[-1], "content", "")
        if isinstance(content, str) and content.strip() == _STEP_EXHAUSTED_EN:
            return _STEP_EXHAUSTED_ZH
    return ""


def _history_items_from_messages(msgs: list) -> list:
    """把 checkpoint 消息通道映射为前端可渲染的历史条目（2026-08 记录恢复）：
    - human/user → {type: user}（内部注入的系统提示消息跳过，不渲染成用户消息）
    - ai（有文本）→ {type: ai}（空内容跳过，避免空气泡重现）
    - tool → {type: tool, name, status, data}——工具卡与结果（图表/文件内容）
      在重开对话后完整还原，不再只剩文本。"""
    items = []
    for m in msgs:
        role, content = _extract_msg_role_content(m)
        if role in ("human", "user"):
            if content.lstrip().startswith(_INTERNAL_NOTE_PREFIX):
                # 内部注入（批准重放续接提示等）：只给模型看，不渲染成用户消息
                continue
            items.append({"type": "user", "content": content})
        elif role in ("ai", "assistant", "AIMessageChunk"):
            if content.strip():
                items.append({"type": "ai", "content": humanize_step_exhaustion(content)})
        elif role == "tool":
            name = str(getattr(m, "name", ""))
            if name == "request_approval":
                # 审批流程瞬态工具：结果不进历史（审批卡已传达语义），
                # 避免裸 JSON 卡出现在重开的对话里
                continue
            data = _parse_tool_content(content)
            _is_error = isinstance(data, dict) and ("error" in data or "requires_approval_blocked" in data)
            status = "error" if _is_error else "done"
            items.append({
                "type": "tool",
                "name": name,
                "status": status,
                "data": data if isinstance(data, dict) else None,
                "raw": content[:2000] if data is None else None,
            })
    return items


@router.get("/chat/conversations")
async def list_conversations(
    prefix: str = "t-",
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("agent", "read", allow_self=True)),
):
    """列出**自己的**对话（按 persona 前缀过滤: t-教师 s-学生 p-家长, 支持逗号分隔如 t-,c）

    前缀只用来缩小范围，**不是**授权依据 —— 传 `prefix=t-` 也只会看到自己登记过的会话。
    """
    import sqlite3
    from app.core.config import settings
    db_path = settings.CHECKPOINT_DB_PATH
    if not os.path.exists(db_path):
        return {"conversations": [], "total": 0}

    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    prefixes = [p.strip() for p in prefix.split(",") if p.strip()]
    clauses = " OR ".join(["thread_id LIKE ?" for _ in prefixes])
    params = [f"{p}%" for p in prefixes]
    cursor.execute(f"""
        SELECT DISTINCT thread_id FROM checkpoints
        WHERE {clauses}
        ORDER BY thread_id DESC LIMIT 200
    """, params)
    threads = [row[0] for row in cursor.fetchall()]
    conn.close()

    # 只保留调用者自己登记过的会话（列表是「我的」，不走老会话前缀兜底）
    threads = filter_owned(db, threads, current_user)

    if not threads:
        return {"conversations": [], "total": 0}

    conversations = []
    from agent.langgraph_agent_v2 import _get_v2_checkpointer
    cp = await _get_v2_checkpointer()

    for tid in threads:
        try:
            ckpt = await cp.aget_tuple({"configurable": {"thread_id": tid}})
            if not ckpt:
                continue
            checkpoint = (
                ckpt.checkpoint if hasattr(ckpt, 'checkpoint')
                else ckpt[0] if isinstance(ckpt, tuple) else ckpt
            )
            if not isinstance(checkpoint, dict):
                continue
            msgs = _extract_messages(checkpoint)
            if not msgs:
                continue
            title = ""
            preview = ""
            msg_count = 0
            for m in msgs:
                role, content = _extract_msg_role_content(m)
                if role in ("human", "user"):
                    if not title:
                        title = content[:50]
                        preview = content[:100]
                    msg_count += 1
                elif role == "ai":
                    msg_count += 1
            last_at = str(ckpt.metadata.get("timestamp", "")) if hasattr(ckpt, 'metadata') and ckpt.metadata else ""
            conversations.append({
                "thread_id": tid, "title": title or "新对话", "preview": preview,
                "last_at": last_at, "message_count": msg_count,
            })
        except Exception:
            continue

    return {"conversations": conversations, "total": len(conversations)}


@router.get("/chat/history/{thread_id}")
async def get_conversation_history(
    thread_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("agent", "read", allow_self=True)),
):
    """获取指定对话的完整消息历史（必须是自己登记的会话）"""
    require_owned(db, thread_id, current_user)

    from agent.langgraph_agent_v2 import _get_v2_checkpointer
    cp = await _get_v2_checkpointer()

    try:
        ckpt = await cp.aget_tuple({"configurable": {"thread_id": thread_id}})
        if not ckpt:
            return _history_payload(db, thread_id, [])
        checkpoint = ckpt.checkpoint if hasattr(ckpt, 'checkpoint') else ckpt[0] if isinstance(ckpt, tuple) else ckpt
        if not isinstance(checkpoint, dict):
            return _history_payload(db, thread_id, [])
        messages = _history_items_from_messages(_extract_messages(checkpoint))
        return _history_payload(db, thread_id, messages)
    except Exception as e:
        logger.error(f"get_conversation_history({thread_id}): {e}\n{traceback.format_exc()}")
        return _history_payload(db, thread_id, [], error=str(e))


def _history_payload(db, thread_id: str, messages: list, error: str = "") -> dict:
    """历史响应的唯一组装点：`context_window` 必须由后端下发，前端不许自己猜（ADR-0023 Q14）。

    窗口是进程配置（`CONTEXT_WINDOW`，改环境变量就变），前端那份 128k 常量在配置改后会
    安静地显示一个错的百分比 —— 而"上下文已用 40%"和"已用 78%"在圆环上看着几乎一样。

    出错那一支不再查库：检查点读挂常常正是 DB 也抖的时候，此时再压一轮 run/门查询只会把
    "带 error 的 200"变成 500。
    """
    from app.config import config as _cfg
    _p = {} if error else _active_run_payload(db, thread_id)
    out = {"messages": messages, "thread_id": thread_id,
           "run": _p.get("run"), "gate": _p.get("gate"),
           # 终态卡的数据段。这一层是**白名单拷贝**：漏写一个键，上面算得再对
           # 也不会出现在响应里（本键第一版就是这么丢的，真机复测才发现——
           # 单测直接调 closed_gates、前端桩喂假 payload，两边都绕过了这里）。
           "closed_gates": _p.get("closed_gates") or [],
           "context_window": _cfg.CONTEXT_WINDOW}
    if error:
        out["error"] = error
    return out


def _active_run_payload(db, thread_id: str):
    """该会话的 Plan Run（活跃优先）——前端刷新后据此重建 todolist 卡。
    顺带带上该会话待确认的草稿门（刷新后确认卡不丢）。
    门优先从 run 步骤的 gate_draft_id（DB）解析——服务重启后内存 guard 缓存
    已失，纯 DB 依据仍能重建门卡；内存缓存仅作 DB 之外的即时补充。"""
    run = None
    gate = None
    try:
        from app.services.plan_run_service import plan_run_service
        run = plan_run_service.get_active_run(db, thread_id)
    except Exception:
        run = None

    from app.services.plan_draft_service import plan_draft_service
    from app.services.parent_report_draft_service import parent_report_draft_service

    def _gate_payload(draft: dict, artifact: str = "learning_plan") -> dict:
        if artifact == "parent_report":
            action, label = "send_parent", "学习报告"
        else:
            action, label = "send_student", "学习计划"
        return {"draft_id": draft["draft_id"], "student_id": draft["student_id"],
                "version": draft["version"], "action": action,
                "message": f"{label}（第 {draft['version']} 版）等待确认"}

    def _fetch_draft_any(did: str):
        """按 id 取草稿（计划/报告两类工件都试），返回 (payload, artifact)。"""
        d = plan_draft_service.get_draft(db, did)
        if d:
            return d, "learning_plan"
        d = parent_report_draft_service.get_draft(db, did)
        return (d, "parent_report") if d else (None, "")

    # ① DB 依据 A：通用 gate 实体（ADR-0015 阶段 2：draft/boolean 同一 seam；
    #    草稿门读取时自愈校验草稿状态，重启后门卡照常重建）
    try:
        from app.services.gate_service import gate_service as _gs
        entity = _gs.get_open_gate(db, thread_id)
        if entity:
            gate = {
                "draft_id": entity.get("draft_id", ""),
                "student_id": (entity.get("kwargs") or {}).get("student_id", ""),
                "version": entity.get("version") or 0,
                "action": entity.get("action") or "",
                "message": entity.get("message") or "请确认操作",
                "gate_id": entity.get("gate_id", ""),
                "kind": entity.get("kind", "draft"),
            }
    except Exception:
        gate = None

    # ② DB 依据 B：活跃 run 门步骤上的 gate_draft_id（todolist 内的门）
    if gate is None and run:
        for s in (run.get("steps") or []):
            did = (s or {}).get("gate_draft_id") or ""
            if not did or s.get("status") not in ("awaiting_confirmation", "revising"):
                continue
            d, artifact = _fetch_draft_any(did)
            if d and d.get("status") == "awaiting_confirmation":
                gate = _gate_payload(d, artifact)
                break

    # ③ 即时补充：内存 guard 的待批调用（DB 门记录尚未写入的窗口）
    if gate is None:
        try:
            from agent.channel.langgraph_channel import (
                _gate_artifact, _gate_draft_snapshot,
            )
            from app.services.decision_intake import is_plan_gate as _gate_is_plan_gate
            # 问实体（gate-lifecycle 2.4 收尾）：待批那件事记在门那行上，
            # 不在进程内存里 —— 重启后刷新页面也要能看到草稿门卡。
            from agent.channel.langgraph_channel import _pending_from_entity
            pending = _pending_from_entity(thread_id)
            if pending and _gate_is_plan_gate(thread_id, pending):
                _svc, _component, label = _gate_artifact(pending.get("name", ""))
                did = (pending.get("kwargs", {}) or {}).get("draft_id") or ""
                if did:
                    d = _gate_draft_snapshot(did)
                    artifact = "parent_report" if label == "学习报告" else "learning_plan"
                else:
                    sid = (pending.get("kwargs", {}) or {}).get("student_id", "")
                    d = _svc.get_active_draft(db, sid) if sid else None
                    artifact = "parent_report" if label == "学习报告" else "learning_plan"
                if d and d.get("status") == "awaiting_confirmation":
                    gate = _gate_payload(d, artifact)
        except Exception:
            pass

    # ④ 同源校正（gate-lifecycle 1.8）：`gate` 到这里已是三个来源问完的**最终**答案。
    #    若仍没有门、却有步骤写着「等待确认」，那条步骤就是在等一件**永远不会到来**
    #    的事：计划卡永远停在那儿，而待确认侧（读 agent_gates）根本不认它 —— 两个
    #    真源就此漂开，老师看到的是「没有任何东西在等我确认，但计划卡不动」（断点⑥）。
    #
    #    只改**下发的载荷**，不回写库：收敛存量是批次 3 对账作业的活。
    #    只动 `awaiting_confirmation` —— `revising`（打回后修订中）本来就不需要门撑着。
    #    改成 `pending` 而不是 `completed`：没被确认就是不等于办完了，宁可少声称。
    if run and gate is None:
        run = dict(run, steps=[
            dict(s, status="pending")
            if s.get("status") == "awaiting_confirmation" else s
            for s in (run.get("steps") or [])
        ])
    # 已结束的 run 只恢复显示快照，不参与上面的活跃门校正或执行恢复。
    # run 的终态统一是 completed；失败/放弃保留在 steps 的 failed/cancelled 中。
    if run is None:
        try:
            from app.models.database import PlanRun
            finished = (db.query(PlanRun)
                        .filter(PlanRun.conversation_id == thread_id,
                                PlanRun.status == "completed")
                        .order_by(PlanRun.id.desc()).first())
            if finished:
                run = {"run_id": finished.run_id, "goal": finished.goal,
                       "steps": finished.steps, "status": finished.status}
        except Exception:
            pass
    # 终态卡（已批/已弃）：同一部史刷新后要能看见之前那几扇门怎么了。
    # 数据取账本（谁批的、批的哪一版只有它答得出来），门行补展示字段。
    closed_gates = []
    try:
        from app.services.gate_service import gate_service as _gs_closed
        closed_gates = _gs_closed.closed_gates(db, thread_id)
    except Exception:
        closed_gates = []
    return {"run": run, "gate": gate, "closed_gates": closed_gates}


@router.post("/chat/new")
async def new_conversation(
    prefix: str = "m-",
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("agent", "create", allow_self=True)),
):
    """创建新对话，返回新的 thread_id（默认 m- 学生端前缀；教师端可传 t-、家长端 p-）

    **这是归属的唯一登记点** —— 别的端点只接受已属于调用者的 thread_id，
    刻意不做「无主就认领」（thread_id 是可枚举的时间戳，那样等于抢注）。
    """
    thread_id = f"{prefix}{int(time.time() * 1000)}"
    claim_conversation(db, thread_id, current_user)
    return {"thread_id": thread_id}


@router.delete("/chat/conversations/{thread_id}")
async def delete_conversation(
    thread_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("agent", "delete", allow_self=True)),
):
    """强制删除对话 — 从 checkpoint 数据库彻底清除

    改造前任何登录用户都能删任意会话（直接对 checkpoint 库 DELETE）。
    """
    require_owned(db, thread_id, current_user)

    # ADR-0015 阶段 3：删除会话顺带关闭 gate 实体（不留幽灵门）。
    # 这里原来包着 `try/except Exception: pass` —— 门没关上、会话照删，正好造成
    # "会话没了、门还开着"那条僵尸行，而 4a 列表与统计都读得到它。收编进工作单元
    # 之后按 fail-loud 处理：关不掉门就让整个删除失败（R7 D8=A）。
    from app.services.gate_service import gate_service
    from app.services.work_unit import work_unit
    with work_unit(db):
        gate_service.close_conversation(db, thread_id)

    import sqlite3
    from app.core.config import settings
    db_path = settings.CHECKPOINT_DB_PATH
    if not os.path.exists(db_path):
        return {"success": False, "message": "数据库不存在"}

    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        # Database has tables: checkpoints, writes
        cursor.execute("DELETE FROM writes WHERE thread_id = ?", (thread_id,))
        cursor.execute("DELETE FROM checkpoints WHERE thread_id = ?", (thread_id,))
        conn.commit()
        deleted = cursor.rowcount > 0
        cursor.close()
        return {"success": True, "message": "已删除" if deleted else "未找到该对话"}
    except Exception as e:
        return {"success": False, "message": str(e)}
    finally:
        conn.close()
