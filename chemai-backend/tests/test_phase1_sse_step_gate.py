"""阶段 1 — SSE 接缝回归：approval_needed 不再推进 todolist（issue #18）。

原始缺陷就在 adapter：send 工具返回 approval_needed（无 error）→ success=True →
门步骤被标 completed、下一步标 running（「人工还没确认就往下推」）。
修后：adapter 只做格式化，转移规则来自 plan_run_service.compute_tool_transition。
"""
import json

from agent.langgraph_sse_v2 import LangGraphSSEAdapterV2


def _plan_started(adapter):
    events = adapter.feed({
        "event": "on_tool_end", "name": "plan_task",
        "data": {"output": json.dumps({
            "goal": "给张三做学习计划并通知家长",
            "total_steps": 2,
            "steps": [
                {"step": 1, "skill": "generate_learning_plan", "description": "生成计划"},
                {"step": 2, "skill": "send_learning_plan", "description": "发送计划"},
            ],
        }, ensure_ascii=False)},
    })
    assert any('"plan_summary"' in e for e in events)
    return events


def _plan_progress(events):
    out = []
    for e in events:
        raw = e[len("data: "):].strip() if e.startswith("data: ") else e.strip()
        if raw in ("[DONE]", ""):
            continue
        try:
            evt = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if evt.get("type") == "plan_progress":
            out.append((evt["current_step"], evt["status"]))
    return out


def test_approval_needed_marks_awaiting_never_completes():
    """直接调发送被门拦下：发送步 → awaiting，绝不 completed / 启动下一步。"""
    adapter = LangGraphSSEAdapterV2()
    _plan_started(adapter)
    adapter._plan_steps[0]["status"] = "completed"
    adapter._plan_steps[1]["status"] = "running"
    events = adapter.feed({
        "event": "on_tool_end", "name": "send_learning_plan",
        "data": {"output": json.dumps({
            "approval_needed": True, "draft_id": "d1", "action": "send_student",
            "message": "请确认是否发送",
        }, ensure_ascii=False)},
    })
    progress = _plan_progress(events)
    assert (2, "awaiting_confirmation") in progress, progress
    assert not any(s == "completed" for _, s in progress), "门步骤被标完成——原始 bug"
    assert not any(s == "running" for _, s in progress), "门后步骤被启动——原始 bug"


def test_generate_gate_blocks_send_step_not_next_step():
    """生成即门：门挂**生成步自己**，后续步骤不得 running。

    ⚠️ 2026-10-16 语义变更（gate-lifecycle 1.5）：原断言是「生成步打勾(completed)
    + 门挂发送步」。那套的前提是计划里存在独立的发送步 —— 而计划是 LLM 经 plan_task
    生成的，端到端实测（evals/e2e_long_chain.py）生成的 7 步里**没有** send_learning_plan，
    门于是谁也没挂、run 收不了尾。现在生成步**不进 completed**（草稿还没被老师确认，
    这一步的事没办完），直接进 awaiting_confirmation，等重放成功才打勾。
    """
    adapter = LangGraphSSEAdapterV2()
    _plan_started(adapter)
    adapter._plan_steps[0]["status"] = "running"
    events = adapter.feed({
        "event": "on_tool_end", "name": "generate_learning_plan",
        "data": {"output": json.dumps({
            "message": "已生成", "approval_needed": True, "draft_id": "d1",
            "action": "send_student", "message_gate": "请确认是否发送",
        }, ensure_ascii=False)},
    })
    progress = _plan_progress(events)
    assert (1, "awaiting_confirmation") in progress, progress
    assert not any(s == "completed" for _, s in progress), \
        "生成步不该在门刚开时就打勾——草稿还没被确认"
    assert not any(s == "running" for _, s in progress)


def test_plain_success_completes_and_starts_next():
    adapter = LangGraphSSEAdapterV2()
    _plan_started(adapter)
    # 先人工把第一步置为 running（模拟执行中）
    adapter._plan_steps[0]["status"] = "running"
    events = adapter.feed({
        "event": "on_tool_end", "name": "generate_learning_plan",
        "data": {"output": json.dumps({"message": "已生成", "plan": {}}, ensure_ascii=False)},
    })
    progress = _plan_progress(events)
    assert (1, "completed") in progress
    assert (2, "running") in progress


def test_failure_marks_failed():
    adapter = LangGraphSSEAdapterV2()
    _plan_started(adapter)
    adapter._plan_steps[0]["status"] = "running"
    events = adapter.feed({
        "event": "on_tool_end", "name": "generate_learning_plan",
        "data": {"output": json.dumps({"error": "llm down"}, ensure_ascii=False)},
    })
    assert (1, "failed") in _plan_progress(events)


def test_filesystem_elevation_gate_does_not_touch_todolist():
    """工作区外访问的按次审批（approval_needed 但非计划门）不得污染 todolist
    （曾把无关步骤标成 awaiting——H6）。"""
    adapter = LangGraphSSEAdapterV2()
    _plan_started(adapter)
    adapter._plan_steps[0]["status"] = "running"
    events = adapter.feed({
        "event": "on_tool_end", "name": "read",
        "data": {"output": json.dumps({
            "approval_needed": True, "message": "工作区外访问需要批准",
            "target": "C:/outside.txt",
        }, ensure_ascii=False)},
    })
    kinds = [e for e in events if '"plan_progress"' in e]
    assert kinds == [], "按次审批不属于计划门——todolist 不动"


def test_gate_open_blocked_marks_awaiting_not_failed():
    """门等待期间 LLM 多调的发送工具被拦（gate_open_blocked）：既不标 failed，
    也不标 awaiting —— **不动任何步的状态**。

    ⚠️ 2026-10-16 语义变更（gate-lifecycle 1.7）：原断言是该步入
    awaiting_confirmation（2026-09-20 从「标 failed」改过来的，理由是别给老师看
    红色失败态）。但 awaiting 比 failed 更糟：它给一个**没有任何门挂住**的工具
    凭空造出一个永远解不开的等待 —— 端到端实测里 step5 assign_adaptive_practice
    就这么卡住，run 永远 running。被已开的门拦下，语义是「暂停、等**别的**门」，
    它自己没在等什么。原意图（不显示红色失败态）由「什么都不动」完整保留。
    """
    adapter = LangGraphSSEAdapterV2()
    _plan_started(adapter)
    adapter._plan_steps[0]["status"] = "completed"
    adapter._plan_steps[1]["status"] = "running"
    events = adapter.feed({
        "event": "on_tool_end", "name": "send_learning_plan",
        "data": {"output": json.dumps({
            "error": "有确认请求等待老师处理（请在确认卡上批准/打回/放弃），当前操作已暂缓。"
                     "请勿继续调用工具，用文字说明你正在等待确认。",
            "gate_open_blocked": True,
        }, ensure_ascii=False)},
    })
    progress = _plan_progress(events)
    assert progress == [], f"被已开的门拦下不得产生任何步转移：{progress}"
    assert adapter._plan_steps[1]["status"] == "running", "步骤状态应原样不动"
