"""admin-shell-v2 八图概览：数据契约 + 口径一致性 + 小样本降级 + 权限。

seam：`GET /api/admin/overview-charts`（HTTP 接口，不测内部函数）。
期望值来自手工构造的小库（已知答案），不是从实现反推。
"""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import admin_overview as admin_overview_mod
from app.middleware.auth import create_access_token
from app.models.database import (
    Account, Base, Class, Grade, School, Student, StudentAnswer, StudentParentBinding,
    Parent, get_db,
)
from app.models.warning_log import WarningLog


@pytest.fixture(autouse=True)
def _clear_charts_cache():
    """八图有 60s 进程内缓存（键只含 school_id/elevated）。

    每个用例换一个新内存库，但缓存键相同 → 不清会读到上一个用例的 payload。
    """
    from app.api import admin_overview as mod
    mod._charts_cache.clear()
    yield
    mod._charts_cache.clear()


@pytest.fixture
def world():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()

    s.add_all([School(school_id="sch1", name="测试中学"),
               Grade(grade_id="g1", school_id="sch1", name="高一", year=2025)])
    s.add_all([
        Class(class_id="c1", name="一班", grade="高一", grade_id="g1", subject="化学"),
        Class(class_id="c2", name="二班", grade="高一", grade_id="g1", subject="化学"),
    ])
    # c1 有 3 个学生（小样本 → 不显示百分比），c2 有 1 个
    s.add_all([
        Student(student_id="S1", class_id="c1", name="学生一"),
        Student(student_id="S2", class_id="c1", name="学生二"),
        Student(student_id="S3", class_id="c1", name="学生三"),
        Student(student_id="S4", class_id="c2", name="学生四"),
    ])
    # 作答：S1 两次（1 对 1 错）→ 50%；S2 一次错 → 0%；S4 一次对 → 100%
    now = datetime.utcnow()
    s.add_all([
        StudentAnswer(answer_id="a1", student_id="S1", question_id="q1",
                      is_correct=True, answered_at=now - timedelta(days=3)),
        StudentAnswer(answer_id="a2", student_id="S1", question_id="q2",
                      is_correct=False, answered_at=now - timedelta(days=3)),
        StudentAnswer(answer_id="a3", student_id="S2", question_id="q1",
                      is_correct=False, answered_at=now - timedelta(days=40)),
        StudentAnswer(answer_id="a4", student_id="S4", question_id="q1",
                      is_correct=True, answered_at=now - timedelta(days=200)),
    ])
    # 家长：3 条绑定，其中 1 位有账号
    s.add_all([
        Parent(parent_id="P1", name="家长一", password_hash="x"),
        Parent(parent_id="P2", name="家长二", password_hash="x"),
        Parent(parent_id="P3", name="家长三", password_hash="x"),
        StudentParentBinding(binding_id="b1", student_id="S1", parent_id="P1", status="active", bind_code="B1"),
        StudentParentBinding(binding_id="b2", student_id="S2", parent_id="P2", status="active", bind_code="B2"),
        StudentParentBinding(binding_id="b3", student_id="S3", parent_id="P3", status="inactive", bind_code="B3"),
    ])
    s.add(Account(account_id="acc_p1", username="p1", password_hash="x", role="parent", parent_id="P1"))
    # 预警：识别 2，已处理 0
    s.add_all([
        WarningLog(warning_id="w1", student_id="S1", warning_type="no_login", title="未登录", status="pending"),
        WarningLog(warning_id="w2", student_id="S2", warning_type="score_drop", title="成绩下滑", status="pending"),
    ])
    s.commit()
    yield engine, s
    s.close()


@pytest.fixture
def client(world):
    _, session = world
    app = FastAPI()
    app.include_router(admin_overview_mod.router)

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        c.session = session
        yield c


def hdr(role, user_id="A1"):
    return {"Authorization": f"Bearer {create_access_token(user_id, role, school_id='sch1')}"}


def test_contract_and_counts(client):
    r = client.get("/api/admin/overview-charts", headers=hdr("admin"))
    assert r.status_code == 200
    body = r.json()

    assert body["kpi"] == {"students": 4, "teachers": 0, "classes": 2,
                           "parent_bindings": 2, "parent_accounts": 1}

    # 家长漏斗：绑定 2（active）→ 有账号 1 → 近 7 天活跃无数据源（None，诚实）
    assert body["parent_funnel"] == {"bound": 2, "activated": 1, "active_7d": None,
                                     "active_7d_available": False}

    # 趋势 12 个月，且带数据截止日（不为空）
    assert len(body["trend"]["points"]) == 12
    assert body["trend"]["data_until"]

    # 趋势：4 条作答全部落在 12 个月窗内（3 天前 ×2、40 天前 ×1、200 天前 ×1）
    total = sum(p["answers"] for p in body["trend"]["points"])
    assert total == 4


def test_class_compare_marked_small_sample(client):
    body = client.get("/api/admin/overview-charts", headers=hdr("admin")).json()
    by_id = {c["class_id"]: c for c in body["class_compare"]}
    assert by_id["c1"]["answers"] == 3
    assert by_id["c1"]["accuracy"] == round(1 / 3, 4)
    # c1：S1 2 次（50%）、S2 1 次（0%）→ 事件加权 1/3；中位数 = median(0.5, 0.0) = 0.25
    assert by_id["c1"]["median"] == 0.25
    assert by_id["c1"]["students"] == 2
    assert by_id["c1"]["small_sample"] is True     # 3 < 10 → 前端只画点不画百分比
    assert by_id["c2"]["small_sample"] is True     # 1 < 10


def test_student_scatter_and_weak_kps(client):
    body = client.get("/api/admin/overview-charts", headers=hdr("admin")).json()
    stu = {s["student_id"]: s for s in body["student_scatter"]}
    assert stu["S1"]["answers"] == 2 and stu["S1"]["accuracy"] == 0.5
    assert stu["S2"]["accuracy"] == 0.0
    # 知识点热力/薄弱 TOP 在本库没有 kp 映射 → 空数组而不是 500
    assert isinstance(body["kp_heatmap"]["cells"], list)
    assert body["weak_kps"] == []


def test_weak_kps_use_kb_error_rate(client, world):
    """回归：知识库聚合必须真的连通。

    曾因 `error_rates_for_kps` 收 Connection 而实现传了 `db.get_bind()`（Engine），
    异常被 except 吞掉 → 热力图永远空。本用例用真实映射与作答把这条链路钉住：
    种子 = 1 道题（标签映射到词典码）+ 6 次作答（3 错 3 对）→ 错误率 0.5、n=6。
    """
    from app.models.database import KBTagMap, Question

    _, session = world
    session.add(KBTagMap(tag_text="电解池", kp_id="M1-T2-KP01", status="accepted"))
    session.add(Question(question_id="q_kp", content="电解池判断题", answer="A",
                         knowledge_points=["电解池"]))
    for i in range(6):
        session.add(StudentAnswer(
            answer_id=f"kp_{i}", student_id="S1", question_id="q_kp",
            is_correct=(i % 2 == 0),  # 3 对 3 错
            answered_at=datetime.utcnow(),
        ))
    session.commit()

    # 60s 进程内缓存：换库前清掉，避免读到上一个用例的 payload
    from app.api import admin_overview as mod
    mod._charts_cache.clear()

    body = client.get("/api/admin/overview-charts", headers=hdr("admin")).json()
    weak = {w["kp_id"]: w for w in body["weak_kps"]}
    assert "M1-T2-KP01" in weak
    assert weak["M1-T2-KP01"]["answers"] == 6
    assert weak["M1-T2-KP01"]["error_rate"] == 0.5

    heat = body["kp_heatmap"]
    assert any(k["kp_id"] == "M1-T2-KP01" for k in heat["kps"])
    assert heat["cells"], "热力矩阵不应为空"


def test_warning_two_stage_no_rate(client):
    body = client.get("/api/admin/overview-charts", headers=hdr("admin")).json()
    assert body["warning"]["identified"] == 2
    assert body["warning"]["processed"] == 0
    assert body["warning"]["closed_loop_available"] is False


def test_teacher_forbidden(client):
    assert client.get("/api/admin/overview-charts", headers=hdr("teacher", "T1")).status_code == 403
    assert client.get("/api/admin/overview-charts", headers=hdr("academic_admin", "AA1")).status_code == 200


def test_super_admin_empty_until_break_glass(client):
    body = client.get("/api/admin/overview-charts", headers=hdr("super_admin", "SU1")).json()
    assert body["data_scope_empty"] is True
