"""实体读写失败必须留痕，不得静默（eng-review 2026-09-24 · 发现 T2）。

本仓库有一条写在 ADR-0015/0016 里的纪律：**门实体、计划实体的失败不许吞**。
理由很具体——重启后能不能重建确认卡、计划卡对不对，全靠这些实体；一次没人看见
的失败就是「老师屏幕上凭空少一张卡」。此前 repo 里 `caplog` 出现 0 次，也就是
说这条纪律只靠 code review 守着：把 `logger.warning` 改回 `except: pass`，
没有任何测试会红。

本文件把纪律变成断言，覆盖五条最关键的降级路径。每条都同时断言两件事：
① 有留痕（日志里能查到 cid 与原因）；② 降级行为不变（回退内存 / 照常发卡）。

"""
import json
import logging

import pytest

from agent.guard import GuardState
from agent.langgraph_sse_v2 import LangGraphSSEAdapterV2


@pytest.fixture
def broken_db(monkeypatch):
    """让 `next(get_db())` 一取就抛——模拟 DB 不可用（连接失败/锁死/表缺失）。"""
    import app.models.database as db_mod

    def _boom():
        raise RuntimeError("db down")
        yield  # noqa: B018 —— 使本函数成为生成器，与 get_db 签名同形

    monkeypatch.setattr(db_mod, "get_db", _boom)


_PLAN_EVENT = {
    "event": "on_tool_end", "name": "plan_task",
    "data": {"output": json.dumps({
        "goal": "出5题并布置", "total_steps": 1,
        "steps": [{"step": 1, "skill": "generate_questions", "description": "出题"}],
    }, ensure_ascii=False)},
}


def test_plan_restore_failure_is_logged_and_falls_back(broken_db, caplog):
    """DB 读不到活跃 run → 留痕 + 回退内存 guard_state（行为不变）。"""
    from agent.channel.langgraph_channel import _restore_plan_state

    caplog.set_level(logging.WARNING)
    adapter = LangGraphSSEAdapterV2(conversation_id="conv-boom")
    guard = GuardState()
    guard.plan_steps = [{"step": 1, "skill": "generate_questions", "status": "running"}]

    _restore_plan_state(adapter, guard)

    assert any("plan run restore failed" in r.message and "conv-boom" in r.getMessage()
               for r in caplog.records), \
        "计划态读库失败必须留痕，否则「重启可重建」失效无人知晓"
    assert adapter._plan_active is True, "留痕之后仍按旧路径回退内存"


def test_mark_paused_failure_is_logged(broken_db, caplog):
    """断线时 run 标 paused 失败 → 留痕（ADR-0016 §3 的契约不能无声落空）。"""
    from agent.channel.langgraph_channel import _mark_run_paused_safe

    caplog.set_level(logging.WARNING)
    _mark_run_paused_safe("conv-pause")   # 不得抛出

    assert any("mark run paused failed" in r.getMessage() and "conv-pause" in r.getMessage()
               for r in caplog.records), \
        "无活跃 run 时 service 侧是无异常空转，能到这里的一定是真故障——必须可见"


def test_gate_kind_lookup_failure_is_logged_and_falls_back(broken_db, caplog):
    """门分类读库失败 → 留痕 + 回退工具名集合（分类不许静默判错）。"""
    from app.services.decision_intake import is_plan_gate as _gate_is_plan_gate

    caplog.set_level(logging.WARNING)
    # `_gate_is_plan_gate` 收的是一份**待批描述**（2.4 收尾后由门实体拼出，
    # `_pending_from_entity`）；这里直接给一份，测的是读库失败时的回退。
    pending = {"name": "send_learning_plan", "fn": None, "kwargs": {}}

    assert _gate_is_plan_gate("conv-kind", pending) is True

    msg = [r.getMessage() for r in caplog.records]
    assert any("open gate kind lookup failed" in m and "conv-kind" in m for m in msg), \
        f"kind 读库失败要留痕（当前日志：{msg}）"


def test_plan_run_start_failure_still_emits_card(broken_db, caplog):
    """run 落库失败 → 留痕，但计划卡照常发出（本轮体验不因持久化失败而崩）。"""
    caplog.set_level(logging.WARNING)
    adapter = LangGraphSSEAdapterV2(conversation_id="conv-start")

    events = adapter.feed(_PLAN_EVENT)

    assert any(e.startswith('{"type": "plan_summary"') for e in events), \
        "落库失败不得连带吞掉计划卡"
    assert any("plan run start failed" in r.getMessage() and "conv-start" in r.getMessage()
               for r in caplog.records), \
        "run 未落库 = ADR-0013 承诺的重启重建失效，必须留痕"


def test_plan_step_persist_failure_is_logged_not_swallowed(broken_db, caplog):
    """批准重放后把步转移落 run 失败 → 留痕，且不得把故障冒泡给 SSE 流。

    内存 plan_steps 已推进、run 行却停在旧步骤，重启后计划卡会凭空退回上一步，
    而用户那边只看到"卡住了"。

    **2026-10-17 改写了靶子（而不是删掉）**：这条原先打的是
    `langgraph_channel._persist_send_completion` —— 一个"重放之后再落一次库"的补漏。
    2.8 让 `_plan_completion_events` 自己走 `project_transitions` 落库之后，
    那个函数成了**双写**，而且用的是已被废掉的旧规则（不传 `step_no` ⇒ 按工具名找步，
    而批次 1 的定论是"只认门上的 `plan_step`"）。删掉它之后，
    **`project_transitions` 成了唯一的落库点** —— 所以这条保证必须钉在它身上：
    它原先正是整段 `except Exception: pass`，一个字都不留。"""
    from app.services.plan_run_service import project_transitions

    caplog.set_level(logging.WARNING)
    project_transitions("conv-persist", [(3, "completed")])

    msg = [r.getMessage() for r in caplog.records]
    assert any("plan step persist failed" in m and "conv-persist" in m for m in msg), \
        f"run 未落库必须留痕（当前日志：{msg}）"
