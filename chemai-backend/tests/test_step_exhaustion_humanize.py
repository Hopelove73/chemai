"""langgraph 步骤耗尽兜底语人化守门（2026-10-05 教师端动画迭代实录）。

create_react_agent 在剩余步数 <2 且模型还想调工具时，丢弃模型回答、塞一句
英文兜底语进消息历史——经 /history 重放原样上屏。这里钉两件事：
1. humanize_step_exhaustion 只对整句精确替换（模型自己引用这句不误伤）；
2. 历史重放（_history_items_from_messages）出口已接人化。
"""
from agent.channel.conversation import (
    _STEP_EXHAUSTED_EN, humanize_step_exhaustion, step_exhausted_reply,
)
from agent.channel.conversation import _history_items_from_messages


class _Msg:
    def __init__(self, type: str, content: str):
        self.type = type
        self.content = content


def test_exact_canned_sentence_is_swapped():
    assert humanize_step_exhaustion(_STEP_EXHAUSTED_EN).startswith("这一轮的执行步骤预算用完了")
    assert "继续" in humanize_step_exhaustion(_STEP_EXHAUSTED_EN)


def test_surrounding_whitespace_and_none_safe():
    assert humanize_step_exhaustion(f"  {_STEP_EXHAUSTED_EN}\n").startswith("这一轮")
    assert humanize_step_exhaustion("正常回复") == "正常回复"
    assert humanize_step_exhaustion("") == ""
    # 模型复述/引用这句（非整句）不误伤
    quoted = f'刚才系统说了一句 "{_STEP_EXHAUSTED_EN}"，意思是步骤到顶了。'
    assert humanize_step_exhaustion(quoted) == quoted


def test_history_replay_swaps_canned_ai_message():
    items = _history_items_from_messages([
        _Msg("human", "给小猫学化学迭代一版"),
        _Msg("tool", "content='{\"ok\": true}' name='grep' tool_call_id='x'"),
        _Msg("ai", _STEP_EXHAUSTED_EN),
    ])
    assert [it["type"] for it in items] == ["user", "tool", "ai"]
    assert items[-1]["content"].startswith("这一轮的执行步骤预算用完了")


def test_state_reply_detects_last_ai_message_for_live_stream():
    """直播场景：图状态末条是兜底语 → 补发中文；正常回复/无消息 → 空串不补。"""
    canned = _Msg("ai", _STEP_EXHAUSTED_EN)
    assert step_exhausted_reply({"messages": [_Msg("human", "hi"), canned]}).startswith("这一轮")
    assert step_exhausted_reply({"messages": [_Msg("ai", "动画已生成")]}) == ""
    assert step_exhausted_reply({"messages": []}) == ""
    assert step_exhausted_reply({}) == ""
