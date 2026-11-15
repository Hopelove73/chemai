"""门状态机 transition 原语测试（架构候选 B · 唯一写点）。

seam：gate_service.transition / transition_bulk / open_gate 复位守卫。
转移表是数据：合法对走条件更新，非法 from 状态返回 False（没转移上），
非法 (from, to) 组合抛 ValueError（程序员错误，要响亮）。

钉住的三个安全不变量：
  ① in_doubt 行不得被同 gate_id 重开复位（结果未裁定，重开=双执行口子，
     审查 I-2：崩溃前那次可能已执行，再批准就跑第二遍）；
  ② executing 行同理（claim 正被某次批准持有，复位=绕过占门）；
  ③ 终态行不再被盲改写（close_gate 旧实现是无条件 update）。
"""
import pytest

from app.models.database import AgentGate
from app.services.gate_service import (
    LEGAL_TRANSITIONS, gate_service, transition,
)
from app.services.work_unit import work_unit


def _row(env, gate_id):
    return env.query(AgentGate).filter(AgentGate.gate_id == gate_id).first()


@pytest.fixture
def gate_row(db_session, unit):
    """一扇 open 的布尔门（经真写入口开，不是手插行）。"""
    with work_unit(db_session):
        gid = gate_service.open_gate(
            db_session, "conv-t", "boolean", "assign_adaptive_practice",
            {"student_id": "s1"}, action="", message="测试门")
    return gid


# ── 转移原语 ───────────────────────────────────────────────────────

def test_transition_legal_path_is_conditional_update(db_session, unit, gate_row):
    with work_unit(db_session):
        ok = transition(db_session, gate_row, "executing",
                        allowed_from=("open",))
    assert ok is True
    assert _row(db_session, gate_row).status == "executing"


def test_transition_wrong_from_state_returns_false(db_session, unit, gate_row):
    with work_unit(db_session):
        ok = transition(db_session, gate_row, "open",
                        allowed_from=("executing", "in_doubt"))  # 行实际是 open
    assert ok is False
    assert _row(db_session, gate_row).status == "open"


def test_transition_illegal_pair_raises(db_session, unit, gate_row):
    with work_unit(db_session):
        with pytest.raises(ValueError, match="非法转移"):
            transition(db_session, gate_row, "open",
                       allowed_from=("open",))          # open→open 不在表里


def test_legal_table_covers_the_documented_lifecycle():
    for pair in (("open", "executing"), ("open", "cancelled"), ("open", "superseded"),
                 ("open", "executed"),          # pilot 批准即收口
                 ("executing", "executed"), ("executing", "open"),
                 ("executing", "in_doubt"), ("executing", "superseded"),
                 ("in_doubt", "open"), ("in_doubt", "executed")):
        assert pair in LEGAL_TRANSITIONS, pair
    # 终态不出现在任何 from 位
    assert not any(f in ("executed", "cancelled", "superseded")
                   for f, _ in LEGAL_TRANSITIONS)


# ── 不变量 ①②：in_doubt / executing 不得被重开复位 ─────────────────

def _force_status(env, gid, status):
    row = _row(env, gid)
    row.status = status
    env.commit()


def test_open_gate_refuses_to_reset_in_doubt_row(db_session, unit, gate_row):
    _force_status(db_session, gate_row, "in_doubt")
    with pytest.raises(ValueError, match="结果未知"):
        with work_unit(db_session):
            gate_service.open_gate(          # 同参数重调 → 同 gate_id 复位路径
                db_session, "conv-t", "boolean", "assign_adaptive_practice",
                {"student_id": "s1"}, action="", message="再来一次")
    assert _row(db_session, gate_row).status == "in_doubt", "行不得被复位"


def test_open_gate_refuses_to_reset_executing_row(db_session, unit, gate_row):
    _force_status(db_session, gate_row, "executing")
    with pytest.raises(ValueError, match="执行中"):
        with work_unit(db_session):
            gate_service.open_gate(
                db_session, "conv-t", "boolean", "assign_adaptive_practice",
                {"student_id": "s1"}, action="", message="并发重开")
    assert _row(db_session, gate_row).status == "executing"


def test_open_gate_still_resets_cancelled_row_for_retry(db_session, unit, gate_row):
    """cancelled/executed/superseded 的复位是合法重试路（A7 探针实测过的形状）。"""
    _force_status(db_session, gate_row, "cancelled")
    with work_unit(db_session):
        gid = gate_service.open_gate(
            db_session, "conv-t", "boolean", "assign_adaptive_practice",
            {"student_id": "s1"}, action="", message="重试")
    assert gid == gate_row
    assert _row(db_session, gate_row).status == "open"


# ── 不变量 ③：终态行不再被盲改写 ───────────────────────────────────

def test_close_gate_on_terminal_row_returns_false(db_session, unit, gate_row):
    _force_status(db_session, gate_row, "executed")
    with work_unit(db_session):
        ok = gate_service.close_gate(db_session, gate_row, "superseded")
    assert ok is False
    assert _row(db_session, gate_row).status == "executed"
