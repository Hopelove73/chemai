"""家长端（批 3 P1/P2 / parent-engagement spec）。

频控纯函数、三挂点 dispatch 幂等、liked 端点权限、weekly 等价聚合、
摘要三卡、调度幂等键、家长派任务工具门配对注册。
"""
import sys
import json
import pathlib
from datetime import datetime, timedelta

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth import create_access_token
from app.models.database import (
    Base, Parent, ParentNotification, Question, Student, StudentAnswer,
    StudentParentBinding, get_db,
)
from app.services import notification_service as ns
from app.services import parent_task_service as pts
from app.services.parent_summary_service import weekly_aggregate


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(Parent(parent_id="p-1", name="家长", password_hash="x"))
    session.add(Student(student_id="s-1", name="孩子", class_id="cls-1", status="approved"))
    session.add(StudentParentBinding(binding_id="b-1", student_id="s-1",
                                     parent_id="p-1", status="active",
                                     bind_code="bc-1"))
    session.add(Question(question_id="pq-1", content="题干",
                         knowledge_points=["盐类水解"], difficulty=3))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _parent_auth():
    return {"Authorization": f"Bearer {create_access_token('p-1', 'parent')}"}


# ── 频控纯函数 ──────────────────────────────────────────────────────────

def test_frequency_empty_week(db_session):
    ok, used, _ = pts.can_assign(db_session, "s-1")
    assert ok and used == 0


def test_frequency_counts_sent_drafts_this_week(db_session):
    """频控按「送达」计（用户定案 2026-10-03）：放弃/打回（awaiting）不占额，
    只有真正 sent 的草稿消耗每周 ≤2 的配额；上周/教师草稿不计。"""
    from app.models.database import LearningPlanDraft
    now = datetime.utcnow()
    monday = pts.week_start(now)
    # 本周 2 条还在确认中（放弃/打回形态）→ 不占额
    for i in range(2):
        db_session.add(LearningPlanDraft(
            draft_id=f"pd-{i}", student_id="s-1", version=1,
            status="awaiting_confirmation", content={"plan_title": "x"},
            created_by=f"parent:p-1",
            created_at=monday + timedelta(days=i)))
    # 本周 1 条已送达 → 占额 1
    db_session.add(LearningPlanDraft(
        draft_id="pd-sent", student_id="s-1", version=1,
        status="sent", content={"plan_title": "sent"},
        created_by="parent:p-1",
        created_at=monday + timedelta(days=1)))
    # 上周的送达草稿不计入本周配额
    db_session.add(LearningPlanDraft(
        draft_id="pd-old", student_id="s-1", version=1,
        status="sent", content={"plan_title": "old"},
        created_by="parent:p-1",
        created_at=monday - timedelta(days=3)))
    # 教师创建的草稿不计入家长配额
    db_session.add(LearningPlanDraft(
        draft_id="pd-t", student_id="s-1", version=1,
        status="sent", content={"plan_title": "t"},
        created_by="t-1", created_at=now))
    db_session.commit()
    # now 必须显式传入：can_assign 缺省走上海真实时钟取周窗口，而种子的 monday
    # 锚自 UTC——上海已周一/UTC 仍周日的时段（北京 0~8 点）种子整体落在窗口外。
    ok, used, msg = pts.can_assign(db_session, "s-1", now=now)
    assert ok and used == 1
    # 再送 1 条 = 达上限
    db_session.add(LearningPlanDraft(
        draft_id="pd-sent2", student_id="s-1", version=1,
        status="sent", content={"plan_title": "sent2"},
        created_by="parent:p-1", created_at=now))
    db_session.commit()
    ok2, used2, msg2 = pts.can_assign(db_session, "s-1", now=now)
    assert not ok2 and used2 == 2 and "每周" in msg2


def test_binding_check(db_session):
    assert pts.parent_of_student(db_session, "p-1", "s-1")
    assert not pts.parent_of_student(db_session, "p-1", "s-other")
    assert not pts.parent_of_student(db_session, "p-none", "s-1")


# ── 三挂点 dispatch 幂等 ────────────────────────────────────────────────

def test_notify_parents_idempotent(db_session):
    n1 = notification_service_ns().notify_student_parents(
        db_session, "s-1", ns.NotificationService.FEED_STREAK_MILESTONE,
        "连击 7 天 🔥", None, idempotency_key="feed_streak_s-1_7")
    n2 = notification_service_ns().notify_student_parents(
        db_session, "s-1", ns.NotificationService.FEED_STREAK_MILESTONE,
        "连击 7 天 🔥", None, idempotency_key="feed_streak_s-1_7")
    assert n1 == 1 and n2 == 0  # 重放去重
    rows = db_session.query(ParentNotification).all()
    assert len(rows) == 1 and rows[0].parent_id == "p-1"


def notification_service_ns():
    from app.services.notification_service import notification_service
    return notification_service


# ── weekly 等价聚合 + like 端点 + 家长链路 ─────────────────────────────

def test_weekly_aggregate_equivalent_shape(db_session):
    db_session.add(StudentAnswer(answer_id="wa-1", student_id="s-1",
                                 question_id="pq-1", is_correct=True, confirmed=True))
    db_session.commit()
    agg = weekly_aggregate(db_session, "s-1")
    # WeeklyReportResponse 字段全兼容（等价性口径：键集合与类型不变）
    from app.api.parent import WeeklyReportResponse
    parsed = WeeklyReportResponse(**agg)  # 不抛 = 形状兼容
    assert parsed.practice_count == 1 and parsed.accuracy_rate == 1.0
    assert parsed.streak_days >= 1


def test_summary_endpoint_three_cards(db_session, client):
    resp = client.get("/api/parent/child/s-1/summary", headers=_parent_auth())
    assert resp.status_code == 200
    cards = resp.json()["cards"]
    assert set(cards.keys()) == {"unfinished", "due_soon", "week_activity"}
    assert cards["week_activity"]["streak_days"] >= 0


def test_frequency_endpoints(db_session, client):
    r1 = client.get("/api/parent/summary-frequency", headers=_parent_auth())
    assert r1.json()["frequency"] == "weekly"  # 默认
    r2 = client.put("/api/parent/summary-frequency", headers=_parent_auth(),
                    json={"frequency": "off"})
    assert r2.status_code == 200
    r3 = client.put("/api/parent/summary-frequency", headers=_parent_auth(),
                    json={"frequency": "hourly"})
    assert r3.status_code == 400  # 白名单外拒绝
    assert client.get("/api/parent/summary-frequency", headers=_parent_auth()).json()["frequency"] == "off"


def test_like_endpoint_permission_and_toggle(db_session, client):
    notification_service_ns().notify_student_parents(
        db_session, "s-1", ns.NotificationService.FEED_ARTIFACT_SHARED,
        "孩子学习了《盐类水解》", None, idempotency_key="feed_art_a1_s-1")
    row = db_session.query(ParentNotification).first()
    # 未绑定家长不能点赞
    db_session.add(Parent(parent_id="p-2", name="路人", password_hash="x"))
    db_session.commit()
    stranger = {"Authorization": f"Bearer {create_access_token('p-2', 'parent')}"}
    assert client.put(f"/api/notification/{row.notification_id}/like",
                      headers=stranger).status_code == 403
    # 本人点赞切换
    r1 = client.put(f"/api/notification/{row.notification_id}/like", headers=_parent_auth())
    assert r1.json()["liked"] is True
    r2 = client.put(f"/api/notification/{row.notification_id}/like", headers=_parent_auth())
    assert r2.json()["liked"] is False


def test_summary_requires_binding(db_session, client):
    assert client.get("/api/parent/child/s-1/summary",
                      headers={"Authorization": f"Bearer {create_access_token('p-none', 'parent')}"}
                      ).status_code == 403


# ── 调度幂等 ────────────────────────────────────────────────────────────

def test_scheduler_summary_idempotent(db_session, client):
    from app.scheduler.parent_summary import run_weekly_summary
    r1 = run_weekly_summary()
    r2 = run_weekly_summary()
    assert r1["sent"] >= 1
    assert r2["sent"] == 0  # 同周重跑：幂等键去重，不重发


def test_tool_registry_has_parent_tools():
    from agent.tools import TOOLS, TOOL_META
    names = {fn.__name__ for fn in TOOLS}
    for name in ("parent_learning_plan", "parent_mastery_levels",
                 "parent_barrier_distribution", "parent_class_comparison",
                 "parent_artifact_log", "parent_assign_task", "parent_send_task"):
        assert name in names
        fn = next(f for f in TOOLS if f.__name__ == name)
        assert "parent" in TOOL_META[fn]["personas"]


# ── E2E E14 回归：家长派任务的工作单元 ────────────────────────────────────

def test_parent_assign_task_opens_gate_card(db_session, monkeypatch, request):
    """此前 _unit()（new_work_unit）开的是另一条会话，create_draft 的
    require_unit(db) 身份校验必炸 NoWorkUnit —— 门卡从未弹出过。
    修复后写入口在同一条会话的 work_unit 里执行，返回 approval_needed。"""
    import asyncio

    from agent.tools import parent as parent_tools

    monkeypatch.setattr("app.models.database.get_db", lambda: iter([db_session]))
    monkeypatch.setattr(
        "app.services.plan_draft_service.plan_draft_service.compute_draft",
        lambda db, student_id: {"student_id": student_id, "student_name": "孩子",
                                 "plan_data": {"items": [{"kp": "盐类水解"}]}})

    from agent.tools.context import set_current_identity
    # 身份是 contextvar，跨测试存活：结束时限时清空（同 test_send_confirmation 的写法）
    request.addfinalizer(lambda: set_current_identity(""))
    set_current_identity("p-1", "parent", None)

    out = asyncio.run(parent_tools.parent_assign_task(student_name="孩子",
                                                      focus="盐类水解"))
    data = json.loads(out)
    assert data.get("approval_needed") is True, data
    assert data.get("draft_id")

    # 草稿落库（单元出口提交），状态为待确认
    from app.services.plan_draft_service import STATUS_AWAITING, _Draft
    row = db_session.query(_Draft).filter(
        _Draft.student_id == "s-1", _Draft.status == STATUS_AWAITING).one()
    assert row.created_by == "parent:p-1"
