"""工具卡中文映射守门（2026-10-05 教师端 invoke_teaching_skill 裸英文名上屏实录）。

前端有两处渲染工具名的面：教师端 ToolCard.tsx 的 TOOL_META（共享映射表）、
移动端（学生/家长）ChatPanel.tsx 的工具胶囊（经 toolLabel 走同一张表）。
后端新增工具而映射漏补时，两处都回退裸英文名——这里钉成绊线：每个可被
agent 绑定的工具（TOOL_META 注册表 + TOOLS 之外无条件绑定的浏览器五件套，
见 langgraph_agent_v2），映射表必须有同名条目；映射表也不许有名单之外的
多余条目。
"""
import re
from pathlib import Path

from agent.browser_tools import (
    browse_click, browse_input, browse_navigate, browse_read, browse_screenshot,
)
from agent.tools import TOOL_META

FRONTEND_DIR = (Path(__file__).resolve().parents[1] / "frontend"
                / "src" / "components" / "chat")
FRONTEND_TOOLCARD = FRONTEND_DIR / "ToolCard.tsx"
FRONTEND_CHATPANEL = (Path(__file__).resolve().parents[1] / "frontend"
                      / "src" / "components" / "mobile" / "ChatPanel.tsx")

# 注册表已退役、但老 checkpoint 重放历史时仍可能出现其工具卡的名字——
# 前端保留映射是故意的；除此之外映射表不许有注册面外的条目。
_REPLAY_ONLY = {"request_approval", "import_exam_paper"}


def _bindable_tool_names() -> set[str]:
    browser = {browse_navigate.__name__, browse_read.__name__, browse_click.__name__,
               browse_input.__name__, browse_screenshot.__name__}
    return {fn.__name__ for fn in TOOL_META} | browser


def _frontend_mapped_names() -> set[str]:
    text = FRONTEND_TOOLCARD.read_text(encoding="utf-8")
    return set(re.findall(r"^ {2}([a-z_0-9]+): \{ label:", text, re.M))


def test_every_bindable_tool_has_chinese_label():
    mapped = _frontend_mapped_names()
    missing = sorted(_bindable_tool_names() - mapped)
    assert not missing, f"前端 ToolCard 缺中文映射（卡片会裸英文名）: {missing}"


def test_no_unaccounted_frontend_labels():
    extra = sorted(_frontend_mapped_names() - _bindable_tool_names() - _REPLAY_ONLY)
    assert not extra, f"前端映射表出现注册面外且非重放兜底的条目: {extra}"


def test_mobile_chatpanel_uses_shared_labels():
    """移动端工具胶囊必须经 toolLabel 走共享映射表，不许裸渲 item.card.tool。"""
    text = FRONTEND_CHATPANEL.read_text(encoding="utf-8")
    assert "toolLabel(item.card.tool)" in text, \
        "ChatPanel 工具胶囊退回裸英文名了（2026-10-05 实录缺陷）"
    assert "{item.card.tool}" not in text
