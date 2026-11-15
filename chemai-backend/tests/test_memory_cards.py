"""批次 B —— FSRS 记忆卡（CONTEXT.md「Memory Card」/ spec 批次 B）。

四组口径：
1. 服务层：建卡幂等、双信号（重做对/错 + 自评三档）喂 FSRS、到期清单按 due 排序。
2. 迁移：ReviewTask（艾宾浩斯天梯）→ 卡初始状态，幂等；契约不变（响应键照旧）。
3. /api/review 内核替换：due/count/submit/sync/history/create 全走记忆卡，
   task_id = mc-<id> 不透明；submit 双信号至少一个。
4. 判卷钩子：save/OCR 批确认对错题自动建卡，答对不建。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth import create_access_token
from app.models.database import Base, MemoryCard, Question, Student, get_db
from app.services import memory_card_service as mcs


@pytest.fixture
def db_session(tmp_path):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(Student(student_id="s-1", name="复习生", class_id="cls-1", status="approved"))
    session.add(Student(student_id="s-2", name="旁座", class_id="cls-1", status="approved"))
    session.add(Question(question_id="q-1", content="盐类水解的本质是什么？",
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


def _auth(user="s-1", role="student"):
    return {"Authorization": f"Bearer {create_access_token(user, role)}"}


# ── 服务层 ──────────────────────────────────────────────────────────────

def test_ensure_card_idempotent(db_session):
    a = mcs.ensure_card(db_session, "s-1", "q-1")
    b = mcs.ensure_card(db_session, "s-1", "q-1")
    assert a.id == b.id
    assert db_session.query(MemoryCard).count() == 1


def test_review_wrong_reschedules_sooner_than_right(db_session):
    """错（Again）比对（Good）下次到期更近——记忆模型的直觉不变式。"""
    wrong = mcs.ensure_card(db_session, "s-1", "q-1")
    mcs.review_card(db_session, wrong.id, is_correct=False)
    due_wrong = mcs.review_card(db_session, wrong.id, is_correct=False).due

    right = mcs.ensure_card(db_session, "s-1", "s-2")  # 借位建卡再改
    right.question_id = "q-1"
    right.student_id = "s-2"
    db_session.commit()
    mcs.review_card(db_session, right.id, is_correct=True)
    due_right = mcs.review_card(db_session, right.id, is_correct=True).due

    assert due_wrong < due_right


def test_self_rating_signal_mapping(db_session):
    """自评三档兜底：forgot→Again（lapses 增加），remembered→Good（stability 增）。"""
    row = mcs.ensure_card(db_session, "s-1", "q-1")
    before_lapses = row.lapses
    mcs.review_card(db_session, row.id, self_rating="forgot")
    assert row.lapses == before_lapses + 1
    assert row.last_signal == "forgot"

    mcs.review_card(db_session, row.id, self_rating="remembered")
    assert row.stability > 0


def test_review_card_missing_id(db_session):
    assert mcs.review_card(db_session, 99999, is_correct=True) is None


# ── 迁移 ────────────────────────────────────────────────────────────────

def test_migrate_review_task_maps_ladder(db_session):
    """ReviewTask（天梯 3 级）→ 卡：稳定度近似 7 天、状态 Review、幂等。"""
    # review_tasks 表的元数据注册依赖 review_task 模块的导入时序（跨文件运行时
    # 可能晚于 fixture 的 create_all）——测试开头补一次幂等建表
    from app.models.review_task import ReviewTask  # noqa: F401  确保模型已注册
    Base.metadata.create_all(db_session.get_bind())
    task = ReviewTask(task_id="review_legacy_1", student_id="s-1", question_id="q-1",
                      review_level=3, status="pending",
                      next_review_at=None, consecutive_errors=2,
                      review_history=[])
    from datetime import datetime, timedelta
    task.next_review_at = datetime.utcnow() + timedelta(days=7)
    db_session.add(task)
    db_session.commit()

    card = mcs.migrate_review_task(db_session, task)
    assert card.stability == 7.0 and card.state == 2 and card.lapses == 2
    assert card.source == "migrated"
    again = mcs.migrate_review_task(db_session, task)
    assert again.id == card.id  # 幂等


# ── /api/review 契约 ────────────────────────────────────────────────────

def test_due_list_contract_shape(db_session, client):
    mcs.ensure_card(db_session, "s-1", "q-1")
    db_session.commit()
    resp = client.get("/api/review/student/s-1/due", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["due_count"] == 1
    task = body["tasks"][0]
    # 契约键全集固化（P2-8）：student 端按这组键消费，多键少键都是破坏
    assert set(task.keys()) == {"task_id", "question_id", "content", "options",
                                "answer", "analysis", "knowledge_points",
                                "difficulty", "review_level", "next_review_at"}
    assert task["task_id"].startswith("mc-")
    assert task["question_id"] == "q-1"


def test_due_list_scoped_to_student(db_session, client):
    mcs.ensure_card(db_session, "s-1", "q-1")
    db_session.commit()
    resp = client.get("/api/review/student/s-2/due", headers=_auth("s-2"))
    assert resp.json()["due_count"] == 0


def test_submit_roundtrip_and_message(db_session, client):
    card = mcs.ensure_card(db_session, "s-1", "q-1")
    db_session.commit()
    resp = client.post("/api/review/submit",
                       json={"task_id": f"mc-{card.id}", "is_correct": True},
                       headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    # FSRS 学习步首刷是分钟级到期（Again≈1min / Good≈10min），文案按实际间隔报
    assert ("分钟" in body["message"] or "今天" in body["message"]
            or "天后再次复习" in body["message"])
    assert body["next_review_at"]


def test_submit_requires_signal(db_session, client):
    card = mcs.ensure_card(db_session, "s-1", "q-1")
    db_session.commit()
    resp = client.post("/api/review/submit",
                       json={"task_id": f"mc-{card.id}"},
                       headers=_auth())
    assert resp.status_code == 400


def test_submit_accepts_self_rating(db_session, client):
    card = mcs.ensure_card(db_session, "s-1", "q-1")
    db_session.commit()
    resp = client.post("/api/review/submit",
                       json={"task_id": f"mc-{card.id}", "self_rating": "vague"},
                       headers=_auth())
    assert resp.status_code == 200


def test_submit_scoped_to_owner(db_session, client):
    card = mcs.ensure_card(db_session, "s-1", "q-1")
    db_session.commit()
    resp = client.post("/api/review/submit",
                       json={"task_id": f"mc-{card.id}", "is_correct": True},
                       headers=_auth("s-2"))
    assert resp.status_code == 403


def test_sync_backfills_from_wrong_answers(db_session, client):
    from app.models.database import StudentAnswer
    from app.models.database import AnswerSource
    db_session.add(StudentAnswer(answer_id="ans-1", student_id="s-1", question_id="q-1",
                                 student_answer="x", is_correct=False,
                                 source=AnswerSource.ONLINE, confirmed=True))
    db_session.commit()
    resp = client.post("/api/review/sync/s-1", headers=_auth())
    assert resp.status_code == 200
    assert db_session.query(MemoryCard).count() == 1


# ── 判卷钩子 ────────────────────────────────────────────────────────────

def test_grading_save_creates_cards_for_wrong_only(db_session, tmp_path, monkeypatch):
    """判卷保存（practice 路径）：错题自动建卡，答对不建（R15）。"""
    from app.services import grading_center
    monkeypatch.setattr("app.services.grading_center._enqueue_diagnosis", lambda *a, **k: "skipped")
    monkeypatch.setattr("app.services.grading_center.notify_graded", lambda *a, **k: None)
    graded = [
        {"question_id": "q-1", "student_answer": "b", "is_correct": False},
    ]
    out = grading_center.save(db_session, graded, "s-1", None, "online")
    assert "error" not in out, out
    cards = db_session.query(MemoryCard).all()
    assert len(cards) == 1 and cards[0].question_id == "q-1" and cards[0].source == "grading"


def test_grading_save_idempotent_on_regrade(db_session, monkeypatch):
    """同题重做再错：不重复建卡（unique student+question）。"""
    from app.services import grading_center
    monkeypatch.setattr("app.services.grading_center._enqueue_diagnosis", lambda *a, **k: "skipped")
    monkeypatch.setattr("app.services.grading_center.notify_graded", lambda *a, **k: None)
    graded = [{"question_id": "q-1", "student_answer": "b", "is_correct": False}]
    grading_center.save(db_session, graded, "s-1", None, "online")
    grading_center.save(db_session, graded, "s-1", None, "online",
                        idempotency_prefix="ans2", upsert=True)
    assert db_session.query(MemoryCard).count() == 1


# ── B 尾巴：计划「今日复习」挂接 + 周报复习进度 ─────────────────────────

def test_plan_compute_attaches_review_today(db_session, monkeypatch):
    """计划草稿挂「今日复习」：到期卡的确定性数据进 plan_data（非 LLM 生成），
    复习数据缺失不阻断计划生成。"""
    from app.services import plan_draft_service as pds
    mcs.ensure_card(db_session, "s-1", "q-1")
    db_session.commit()

    fake_plan = {"plan_title": "期中冲刺计划", "daily_tasks": []}
    monkeypatch.setattr(pds.PlanDraftService, "compute_draft",
                        lambda self, db, student_id, student_name="": {"x": 1})
    # 直接打挂接函数：到期卡 → review_today
    pds._attach_review_today(db_session, fake_plan, "s-1")
    assert fake_plan["review_today"]["due_count"] == 1
    assert fake_plan["review_today"]["items"][0]["question_id"] == "q-1"


def test_plan_attach_survives_card_service_failure(db_session, monkeypatch):
    """复习数据坏了 → 静默跳过（增强项不是依赖项），plan_data 原样。"""
    from app.services import plan_draft_service as pds
    fake_plan = {"plan_title": "计划"}
    monkeypatch.setattr("app.services.memory_card_service.due_cards",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    pds._attach_review_today(db_session, fake_plan, "s-1")
    assert "review_today" not in fake_plan
    assert fake_plan["plan_title"] == "计划"


def test_weekly_report_review_stats_shape(db_session):
    """周报的复习进度是确定性数据：到期张数 + 本周复习张数。"""
    from datetime import datetime, timedelta
    mcs.ensure_card(db_session, "s-1", "q-1")
    db_session.commit()
    card = db_session.query(MemoryCard).first()
    mcs.review_card(db_session, card.id, is_correct=True)  # reviewed this week
    mcs.ensure_card(db_session, "s-1", "q-new")            # due now (new card)
    # q-new 卡的 question 不存在不影响计数（计数只在卡表）

    due = mcs.due_count(db_session, "s-1")
    reviewed = (db_session.query(MemoryCard)
                .filter(MemoryCard.student_id == "s-1",
                        MemoryCard.last_review >= datetime.utcnow() - timedelta(days=7))
                .count())
    assert due >= 1 and reviewed >= 1


# ── 批次 B 审查修复的回归钉 ─────────────────────────────────────────────

def test_judgment_sync_recycles_never_reviewed_card(db_session):
    """主观题判定落定为对 → 从未复习的占位卡直接回收（P1-1 回收路径）。"""
    mcs.ensure_card(db_session, "s-1", "q-1")
    db_session.commit()
    mcs.sync_card_from_judgment(db_session, "s-1", "q-1", is_correct=True)
    assert db_session.query(MemoryCard).count() == 0


def test_judgment_sync_ensures_card_on_wrong(db_session):
    mcs.sync_card_from_judgment(db_session, "s-1", "q-1", is_correct=False)
    assert db_session.query(MemoryCard).count() == 1


def test_migrated_card_first_review_stays_sane(db_session):
    """P1-5 回归：迁移卡（有 last_review）首刷 Good 稳定度温和增长，
    不再出现 7→247 的爆炸。"""
    from app.models.review_task import ReviewTask
    from datetime import datetime, timedelta
    task = ReviewTask(task_id="review_m1", student_id="s-1", question_id="q-1",
                      review_level=3, status="pending",
                      next_review_at=datetime.utcnow() + timedelta(days=7),
                      consecutive_errors=0, review_history=[])
    db_session.add(task)
    db_session.commit()
    card = mcs.migrate_review_task(db_session, task)
    before = card.stability
    mcs.review_card(db_session, card.id, is_correct=True)
    assert card.stability < before * 5  # 温和增长（爆炸版是 35 倍）


def test_migrate_all_marks_rows_and_idempotent(db_session):
    """P1-4：全量迁移把行标 migrated（家长提醒/旧列表的死计数随之消失），
    重跑零新增。"""
    from app.models.review_task import ReviewTask
    from datetime import datetime, timedelta
    db_session.add(ReviewTask(task_id="review_m2", student_id="s-1", question_id="q-1",
                              review_level=1, status="pending",
                              next_review_at=datetime.utcnow() + timedelta(days=1),
                              consecutive_errors=0, review_history=[]))
    db_session.commit()
    n1 = mcs.migrate_all_legacy(db_session)
    n2 = mcs.migrate_all_legacy(db_session)
    assert n1 == 1 and n2 == 0
    task = db_session.query(ReviewTask).first()
    assert task.status == "migrated"


def test_self_rating_unknown_value_rejected(db_session, client):
    """P2-7：未知自评值 400，不静默当 Again 偷扣 lapses。"""
    card = mcs.ensure_card(db_session, "s-1", "q-1")
    db_session.commit()
    lapses = card.lapses
    resp = client.post("/api/review/submit",
                       json={"task_id": f"mc-{card.id}", "self_rating": "easy"},
                       headers=_auth())
    assert resp.status_code == 400
    db_session.refresh(card)
    assert card.lapses == lapses


def test_submit_message_minute_level(db_session, client):
    """P2-9：学习步分钟级到期不谎称"1 天后"。"""
    card = mcs.ensure_card(db_session, "s-1", "q-1")
    db_session.commit()
    resp = client.post("/api/review/submit",
                       json={"task_id": f"mc-{card.id}", "self_rating": "forgot"},
                       headers=_auth())
    assert resp.status_code == 200
    assert "分钟" in resp.json()["message"] or "今天" in resp.json()["message"]


def test_count_matches_due_when_question_missing(db_session, client):
    """P2-10：题目被删后 /count 与 /due 口径一致（都跳过缺题卡）。"""
    mcs.ensure_card(db_session, "s-1", "q-ghost")
    db_session.commit()
    count = client.get("/api/review/student/s-1/count", headers=_auth()).json()["due_count"]
    due = client.get("/api/review/student/s-1/due", headers=_auth()).json()["due_count"]
    assert count == due == 0


def test_migrated_card_repeated_forgot_stays_near_term(db_session):
    """批次 B 审查 P1-5 回归：迁移卡连刷 forgot，due 保持近未来
    （last_review 缺失曾致稳定度爆炸 7→247 天）。"""
    from datetime import datetime, timedelta
    from types import SimpleNamespace
    legacy = SimpleNamespace(
        student_id="s-1", question_id="q-1", review_level=3, status="pending",
        next_review_at=datetime.utcnow() + timedelta(days=7),
        consecutive_errors=0, review_history=[])
    card = mcs.migrate_review_task(db_session, legacy)
    db_session.commit()
    for _ in range(5):
        row = mcs.review_card(db_session, card.id, self_rating="forgot")
        delta_days = (row.due - datetime.utcnow()).total_seconds() / 86400
        assert delta_days < 30, f"连错 5 次后 due 被推到 {delta_days:.0f} 天后"
