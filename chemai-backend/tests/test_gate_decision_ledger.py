"""T2（后端）：门决定账本的写入方 —— 验收 §7.5「可回放」的唯一数据来源。

`agent_gates` 是**当前态**表：批准之后再开门会覆盖门面，`decided_at` 也会被下一轮
改写。`agent_gate_decisions` 表早就建好了（database.py），但从建表到这次改动之间
**全仓没有任何写入方** —— 于是「谁批的、批的哪一版」根本无处可查。

这里钉三件事：
  1. 单元内写入 → 行确实落库，字段对得上；
  2. 单元外调用 → 当场抛 NoWorkUnit（漏包要的是响，不是静默丢写）；
  3. 整单回滚 → 账本行一起没（账本与它描述的动作同一次提交）。
"""
import pytest

from app.models.database import AgentGateDecision
from app.services import work_unit as wu
from app.services.gate_service import gate_service


def _rows(session):
    return session.query(AgentGateDecision).all()


def test_writes_a_row_inside_the_unit(db_session, unit):
    with unit(db_session):
        gate_service.record_decision_log(
            db_session, "g-draft-d1", "approved",
            conversation_id="t-1", actor_user_id="u-teacher",
            tool_name="send_learning_plan", kind="draft",
            detail='{"draft_id": "d1", "version": 2}')
    rows = _rows(db_session)
    assert len(rows) == 1
    row = rows[0]
    assert row.gate_id == "g-draft-d1"
    assert row.decision == "approved"
    assert row.actor_user_id == "u-teacher"
    assert row.conversation_id == "t-1"
    assert row.tool_name == "send_learning_plan"
    assert row.kind == "draft"
    assert '"version": 2' in row.detail
    assert row.created_at is not None


def test_outside_a_unit_raises(db_session):
    with pytest.raises(wu.NoWorkUnit):
        gate_service.record_decision_log(db_session, "g-1", "approved")


def test_missing_identity_is_null_not_blank(db_session, unit):
    """`conversation_id` / `actor_user_id` 允许为空：`send_plan_to_parent` 那条 REST
    请求只带 student_id，没有会话上下文，模型里明写「不得静默丢」。空串入库会把
    「没有」伪装成「有空串」，统计那边就分不清该不该排除它。"""
    with unit(db_session):
        gate_service.record_decision_log(db_session, "g-2", "rejected")
    row = _rows(db_session)[0]
    assert row.conversation_id is None
    assert row.actor_user_id is None
    assert row.tool_name == ""
    assert row.kind == ""
    assert row.detail == ""


def test_rolled_back_unit_leaves_no_row(db_session):
    """动作没落，账也不能落 —— 否则下一次重试会拿着一条假凭据。"""
    with pytest.raises(RuntimeError):
        with wu.work_unit(db_session):
            gate_service.record_decision_log(db_session, "g-3", "approved")
            raise RuntimeError("动作失败了")
    assert _rows(db_session) == []


def test_closed_gates_reads_the_ledger(db_session, unit):
    """终态卡的数据来源：账本出「谁批的/什么决定」，门行补展示字段。"""
    with unit(db_session):
        gid = gate_service.open_gate(
            db_session, "t-1", kind="draft", tool_name="send_learning_plan",
            kwargs={"student_id": "s1", "draft_id": "d1"},
            draft_id="d1", version=2, action="send_student", message="计划待发")
        gate_service.record_decision_log(
            db_session, gid, "approved", conversation_id="t-1",
            actor_user_id="u1", tool_name="send_learning_plan", kind="draft")
        gate_service.close_gate(db_session, gid, "executed")
    rows = gate_service.closed_gates(db_session, "t-1")
    assert len(rows) == 1
    row = rows[0]
    assert row["gate_id"] == gid
    assert row["decision"] == "approved"
    assert row["actor_user_id"] == "u1"
    assert row["action"] == "send_student"
    assert row["message"] == "计划待发"
    assert row["kind"] == "draft"
    assert row["decided_at"], "没有时间就排不出时间线"


def test_closed_gates_excludes_other_conversations(db_session, unit):
    with unit(db_session):
        gate_service.record_decision_log(
            db_session, "g-a", "approved", conversation_id="t-1",
            actor_user_id="u1", tool_name="x", kind="draft")
        gate_service.record_decision_log(
            db_session, "g-b", "approved", conversation_id="t-2",
            actor_user_id="u1", tool_name="x", kind="draft")
    assert [r["gate_id"] for r in gate_service.closed_gates(db_session, "t-1")] == ["g-a"]


def test_closed_gates_blank_conversation_returns_nothing(db_session):
    """空会话 id 不许退化成全库扫描（那会把别人的门画到你屏幕上）。"""
    assert gate_service.closed_gates(db_session, "") == []

def test_history_payload_actually_surfaces_closed_gates(db_session, unit):
    """`/chat/history` 的组装点是**白名单拷贝**，漏一个键就静默丢。

    这条测试钉的就是那个组装点：`closed_gates` 在 `_active_run_payload` 里算对了，
    但 `_history_payload` 不挑它，前端就永远收不到终态卡（本键第一版正是如此，
    单测与前端桩都绕过了这一层，真机复测才发现）。
    """
    from agent.channel.conversation import _history_payload
    with unit(db_session):
        gate_service.record_decision_log(
            db_session, "g-77", "approved", conversation_id="t-77",
            actor_user_id="u-teacher", tool_name="send_learning_plan", kind="draft")
    payload = _history_payload(db_session, "t-77", [])
    assert "closed_gates" in payload, "组装点漏了这个键，前端永远收不到终态卡"
    assert [g["gate_id"] for g in payload["closed_gates"]] == ["g-77"]
