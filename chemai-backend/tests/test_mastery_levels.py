"""四级掌握状态映射（student-daily-loop... 批 2 / mastery-visualization REQ-MSV-001）。

表驱动：BKT 概率 → mastered/proficient/familiar/attempted；
课程掌握 % = 精通+熟练占比；阈值 0.7/0.9 与 knowledge-map 就绪过滤同源。
"""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import pytest

from app.services.mastery_service import (
    MASTERY_MASTERED_AT,
    MASTERY_PROFICIENT_AT,
    course_mastery_percent,
    mastery_level,
)


@pytest.mark.parametrize("prob,expected", [
    (0.0, "attempted"),
    (0.39, "attempted"),
    (0.4, "familiar"),
    (0.69, "familiar"),
    (0.7, "proficient"),
    (0.89, "proficient"),
    (0.9, "mastered"),
    (1.0, "mastered"),
])
def test_mastery_level_table(prob, expected):
    assert mastery_level(prob) == expected


def test_thresholds_are_shared_constants():
    assert MASTERY_PROFICIENT_AT == 0.7
    assert MASTERY_MASTERED_AT == 0.9


def test_course_mastery_percent_counts_proficient_plus():
    levels = ["mastered", "proficient", "familiar", "attempted"]
    assert course_mastery_percent(levels) == 50
    assert course_mastery_percent(["attempted"]) == 0
    assert course_mastery_percent(["mastered"]) == 100
    assert course_mastery_percent([]) == 0


def test_kp_mastery_endpoint_carries_bkt_fields(client_fixture=None):
    """kp-mastery 返回体带 mastery_probability + level（BKT 直读，旧 mastery 字段并存）。"""
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from app.main import app
    from app.middleware.auth import create_access_token
    from app.models.database import (
        Base, Question, Student, StudentAnswer, get_db,
    )

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()
    db.add(Student(student_id="s-m", name="掌握生", class_id="cls-1", status="approved"))
    for i in range(6):
        db.add(Question(question_id=f"mq-{i}", content="题干",
                        knowledge_points=["氧化还原"], difficulty=3))
        db.add(StudentAnswer(answer_id=f"ma-{i}", student_id="s-m",
                             question_id=f"mq-{i}", is_correct=(i >= 3),
                             confirmed=True))
    db.commit()

    app.dependency_overrides[get_db] = lambda: db
    try:
        with TestClient(app) as c:
            headers = {"Authorization": f"Bearer {create_access_token('s-m', 'student')}"}
            resp = c.get("/api/report/student/s-m/kp-mastery", headers=headers)
            assert resp.status_code == 200
            rows = resp.json()["kp_mastery"]
            assert rows, "有作答就该有知识点行"
            row = rows[0]
            # 旧字段保留（向后兼容）
            assert "mastery" in row and "correct_count" in row
            # BKT 字段直读 mastery_service（样本 6 ≥ MIN_SAMPLES）
            assert row["mastery_probability"] is not None
            assert row["level"] in ("attempted", "familiar", "proficient", "mastered")
    finally:
        app.dependency_overrides.pop(get_db, None)
        db.close()
        Base.metadata.drop_all(engine)
