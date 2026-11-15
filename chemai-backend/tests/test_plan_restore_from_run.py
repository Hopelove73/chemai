"""计划态从持久化 PlanRun 恢复（缺陷 #3 / 安全门加固 Commit ②E）。

`_restore_plan_state` 过去只读进程内的 `guard_state.plan_steps`。服务重启后
guard_state 是全新的（`plan_steps` 为空），而 PlanRun 行（status=running/paused）
在 DB 里存活——ADR-0013/0016 承诺的「重启可重建」本应覆盖 todolist。旧实现漏了
这一环：重启后恢复的流里 `_plan_steps` 空，后续工具调用无法推进计划卡，任务永远
停在「执行中」。

本测试钉死：只要 DB 有活跃 run，恢复必须以 DB 为真源，与内存 guard_state 无关；
DB 无活跃 run 时才回退内存；已 completed 的 run 不得复活。
"""
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import Base
import app.models.database as db_mod
from app.services.plan_run_service import plan_run_service
from agent.guard import GuardState
from agent.langgraph_sse_v2 import LangGraphSSEAdapterV2
from agent.channel.langgraph_channel import _restore_plan_state


def _patch_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def _fake_get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(db_mod, "get_db", _fake_get_db)
    return Session


_STEPS = [
    {"step": 1, "skill": "generate_questions", "description": "出题", "status": "completed"},
    {"step": 2, "skill": "assign_adaptive_practice", "description": "布置", "status": "running"},
]


def test_restore_uses_persisted_plan_run_when_guard_state_lost(monkeypatch):
    """重启模拟：DB 有活跃 run，内存 guard_state 为空 → 必须从 DB 恢复计划步骤。"""
    Session = _patch_db(monkeypatch)
    db = Session()
    try:
        plan_run_service.start_run(db, "conv-r1", "出5题并布置", _STEPS)
        # start_run 以 pending 起步，按 DB 真源推进到当前态
        plan_run_service.mark_step(db, "conv-r1", 1, "completed")
        plan_run_service.mark_step(db, "conv-r1", 2, "running")
    finally:
        db.close()

    adapter = LangGraphSSEAdapterV2(conversation_id="conv-r1")
    guard = GuardState()  # 全新进程：plan_steps 丢失
    assert getattr(guard, "plan_steps", None) in (None, [])

    _restore_plan_state(adapter, guard)

    assert adapter._plan_active is True, "DB 有活跃 run 时计划必须恢复为活跃"
    assert [s["step"] for s in adapter._plan_steps] == [1, 2]
    assert adapter._plan_steps[0]["status"] == "completed"
    assert adapter._plan_steps[1]["status"] == "running"


def test_falls_back_to_guard_state_when_no_db_run(monkeypatch):
    """DB 无活跃 run 时回退内存 guard_state（保持既有行为）。"""
    _patch_db(monkeypatch)
    adapter = LangGraphSSEAdapterV2(conversation_id="conv-nope")
    guard = GuardState()
    guard.plan_steps = [dict(s) for s in _STEPS]

    _restore_plan_state(adapter, guard)

    assert adapter._plan_active is True
    assert [s["step"] for s in adapter._plan_steps] == [1, 2]


def test_completed_run_is_not_resurrected(monkeypatch):
    """已完成的 run 不得复活为新流的活跃计划（否则旧任务幽灵般继续推进）。"""
    Session = _patch_db(monkeypatch)
    db = Session()
    try:
        plan_run_service.start_run(db, "conv-done", "全部完成", _STEPS)
        plan_run_service.mark_step(db, "conv-done", 1, "completed")
        plan_run_service.mark_step(db, "conv-done", 2, "completed")  # 末步 → run completed
    finally:
        db.close()

    adapter = LangGraphSSEAdapterV2(conversation_id="conv-done")
    guard = GuardState()

    _restore_plan_state(adapter, guard)

    assert adapter._plan_active is False
    assert adapter._plan_steps == []


@pytest.mark.parametrize("step_status", ["completed", "failed", "cancelled"])
def test_history_keeps_finished_plan_steps_after_refresh(monkeypatch, step_status):
    """结束的计划只恢复显示快照，完成/失败/放弃的步骤都不得在刷新后丢失。"""
    from agent.channel.conversation import _history_payload

    Session = _patch_db(monkeypatch)
    db = Session()
    try:
        started = plan_run_service.start_run(db, "conv-history-done", "完成的任务", _STEPS[:1])
        plan_run_service.mark_step(db, "conv-history-done", 1, step_status)

        payload = _history_payload(db, "conv-history-done", [])

        assert payload["run"] is not None, "历史响应应保留结束计划的步骤状态"
        assert payload["run"]["run_id"] == started["run_id"]
        assert payload["run"]["status"] == "completed"
        assert payload["run"]["steps"][0]["status"] == step_status
        assert payload["gate"] is None
        assert plan_run_service.get_active_run(db, "conv-history-done") is None
    finally:
        db.close()


def test_history_uses_latest_finished_run_in_its_conversation(monkeypatch):
    from agent.channel.conversation import _active_run_payload

    Session = _patch_db(monkeypatch)
    db = Session()
    try:
        plan_run_service.start_run(db, "conv-history-latest", "旧任务", _STEPS[:1])
        plan_run_service.mark_step(db, "conv-history-latest", 1, "completed")
        latest = plan_run_service.start_run(db, "conv-history-latest", "新任务", _STEPS[:1])
        plan_run_service.mark_step(db, "conv-history-latest", 1, "completed")
        plan_run_service.start_run(db, "conv-other", "其他会话", _STEPS[:1])
        plan_run_service.mark_step(db, "conv-other", 1, "completed")

        payload = _active_run_payload(db, "conv-history-latest")

        assert payload["run"] is not None
        assert payload["run"]["run_id"] == latest["run_id"]
        assert payload["run"]["goal"] == "新任务"
    finally:
        db.close()


@pytest.mark.parametrize("paused", [False, True])
def test_history_prefers_active_run_over_finished_snapshot(monkeypatch, paused):
    from agent.channel.conversation import _active_run_payload

    Session = _patch_db(monkeypatch)
    db = Session()
    try:
        plan_run_service.start_run(db, "conv-history-active", "旧任务", _STEPS[:1])
        plan_run_service.mark_step(db, "conv-history-active", 1, "completed")
        active = plan_run_service.start_run(db, "conv-history-active", "当前任务", _STEPS)
        if paused:
            plan_run_service.mark_run_paused(db, "conv-history-active")

        payload = _active_run_payload(db, "conv-history-active")

        assert payload["run"]["run_id"] == active["run_id"]
        assert payload["run"]["status"] == ("paused" if paused else "running")
        assert payload["run"]["steps"] == _STEPS
    finally:
        db.close()


def test_history_does_not_restore_superseded_plan(monkeypatch):
    from agent.channel.conversation import _active_run_payload
    from app.models.database import PlanRun

    Session = _patch_db(monkeypatch)
    db = Session()
    try:
        plan_run_service.start_run(db, "conv-history-superseded", "已作废任务", _STEPS)
        db.query(PlanRun).filter_by(conversation_id="conv-history-superseded").update(
            {"status": "superseded"}, synchronize_session=False)
        db.commit()

        assert _active_run_payload(db, "conv-history-superseded")["run"] is None
    finally:
        db.close()


def test_restore_feeds_the_approve_path_emitters(monkeypatch):
    """重启后**批准**这一回合必须仍能补发 plan_progress（缺陷 #3 的真实症状）。

    /resume 路径没有 adapter→guard 的回写（那条回写只在 /stream 的生成循环里），而批准
    重放的三个发射器读的是 `guard_state.plan_steps`。
    所以恢复必须以 guard_state 为落点之一，否则老师屏幕上的计划卡冻住，
    尽管 DB 已推进。本测试直接调用发射器，不复现 adapter 字段。"""
    from agent.channel.langgraph_channel import _plan_completion_events

    Session = _patch_db(monkeypatch)
    db = Session()
    try:
        plan_run_service.start_run(db, "conv-r2", "出5题并布置", _STEPS)
        plan_run_service.mark_step(db, "conv-r2", 1, "completed")
        plan_run_service.mark_step(db, "conv-r2", 2, "running")
    finally:
        db.close()

    adapter = LangGraphSSEAdapterV2(conversation_id="conv-r2")
    guard = GuardState()  # 全新进程

    _restore_plan_state(adapter, guard)

    events = _plan_completion_events(guard, "assign_adaptive_practice", True)
    assert events, "恢复后的计划必须能让批准重放路径发出 plan_progress"
    payloads = [json.loads(e) for e in events]
    assert payloads[0] == {"type": "plan_progress", "current_step": 2, "status": "completed"}
