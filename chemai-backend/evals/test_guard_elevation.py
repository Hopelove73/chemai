"""按次审批与门登记（工作区外访问，DSH danger-full-access 模式）。

契约在 ADR-0015 阶段 2 改过一次，本文件跟着改：
- 原先允许「本轮已批准」（`GuardState.approved`）时直接放行重跑——一次批准会解锁
  同轮后续一切按次审批，故退役。现在工具返回 approval_needed 一律登记待重放 +
  门实体，**重放由 channel 在老师批准后经 `pending_approval["fn"]` 裸函数完成**，
  不经过被拦截的那层包装。
- 门开着期间，同一个 guard 状态上的任何后续工具调用都被拒（C1-Q3 全拦）。
- 不再在工具内 `interrupt()`：langgraph 1.x 的 astream_events 会静默吞掉工具内
  interrupt，既没有审批卡，又会在带旧中断状态的线程上永久挂死流。

门实体会落库，本文件的 GuardState 一律用桩替掉 `register_gate_entity`——这里测的是
guard 的判定与登记，不是 gate 表。
"""
import asyncio
import json

from agent.guard import GuardState, _make_guarded_tool


def _fake_tool_results(calls: list):
    """Tool that returns approval_needed on the first call, success on later
    elevated calls."""

    async def fake(target: str = "", **kwargs):
        """Fake file tool: approval_needed first, success on elevated replay.

        `target` 写成显式参数：`**kwargs` 单独一个的话，langchain 生成出来的参数
        模式是空的，调用方传的字段会被静默滤掉（`kwargs` 恒为 `{}`），
        就验不了"登记的是原样那次调用"。
        """
        calls.append({"target": target, **kwargs})
        if len(calls) == 1:
            return json.dumps({"approval_needed": True,
                               "message": "工作区外访问需要批准: /x",
                               "target": "/x"}, ensure_ascii=False)
        return json.dumps({"ok": True, "elevated": True}, ensure_ascii=False)

    return fake


def _state() -> GuardState:
    """带桩的 GuardState：本文件不碰 gate 表。

    2.4 收尾：`gate_is_open()` 现在会真的查库，所以这里一并桩掉——本文件测的是
    guard 的**判定**，不是 gate 表（表侧在 `tests/` 里）。桩是**有状态的**：
    「登记过一扇门 ⇒ 门开着」，否则 C1-Q3 会把第一次调用也拦掉。
    """
    gs = GuardState()
    gs.conversation_id = "eval-elevation"
    gs._registered = []
    gs.register_gate_entity = lambda **kw: (gs._registered.append(kw), True)[1]
    gs.gate_is_open = lambda: bool(gs._registered)
    return gs


def _run(guarded, **kwargs):
    async def go():
        # StructuredTool 经 ainvoke 调用（参数以 dict 传入）
        return await guarded.ainvoke(kwargs)

    return asyncio.run(go())


def test_first_call_opens_gate_and_pends_replay():
    """首次调用：弹门 + 把这条调用登记为待重放，工具真身只跑一次。"""
    calls = []
    gs = _state()
    guarded = _make_guarded_tool(_fake_tool_results(calls), gs, requires_approval=False)
    data = json.loads(_run(guarded))
    assert data.get("approval_needed") is True
    assert data.get("awaiting_gate") is True
    assert data.get("gate_id", "").startswith("g-tool-fake-"), data
    assert len(calls) == 1, "弹门不得放行执行——副作用只能在批准后发生"
    # 2.4 收尾：断言"登记了什么"（内存那份 pending 已删，载荷真源是门实体）
    assert gs._registered and gs._registered[-1]["tool_name"] == "fake"


def test_replay_target_is_the_original_call_through_the_bare_function():
    """批准后的重放打的是**原样那次调用**、且走裸函数（不再过包装，不会再弹门）。

    2.4 收尾：重放目标改从门实体构造（`_rebuild_pending_from_gate` →
    `_TOOL_BY_NAME[tool_name](**kwargs)`）。这个 eval 把登记桩掉了，所以这里断言
    **登记进去的那份载荷就是原调用**，并直接用它跑一次裸函数。
    """
    calls = []
    gs = _state()
    raw = _fake_tool_results(calls)
    guarded = _make_guarded_tool(raw, gs, requires_approval=False)
    _run(guarded, **{"target": "/x"})

    entry = gs._registered[-1]
    assert entry["tool_name"] == "fake"
    assert entry["kwargs"] == {"target": "/x"}, "登记的必须原样是那次调用"

    result = json.loads(asyncio.run(raw(**entry["kwargs"])))
    assert result.get("ok") is True and result.get("elevated") is True
    assert len(calls) == 2, "重放恰好一次"


def test_gate_open_blocks_every_later_call():
    """门开着期间重试同一调用：判定是「等确认」，不是「重复调用」也不是放行。

    旧实现让「本轮已批准」直接重跑（老师一次批准 = 同轮后续全部解锁），已由
    ADR-0015 阶段 2 退役；本条钉住退役后的形状。"""
    calls = []
    gs = _state()
    guarded = _make_guarded_tool(_fake_tool_results(calls), gs, requires_approval=False)
    assert json.loads(_run(guarded)).get("approval_needed") is True
    second = json.loads(_run(guarded))
    assert second.get("gate_open_blocked") is True, second
    assert "重复调用" not in json.dumps(second, ensure_ascii=False)
    assert len(calls) == 1, "被暂缓的调用不许有第二次副作用"


def test_sticky_approved_flag_no_longer_opens_the_gate():
    """`approved` 在 v2 已无读取方（只有退役中的 request_approval 用过它）。

    留着这个字段是兼容旧调用点；本条断言它不再参与放行判定。"""
    calls = []
    gs = _state()
    gs.approved = True
    guarded = _make_guarded_tool(_fake_tool_results(calls), gs, requires_approval=False)
    data = json.loads(_run(guarded))
    assert data.get("approval_needed") is True
    assert len(calls) == 1
