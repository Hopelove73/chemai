"""确认门过期作业（T17 / gate-lifecycle 批次 3）：逾期作废 + 卡死 run 对账。

**为什么非要有这个作业。** 门开着就**全拦**（C1-Q3：不可逆动作一律先要老师点头），
而**没有任何一条路径会替老师放弃** —— 一张没人点过的卡片能把那个会话**永久**锁住。
`T4` 的假门收敛管的是「按判据不算数」的行，管不了「算数但没人处理」的行：
两件事、两套判据。2026-10-17 实测：演示库里攒了 41 扇算数的未决门，
而那个每日作业**从来没挂上**（`expire_gate` / `gate_expiry.py` 压根不存在）。

第二件事（run 对账）是必需的：门被作废之后，那些「等这张卡」的计划步就永远等不到
结果了 —— 界面上表现成永远转圈的计划卡。**收了门不收 run，只解决一半。**

逐条验的是 T17 原文那三个判据：超期归零 / 单扇失败不连累其余 / 缺门的那一步不动。
"""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.jobs.handlers as handlers
from app.models.database import AgentGate, Base, PlanRun, Student
from app.services.gate_service import gate_service


@pytest.fixture
def db(monkeypatch):
    """内存库，并且**两条取库的路都指过来**。

    `work_unit._session` 是延迟绑定 `app.models.database.get_db` 的（它自己的注释
    把这条写成契约）；而 `handlers` 是在模块顶部 `from ... import get_db` 的，
    换前者够不着它 —— 两处都要换，否则读写落在两个库上，测出来的东西没有意义。
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    import app.models.database as _db_mod
    monkeypatch.setattr(_db_mod, "get_db", lambda: iter([session]))
    monkeypatch.setattr(handlers, "get_db", lambda: iter([session]))
    session.add(Student(student_id="s1", name="王梓涵", class_id="c1", status="approved"))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _gate(db, gid, days_ago=0.0, status="open", tool="delete_bank", kwargs=None):
    row = AgentGate(
        gate_id=gid, conversation_id=f"t-{gid}", kind="boolean", tool_name=tool,
        kwargs=kwargs or {}, action="", message="", status=status,
        last_opened_at=datetime.utcnow() - timedelta(days=days_ago))
    db.add(row)
    db.commit()
    return row


def _run(db, rid, conv, step_status):
    row = PlanRun(run_id=rid, conversation_id=conv, goal="g", status="running",
                  steps=[{"step": 1, "skill": "x", "status": step_status}])
    db.add(row)
    db.commit()
    return row


def _steps(db, rid):
    return [s["status"] for s in db.query(PlanRun).filter_by(run_id=rid).one().steps]


def _status(db, rid):
    return db.query(PlanRun).filter_by(run_id=rid).one().status


# ── 过期：逾期作废，未逾期不碰 ────────────────────────────────────────

def test_expires_gates_past_the_threshold(db, caplog):
    """超期的作废，且**每条留一行日志** —— 事后问"这张卡怎么没了"要查得到。"""
    _gate(db, "g-old", days_ago=20)
    with caplog.at_level("INFO"):
        out = handlers.expire_gate(db, days=14)
    assert out["expired"] == ["g-old"] and out["failed"] == []
    assert db.query(AgentGate).filter_by(gate_id="g-old").one().status == "superseded"
    assert any("gate expired" in r.message and "g-old" in str(r.args)
               for r in caplog.records), "作废没留痕"


def test_keeps_gates_inside_the_threshold(db):
    """没超期的一个都不许动 —— 阈值定短了会把老师手上正在处理的卡收掉。"""
    _gate(db, "g-fresh", days_ago=3)
    out = handlers.expire_gate(db, days=14)
    assert out["expired"] == []
    assert db.query(AgentGate).filter_by(gate_id="g-fresh").one().status == "open"


def test_one_failure_does_not_stop_the_rest(db, monkeypatch):
    """单扇失败不连累其余（T17 验收原句）—— 逐扇一个工作单元就是为了这个。"""
    _gate(db, "g-a", days_ago=30)
    _gate(db, "g-b", days_ago=20)
    real = gate_service.close_gate

    def flaky(_db, gate_id, status):
        if gate_id == "g-a":
            raise RuntimeError("库锁了")
        return real(_db, gate_id, status)

    monkeypatch.setattr(gate_service, "close_gate", staticmethod(flaky))
    out = handlers.expire_gate(db, days=14)
    assert out["failed"] == ["g-a"]
    assert out["expired"] == ["g-b"], "一扇炸了，另一扇也要做完"
    assert db.query(AgentGate).filter_by(gate_id="g-b").one().status == "superseded"


def test_expiry_is_idempotent(db):
    """重复跑不重复留痕：收成 superseded 之后不再满足 `status='open'`。"""
    _gate(db, "g-old", days_ago=20)
    assert handlers.expire_gate(db, days=14)["expired"] == ["g-old"]
    assert handlers.expire_gate(db, days=14)["expired"] == []


# ── run 对账：没有活门撑着的步骤收到终态 ─────────────────────────────

def test_settles_a_run_with_no_live_gate(db):
    """门被收走之后，等不到结果的那一步要收掉 —— 否则计划卡永远转圈。"""
    _run(db, "r-1", "t-orphan", "running")
    handlers.reconcile_plan_runs(db)
    assert _steps(db, "r-1") == ["failed"], "卡在执行中断的记 failed"
    assert _status(db, "r-1") == "completed", "全部到终态时 run 收口"


def test_awaiting_step_without_a_gate_is_cancelled(db):
    """等着一张**已经不在了**的卡的那一步记 `cancelled` —— 它从没执行过。"""
    _run(db, "r-2", "t-orphan", "awaiting_confirmation")
    handlers.reconcile_plan_runs(db)
    assert _steps(db, "r-2") == ["cancelled"]


def test_leaves_a_run_that_is_waiting_on_a_live_gate(db):
    """**反向钉子**：会话还有算数的开门时，那一步是真在等老师，一个字都不许改。

    这条要是错了，对账就变成了「把老师还没决定的任务提前判死」。
    """
    _gate(db, "g-live", days_ago=1)
    _run(db, "r-3", "t-g-live", "awaiting_confirmation")
    out = handlers.reconcile_plan_runs(db)
    assert out["skipped_waiting_on_gate"] == 1
    assert _steps(db, "r-3") == ["awaiting_confirmation"]
    assert _status(db, "r-3") == "running"


def test_reconcile_is_idempotent(db):
    _run(db, "r-4", "t-orphan", "pending")
    handlers.reconcile_plan_runs(db)
    before = _steps(db, "r-4")
    handlers.reconcile_plan_runs(db)
    assert _steps(db, "r-4") == before == ["cancelled"]
