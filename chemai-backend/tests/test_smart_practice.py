"""智能练习（批 2 S7 / smart-practice REQ-SMP-001..004）。

纯函数：EAP θ 逐题更新收敛、SE 停止规则边界、未标定回退排序；
API：start → answer 循环 → finish，未标定题占比上报，作答落 StudentAnswer。
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth import create_access_token
from app.models.database import (
    Base, Question, QuestionCalibration, Student, StudentAnswer, get_db,
)
from app.services import smart_practice_service as sps


# ── 纯函数 ──────────────────────────────────────────────────────────────

def test_estimate_theta_converges_up_on_correct():
    # 全对（难度 0 的题）→ θ 上移
    theta, se = sps.estimate_theta([(1.0, 0.0)] * 5, [True] * 5)
    assert theta > 0.5
    assert se < 1.0


def test_estimate_theta_drops_on_wrong():
    theta, _ = sps.estimate_theta([(1.0, 0.0)] * 5, [False] * 5)
    assert theta < -0.5


def test_estimate_theta_no_answers_returns_prior():
    theta, se = sps.estimate_theta([], [])
    assert theta == 0.0 and se == 1.0


def test_se_shrinks_with_more_items():
    params = [(1.0, 0.0)] * 6
    se1 = sps.se_of(params[:1], 0.0)
    se6 = sps.se_of(params, 0.0)
    assert se6 < se1


def test_should_stop_boundaries():
    assert not sps.should_stop(0, 0.1)
    assert sps.should_stop(8, 0.9)          # 题数上限先到
    assert sps.should_stop(3, 0.3)          # SE 达标即停
    assert not sps.should_stop(3, 0.31)     # 未达标继续


def test_pick_next_item_falls_back_to_prior_ordering():
    # 无标定参数 → 难度最接近 θ 定向者先出（θ=2 定向难度 4，_theta_to_difficulty）
    pool = [{"question_id": f"q{i}", "difficulty": d}
            for i, d in enumerate([3, 4, 1])]
    picked = sps.pick_next_item(pool, set(), {}, 2.0,
                                {c["question_id"]: c["difficulty"] for c in pool})
    assert picked["question_id"] == "q1"


def test_pick_next_item_prefers_high_information():
    # 标定题：θ=0 时 b=0 信息量最大 → 先出
    pool = [{"question_id": "qb", "difficulty": 3},
            {"question_id": "qa", "difficulty": 3}]
    params = {"qb": (1.0, 2.0), "qa": (1.0, 0.0)}
    picked = sps.pick_next_item(pool, set(), params, 0.0,
                                {"qb": 3, "qa": 3})
    assert picked["question_id"] == "qa"


def test_pick_next_item_excludes_administered_and_empty_pool():
    pool = [{"question_id": "q0", "difficulty": 3}]
    assert sps.pick_next_item(pool, {"q0"}, {}, 0.0) is None
    assert sps.pick_next_item([], set(), {}, 0.0) is None


# ── API 全流程 ──────────────────────────────────────────────────────────

@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    session.add(Student(student_id="s-cat", name="猫测生", class_id="cls-1",
                        status="approved"))
    # 8 道带答案题（知识点：盐类水解），全部未标定 → 回退路径
    for i in range(8):
        session.add(Question(
            question_id=f"cat-{i}", content=f"题{i}",
            question_type="fill_blank", options=None, answer=f"ans{i}",
            knowledge_points=["盐类水解"], difficulty=3))
    # 标定 2 道（b=0）→ 已标定优先
    for i in range(2):
        session.add(QuestionCalibration(question_id=f"cat-{i}", a=1.0, b=0.0,
                                        n_answers=50))
    session.commit()
    yield session
    session.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session):
    app.dependency_overrides[get_db] = lambda: db_session
    yield TestClient(app)
    app.dependency_overrides.pop(get_db, None)


def _auth(user="s-cat", role="student"):
    return {"Authorization": f"Bearer {create_access_token(user, role)}"}


def test_smart_start_needs_kps_or_data(db_session, client):
    # 未指定 kps 且学生无作答 → 400
    resp = client.post("/api/practice/smart/start", headers=_auth(),
                       json={"student_id": "s-cat"})
    assert resp.status_code == 400


def test_smart_session_full_loop(db_session, client):
    resp = client.post("/api/practice/smart/start", headers=_auth(),
                       json={"student_id": "s-cat", "kps": ["盐类水解"], "count": 3})
    assert resp.status_code == 200
    first = resp.json()
    pid = first["practice_id"]
    q = first["question"]
    assert pid.startswith("smart_")
    assert q["content"] and q["question_type"]

    # 答到停止规则触发（count=3 由池控制有限轮；这里逐题答 8 题内必停）
    answered = 0
    current = q
    done = False
    for _ in range(10):
        r = client.post("/api/practice/smart/answer", headers=_auth(), json={
            "student_id": "s-cat", "practice_id": pid,
            "question_id": current["question_id"], "answer": "ans",
        })
        assert r.status_code == 200
        body = r.json()
        answered += 1
        assert "theta" in body and "standard_error" in body
        if body["done"]:
            done = True
            break
        current = body["next"]["question"]
    assert done, "8 题内必须触发停止规则"
    assert answered <= 8

    # 作答落 StudentAnswer（REQ-SMP-004：既有链路）
    n = db_session.query(StudentAnswer).filter(
        StudentAnswer.exam_record_id == pid).count()
    assert n == answered

    # finish 快照
    fin = client.post("/api/practice/smart/finish", headers=_auth(),
                      json={"student_id": "s-cat", "practice_id": pid})
    assert fin.status_code == 200
    summary = fin.json()
    assert summary["answered"] == answered
    assert 0.0 <= summary["uncalibrated_ratio"] <= 1.0
    assert "suggestion" in summary


def test_session_payload_repairs_choice_type_without_options(db_session, client):
    """E2E E7 回归：每日练习复制题曾把 question_type 缺省成 single_choice
    却没有选项 —— 前端既不渲染选项也不渲染作答器。payload 按选项实况纠偏。"""
    db_session.add(Question(question_id="bad-type", content="三空主观题",
                            question_type="single_choice", options=None,
                            answer="a;b;c", knowledge_points=["盐类水解"],
                            difficulty=3))
    db_session.commit()
    from app.api.smart_practice import _session_question_payload
    payload = _session_question_payload(
        db_session.query(Question).filter(
            Question.question_id == "bad-type").one())
    assert payload["question_type"] == "fill_blank"
    assert payload["blank_count"] == 3
