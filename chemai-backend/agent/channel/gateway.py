"""Gateway 分类共享工具 — ChatRequest、对话上下文提取、意图分类+工具收窄。

原位于 fastapi_sse.py（pydantic-ai 端点）。pydantic-ai 移除后抽到此处，
供 LangGraph channel 与 classifier eval 共用。
"""
import asyncio
import logging
from typing import Optional

from pydantic import BaseModel

logger = logging.getLogger(__name__)

# 会话状态（进程内存，重启丢失）——原 fastapi_sse.py 中的共享状态
_conversations: dict[str, dict] = {}


class ChatRequest(BaseModel):
    # 缺省 = "没指定"，交给 `_resolve_persona` 按 token 角色取 allowed[0]，**不要**
    # 在这里给一个具体 persona。原默认 "tutor" 的坑（2026-10-17）：它对 teacher 角色
    # 也合法（`_PERSONAS_BY_ROLE` 里 teacher 允许 ("teacher","tutor")），于是
    # `requested or allowed[0]` 那行的兜底**永远轮不到**，任何漏发 persona 的调用方
    # 都被静默降级成家教 —— teacher.yaml 才有的工具当场消失，长链路批准后断在第 5 步，
    # 模型只能如实汇报「这个技能在环境里不存在」。
    persona: Optional[str] = None
    message: str
    student_id: Optional[str] = None
    provider: str = "deepseek"
    history: Optional[list[dict]] = None
    conversation_id: Optional[str] = None
    # 文件访问档位（前端开关下发）: off=关闭 | workspace=仅工作区(默认)
    file_access: str = "workspace"
    # 老师自定义工作区路径（空 = 默认 ~/ChemAI-Workspace）
    workspace: str = ""
    # 确认门三值决定（ADR-0013）：{action: approve|reject|abandon, feedback?, draft_id?}。
    # 缺省 None 时按旧协议处理（message 字符串向后兼容映射）。
    decision: Optional[dict] = None


def _extract_conversation_context(messages: list[dict] | None, max_turns: int = 2) -> str:
    """Extract the last N turns from message list as text context.

    Each message is a dict with keys: role ("user"/"assistant") and content (str).
    """
    if not messages:
        return ""

    lines = []
    recent = messages[-(max_turns * 2):]  # each turn = user + assistant
    for msg in recent:
        if not isinstance(msg, dict):
            continue
        role = "用户" if msg.get("role") == "user" else "助手"
        content = msg.get("content", "")
        if content:
            lines.append(f"{role}: {str(content)[:200]}")

    return "\n".join(lines) if lines else ""


async def _classify_and_narrow(
    message: str,
    persona: str,
    message_history: list | None,
) -> tuple[list[str] | None, object | None]:
    """Run the Gateway LLM classifier. Returns (tool_names, intent_result_or_None).

    Returns (None, None) if classification fails — caller should use all tools.
    """
    import os
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

    try:
        from agent.agents import load_persona
        from agent.gateway import IntentClassifier
        from agent.provider.deepseek import get_classifier_provider

        persona_config = load_persona(persona)
        available_skills = persona_config.get("available_skills", [])
        conversation_context = _extract_conversation_context(message_history)

        provider = get_classifier_provider()
        classifier = IntentClassifier(provider)

        result = await asyncio.wait_for(
            classifier.classify(
                message,
                available_skills=available_skills,
                conversation_context=conversation_context,
            ),
            timeout=5.0,
        )
        return result.tools, result

    except asyncio.TimeoutError:
        logger.warning("IntentClassifier: timed out after 5s, falling back to all tools")
        return None, None
    except Exception:
        logger.warning("IntentClassifier: classification failed", exc_info=True)
        return None, None
