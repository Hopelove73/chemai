"""A1 —— SSE `artifact` 事件：tool_result 只带引用，全文走 /static 回链。

先例是 exam_images（adapter 从工具结果 JSON 抽字段合成事件）。工件必须走同一条
路：tool_result 载荷有 8000 字符截断，动画 HTML 全文一旦内联必被截成非法 JSON，
所以工具结果的 `artifact` 键在 adapter 被抽成独立事件、LLM 只见干净 text。
"""
import json

from agent.langgraph_sse_v2 import LangGraphSSEAdapterV2


def _feed_artifact_tool(adapter, artifact: dict, text: str = "动画已生成，请在右侧预览。"):
    return adapter.feed({
        "event": "on_tool_end",
        "name": "invoke_teaching_skill",
        "data": {"output": json.dumps(
            {"text": text, "artifact": artifact}, ensure_ascii=False)},
    })


def test_artifact_event_emitted_and_llm_sees_clean_text():
    adapter = LangGraphSSEAdapterV2()
    events = _feed_artifact_tool(adapter, {
        "artifact_id": "art_abc", "kind": "animation",
        "title": "盐类水解教学动画", "version_seq": 2,
        "url": "/static/artifacts/2026-09-30/art_abc_v2.html",
    })

    types = [json.loads(e)["type"] for e in events]
    assert types == ["tool_result", "artifact"]

    tool_result = json.loads(events[0])
    assert tool_result["success"] is True
    assert "artifact" not in tool_result["result"]      # 引用不进 tool_result
    assert "动画已生成" in tool_result["result"]        # text 保留给 LLM/历史

    evt = json.loads(events[1])
    assert evt["artifact_id"] == "art_abc"
    assert evt["kind"] == "animation"
    assert evt["title"] == "盐类水解教学动画"
    assert evt["version_seq"] == 2
    assert evt["url"] == "/static/artifacts/2026-09-30/art_abc_v2.html"


def test_artifact_event_survives_when_text_would_be_truncated():
    """text 超长被 8000 截断是 tool_result 的事，artifact 引用事件必须完整。"""
    adapter = LangGraphSSEAdapterV2()
    long_text = "长" * 20000
    events = _feed_artifact_tool(adapter, {
        "artifact_id": "art_big", "kind": "slides",
        "title": "讲评课件", "version_seq": 1,
        "url": "/static/artifacts/2026-09-30/art_big_v1.html",
    }, text=long_text)

    evt = json.loads(events[-1])
    assert evt["type"] == "artifact" and evt["artifact_id"] == "art_big"
    tool_result = json.loads(events[0])
    assert len(tool_result["result"]) < 20000           # 载荷确实走了截断


def test_tool_result_without_artifact_key_unchanged():
    """没有 artifact 键的工具结果不受影响（exam_images 路径原样保留）。"""
    adapter = LangGraphSSEAdapterV2()
    events = adapter.feed({
        "event": "on_tool_end",
        "name": "search_exam_bank",
        "data": {"output": json.dumps({"text": "普通结果", "images": []}, ensure_ascii=False)},
    })
    types = [json.loads(e)["type"] for e in events]
    assert "artifact" not in types


def test_malformed_artifact_value_does_not_crash_adapter():
    """artifact 键为字符串/None 等畸形值：不炸 adapter（外层防御）。"""
    adapter = LangGraphSSEAdapterV2()
    events = adapter.feed({
        "event": "on_tool_end", "name": "invoke_teaching_skill",
        "data": {"output": json.dumps({"text": "ok", "artifact": "oops"},
                                      ensure_ascii=False)},
    })
    assert events  # 有事件输出（error 卡或工具卡），不能吞掉整条流
