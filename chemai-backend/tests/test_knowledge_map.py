"""知识地图（S6 / knowledge-edges + knowledge-map-views spec）。

状态机三态表驱动、环检测/拒环、API 学生视图（鉴权/冷启动/绑定只呈现已分享版本）、
教师边管理启停。
"""
import sys
import json
import pathlib
from datetime import datetime

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth import create_access_token
from app.models.database import (
    Base, KPArtifactBinding, KnowledgeEdge, Question, Student,
    StudentNotification, get_db,
)
from app.services import knowledge_map_service as kms


# ── 状态机（纯函数）─────────────────────────────────────────────────────

@pytest.mark.parametrize("mastery,prereqs,expected", [
    (0.95, {}, "mastered"),
    (0.5, {}, "ready"),          # 无前置：就绪判定
    (0.95, {}, "mastered"),
])
def test_kp_state_no_prereqs(mastery, prereqs, expected):
    assert kms.kp_state("k", {"k": mastery}, prereqs) == expected


def test_kp_state_locked_by_prereq():
    assert kms.kp_state("b", {"a": 0.3, "b": 0.5}, {"b": ["a"]}) == "locked"


def test_kp_state_mastered_not_relocked():
    # 自身已精通不因前置回锁（避免误导性回退）
    assert kms.kp_state("b", {"a": 0.3, "b": 0.95}, {"b": ["a"]}) == "mastered"


def test_kp_state_no_samples_counts_zero():
    assert kms.kp_state("b", {"a": 0.9}, {"b": ["a"]}) == "ready"   # 前置达标，自身无结论 → ready
    assert kms.kp_state("b", {}, {"b": ["a"]}) == "locked"          # 前置无结论 = 0 → locked


def test_resolve_states_chain():
    st = kms.resolve_states({"a": 0.95, "b": 0.5, "c": 0.2},
                            [("a", "b"), ("b", "c")])
    assert st == {"a": "mastered", "b": "ready", "c": "locked"}


def test_resolve_states_propagates_lock():
    # a 未达 → b 锁（b 未精通）→ c 锁（前置 b 为 locked 级联）
    st = kms.resolve_states({"a": 0.2, "b": 0.5, "c": 0.5},
                            [("a", "b"), ("b", "c")])
    assert st["b"] == "locked" and st["c"] == "locked"


# ── 环检测 ──────────────────────────────────────────────────────────────

def test_find_cycle_detects_new_edge_cycle():
    assert kms.find_cycle([("a", "b"), ("b", "c")], ("c", "a")) == ["a", "b", "c", "a"]


def test_find_cycle_none_when_acyclic():
    assert kms.find_cycle([("a", "b"), ("b", "c")], ("c", "d")) is None


def test_accept_edges_rejects_cycle_and_duplicates(db_none=None):
    from app.models.database import get_db
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    try:
        out = kms.accept_edges(db, [
            {"pre": "共价键", "post": "分子结构", "reason": "先键后构"},
            {"pre": "分子结构", "post": "晶体", "reason": "构后谈晶"},
        ], "t-1")
        assert len(out["accepted"]) == 2
        # 成环：晶体 → 共价键 会闭环
        out2 = kms.accept_edges(db, [{"pre": "晶体", "post": "共价键", "reason": "x"}], "t-1")
        assert not out2["accepted"] and "成环" in out2["rejected"][0]["reason_brief"]
        # 重复边幂等（重新启用）
        db.add(KnowledgeEdge(from_kp="共价键", to_kp="分子结构",
                             status="disabled"))
        db.commit()
        out3 = kms.accept_edges(db, [{"pre": "共价键", "post": "分子结构", "reason": ""}], "t-1")
        assert out3["accepted"][0].get("reenabled") is True
    finally:
        db.close()
        Base.metadata.drop_all(engine)


# ── API ────────────────────────────────────────────────────────────────

@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    from app.models.database import ExamRecord, RecordType
    session.add(Student(student_id="s-map", name="地图生", class_id="cls-1",
                        status="approved"))
    session.add(ExamRecord(record_id="rec-1", class_id="cls-1", name="班级练习",
                           type=RecordType.PRACTICE, exam_date=datetime.utcnow()))
    session.add(Question(question_id="mq-1", content="题干", record_id="rec-1",
                         knowledge_points=["氧化还原"], difficulty=3))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _auth(user="s-map", role="student"):
    return {"Authorization": f"Bearer {create_access_token(user, role)}"}


def test_student_map_cold_start(db_session, client):
    resp = client.get("/api/knowledge-map/student/s-map", headers=_auth())
    assert resp.status_code == 200
    body = resp.json()
    assert body["cold_start"] is True
    assert body["states"].get("氧化还原") == "ready"  # 无前置、无结论 → ready


def test_student_map_states_and_faded(db_session, client):
    from app.services.memory_card_service import ensure_card
    from datetime import datetime
    card = ensure_card(db_session, "s-map", "mq-1")
    card.due = datetime.utcnow()
    db_session.commit()
    resp = client.get("/api/knowledge-map/student/s-map", headers=_auth())
    body = resp.json()
    assert "氧化还原" in body["faded"]


def test_binding_only_shared_visible(db_session, client):
    """REQ-KMV-003：绑定只呈现已分享给该学生的工件（收件通知口径）。"""
    db_session.add(KPArtifactBinding(kp="氧化还原", artifact_id="art-shared"))
    db_session.add(KPArtifactBinding(kp="盐类水解", artifact_id="art-secret"))
    db_session.add(StudentNotification(
        notification_id="sn-share-1", student_id="s-map",
        type="artifact_share",
        title="老师分享了",
        content=json.dumps({"artifact_id": "art-shared", "kind": "animation",
                            "version": 1}),
        sent_at=datetime.utcnow()))
    db_session.commit()
    resp = client.get("/api/knowledge-map/student/s-map", headers=_auth())
    bind_kps = {b["kp"] for b in resp.json()["bindings"]}
    assert "氧化还原" in bind_kps
    assert "盐类水解" not in bind_kps  # 未分享：学生不可见（fail-closed）


def test_teacher_edge_management(db_session, client):
    tauth = {"Authorization": f"Bearer {create_access_token('t-1', 'teacher')}"}
    db_session.add(KnowledgeEdge(from_kp="电离平衡", to_kp="盐类水解",
                                 status="active", reason="先电离后水解"))
    db_session.commit()
    resp = client.get("/api/knowledge-map/teacher/edges", headers=tauth)
    assert resp.status_code == 200
    edge_id = resp.json()["edges"][0]["id"]
    r2 = client.put(f"/api/knowledge-map/edges/{edge_id}/status", headers=tauth,
                    json={"status": "disabled"})
    assert r2.json()["status"] == "disabled"
    r3 = client.put(f"/api/knowledge-map/edges/{edge_id}/status", headers=tauth,
                    json={"status": "weird"})
    assert r3.status_code == 400
