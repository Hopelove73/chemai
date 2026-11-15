"""复习连击（student-daily-loop S1 / REQ-RCS-002）。

纯函数内核表驱动四分支 + 边界（周界、上限、幂等）+ API 接线
（GET /api/review/streak/{sid} 惰性结算、submit 清卡钩子、POST repair）。
"""
import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth import create_access_token
from app.models.database import Base, MemoryCard, Question, Student, get_db
from app.services import memory_card_service as mcs
from app.services import streak_service as ss


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(Student(student_id="s-1", name="复习生", class_id="cls-1", status="approved"))
    session.add(Question(question_id="q-1", content="盐类水解的本质是什么？",
                         knowledge_points=["盐类水解"], difficulty=3))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session):
    # 仓库标准模式：不起 lifespan——启动事件里的 APScheduler 在前一个用例
    # 关闭的事件循环上 start() 会炸（RuntimeError: Event loop is closed）
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _auth(user="s-1", role="student"):
    return {"Authorization": f"Bearer {create_access_token(user, role)}"}


def _mk_card(db, student_id="s-1", qid="q-1", due_offset_minutes=0):
    card = mcs.ensure_card(db, student_id, qid)
    from datetime import datetime, timedelta
    card.due = datetime.utcnow() + timedelta(minutes=due_offset_minutes)
    db.commit()
    return card


# ── 纯函数内核：表驱动四分支 ────────────────────────────────────────────

D0 = date(2026, 10, 1)


def _state(current=0, best=0, freeze=0, last=None, freeze_week=None, repair_week=None):
    return ss.StreakState(current=current, best=best, freeze_count=freeze,
                          last_active_date=last, freeze_week=freeze_week, repair_week=repair_week)


def test_clear_day_increments_and_awards_freeze_first_time_in_week():
    state, event = ss.register_cleared_day(_state(), D0)
    assert (state.current, state.best, state.freeze_count) == (1, 1, 1)
    assert event.freeze_awarded == 1
    assert state.freeze_week == ss.week_key(D0)


def test_clear_day_same_day_idempotent():
    state, _ = ss.register_cleared_day(_state(), D0)
    state2, event = ss.register_cleared_day(state, D0)
    assert state2.current == state.current
    assert event.freeze_awarded == 0


def test_clear_day_freeze_cap_two_per_week_quota():
    # 同一周第二次清卡不再发冻结；上限 2
    state = _state(freeze=1, freeze_week=ss.week_key(D0), last=D0 - timedelta(days=1))
    state, _ = ss.register_cleared_day(state, D0)
    assert state.freeze_count == 1  # 本周已发过，不再发


def test_gap_with_freeze_consumes_and_survives():
    # 昨天（相对漏天）前清的，冻 1 张，漏 1 天 → 吃 1 张、连击不断
    state = _state(current=5, best=5, freeze=1, last=D0 - timedelta(days=2))
    state, event = ss.consume_gap(state, D0)
    assert not event.broke
    assert state.current == 5
    assert state.freeze_count == 0


def test_gap_without_freeze_breaks():
    state = _state(current=5, best=9, freeze=0, last=D0 - timedelta(days=4))
    state, event = ss.consume_gap(state, D0)
    assert event.broke
    assert state.current == 0
    assert state.best == 9  # best 保留
    assert state.last_active_date is None


def test_gap_within_one_day_is_alive():
    state = _state(current=3, best=3, freeze=0, last=D0 - timedelta(days=1))
    state, event = ss.consume_gap(state, D0)
    assert not event.broke
    assert state.current == 3


def test_repair_weekly_once_and_gap_bound():
    # 漏 2 天内可修
    state = _state(current=4, best=4, freeze=0, last=D0 - timedelta(days=3))
    state, event, msg = ss.apply_repair(state, D0)
    assert event.repaired
    assert state.last_active_date == D0 - timedelta(days=1)
    # 同周第二次修复被拒
    state2, event2, msg2 = ss.apply_repair(state, D0 + timedelta(days=1))
    assert not event2.repaired
    assert "已用过" in msg2


def test_repair_rejects_long_gap():
    state = _state(current=4, best=4, freeze=0, last=D0 - timedelta(days=10))
    state, event, msg = ss.apply_repair(state, D0)
    assert not event.repaired
    assert "超出" in msg


def test_freeze_week_and_repair_week_are_independent_quotas():
    # 周内清过卡（领过冻结）仍可修复——两个配额互不挤占
    state = _state(current=2, best=2, freeze=2, last=D0 - timedelta(days=3),
                   freeze_week=ss.week_key(D0))
    state, event, msg = ss.apply_repair(state, D0)
    assert event.repaired


# ── API 接线 ────────────────────────────────────────────────────────────

def test_streak_view_lazy_settlement(db_session, client):
    c = client
    from app.models.database import StudentStreak
    db_session.add(StudentStreak(
        student_id="s-1", current=5, best=5, freeze_count=0,
        last_active_date=ss.local_today() - timedelta(days=5)))
    db_session.commit()
    resp = c.get("/api/review/streak/s-1", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["current"] == 0  # 漏 5 天无冻结 → 断
    assert body["best"] == 5
    assert body["repair_available"] is True


def test_submit_zero_due_registers_clear(db_session, client):
    c = client
    _mk_card(db_session)  # 唯一张到期卡
    resp = c.post("/api/review/submit", headers=_auth(),
                  json={"task_id": "mc-1", "is_correct": True})
    assert resp.status_code == 200
    body = resp.json()
    assert body["streak"]["current"] == 1
    assert body["streak"]["freeze_awarded"] == 1
