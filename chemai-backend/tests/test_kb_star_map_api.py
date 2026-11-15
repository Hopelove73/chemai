"""班级星图 API（star-map-teacher）：/api/knowledge-map/class/{class_id}/graph
与 .../kp/{kp_id}/weak-students。

钉子：班级过滤口径（c1/c2 同题不同率）、MIN_ANSWERS 阈值、静默降级（无数据
不 500）、ScopeGuard（教师只能看自己任教的班，他班 403）、weak-students 排序
与错误样本。
"""
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import knowledge_map as km_mod
from app.main import app
from app.middleware.auth import create_access_token
from app.models.database import (
    Base, Class, Grade, KBTagMap, KnowledgePoint, Question, School, Student,
    StudentAnswer, Teacher, TeacherClassSubject, get_db,
)


@pytest.fixture()
def sm_client():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    db.add_all([
        School(school_id="sch1", name="测试中学"),
        Grade(grade_id="g1", school_id="sch1", name="高一", year=2025),
        Class(class_id="c1", name="2419班", grade="高一", grade_id="g1", subject="化学"),
        Class(class_id="c2", name="2420班", grade="高一", grade_id="g1", subject="化学"),
        Class(class_id="c9", name="无数据班", grade="高一", grade_id="g1", subject="化学"),
        Teacher(teacher_id="T_chem", name="化学老师", school_id="sch1", status="approved"),
        Teacher(teacher_id="T_other", name="隔壁老师", school_id="sch1", status="approved"),
        TeacherClassSubject(id="t1", teacher_id="T_chem", class_id="c1", subject="化学"),
        TeacherClassSubject(id="t1b", teacher_id="T_chem", class_id="c9", subject="化学"),
        TeacherClassSubject(id="t2", teacher_id="T_other", class_id="c2", subject="化学"),
        Student(student_id="S1", class_id="c1", name="学生一"),
        Student(student_id="S2", class_id="c1", name="学生二"),
        Student(student_id="S3", class_id="c2", name="学生三"),
        KnowledgePoint(kp_id="M1-T2-KP06", name="氧化还原反应",
                       description="电子转移", code="M1-T2-KP06",
                       theme="T2 常见的无机物及其应用", module="必修一", order_no=6),
        KnowledgePoint(kp_id="M1-T2-KP14", name="物质的量",
                       description="n/N/m/V", code="M1-T2-KP14",
                       theme="T1 化学科学与实验探究", module="必修一", order_no=14),
        KBTagMap(tag_text="氧化还原反应", kp_id="M1-T2-KP06"),
        KBTagMap(tag_text="物质的量", kp_id="M1-T2-KP14"),
        Question(question_id="q1", content="题目一：氧化还原判断", answer="A",
                 knowledge_points=["氧化还原反应"]),
        Question(question_id="q3", content="题目三：物质的量计算", answer="B",
                 knowledge_points=["物质的量"]),
    ])
    db.add_all([
        # c1：q1 上 6 答 3 错 → 0.5
        StudentAnswer(answer_id="a1", student_id="S1", question_id="q1",
                      is_correct=False, student_answer="错答一"),
        StudentAnswer(answer_id="a2", student_id="S1", question_id="q1",
                      is_correct=False, student_answer="错答二"),
        StudentAnswer(answer_id="a3", student_id="S2", question_id="q1",
                      is_correct=False, student_answer="错答三"),
        StudentAnswer(answer_id="a4", student_id="S1", question_id="q1", is_correct=True),
        StudentAnswer(answer_id="a5", student_id="S2", question_id="q1", is_correct=True),
        StudentAnswer(answer_id="a6", student_id="S2", question_id="q1", is_correct=True),
        # c2：q1 上 5 答 5 错 → 1.0
        StudentAnswer(answer_id="a7", student_id="S3", question_id="q1", is_correct=False),
        StudentAnswer(answer_id="a8", student_id="S3", question_id="q1", is_correct=False),
        StudentAnswer(answer_id="a9", student_id="S3", question_id="q1", is_correct=False),
        StudentAnswer(answer_id="a10", student_id="S3", question_id="q1", is_correct=False),
        StudentAnswer(answer_id="a11", student_id="S3", question_id="q1", is_correct=False),
        # KP14：c1 仅 2 次作答（< MIN_ANSWERS，静默压制），无错误作答
        StudentAnswer(answer_id="a12", student_id="S1", question_id="q3", is_correct=True),
        StudentAnswer(answer_id="a13", student_id="S1", question_id="q3", is_correct=True),
    ])
    db.commit()

    def override_get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    from app.middleware.auth import PermissionChecker
    app.dependency_overrides[km_mod.get_db] = override_get_db

    def _client_for(teacher_id: str):
        # ScopeGuard 的 _classes_taught 直接拿 token user_id 查 teacher_class_subject
        # —— user_id 必须是 teacher_id（与 test_permission_roles 的 TEACHER 约定一致）
        token = create_access_token(teacher_id, "teacher", school_id="sch1")
        c = TestClient(app)
        c.headers.update({"Authorization": f"Bearer {token}"})
        return c

    yield _client_for, engine
    app.dependency_overrides.clear()
    db.close()
    engine.dispose()


def test_class_graph_class_scoped_rates(sm_client):
    """班级过滤：c1 看到自己的 0.5/6 次，看不到 c2 的样本。"""
    _client_for, _ = sm_client
    c = _client_for("T_chem")
    r = c.get("/api/knowledge-map/class/c1/graph")
    assert r.status_code == 200
    d = r.json()
    assert d["success"] and d["class_id"] == "c1" and d["class_name"] == "2419班"
    node = {n["kp_id"]: n for n in d["nodes"]}["M1-T2-KP06"]
    assert node["class_error_rate"] == pytest.approx(0.5)
    assert node["class_answers"] == 6
    kp14 = {n["kp_id"]: n for n in d["nodes"]}["M1-T2-KP14"]
    assert kp14["class_error_rate"] is None, "作答 <MIN_ANSWERS 不出错误率"
    assert kp14["class_answers"] == 0, "与全局口径同尺：样本过小整条压制，视同无可信样本"


def test_class_graph_silent_degradation(sm_client):
    """无数据班级：全部节点无错误率，端点 200 不 500。"""
    _client_for, _ = sm_client
    c = _client_for("T_chem")
    r = c.get("/api/knowledge-map/class/c9/graph")
    assert r.status_code == 200
    d = r.json()
    assert all(n["class_error_rate"] is None and n["class_answers"] == 0
               for n in d["nodes"])


def test_class_graph_403_for_other_class(sm_client):
    """ScopeGuard：T_chem 读 c2（隔壁老师的班）→ 403。"""
    _client_for, _ = sm_client
    c = _client_for("T_chem")
    assert c.get("/api/knowledge-map/class/c2/graph").status_code == 403
    assert c.get(
        "/api/knowledge-map/class/c2/kp/M1-T2-KP06/weak-students").status_code == 403


def test_weak_students_sorted_with_sample(sm_client):
    """下钻：按错误数排序，带最近一条错误作答样本；他班学生不出现。"""
    _client_for, _ = sm_client
    c = _client_for("T_chem")
    r = c.get("/api/knowledge-map/class/c1/kp/M1-T2-KP06/weak-students")
    assert r.status_code == 200
    students = r.json()["students"]
    assert [s["student_id"] for s in students] == ["S1", "S2"]
    assert students[0]["wrong_count"] == 2
    assert students[0]["last_wrong_answer"] in ("错答一", "错答二"), "最近一条错误样本"


def test_weak_students_empty_cases(sm_client):
    """无错误作答 / 未映射知识点 → 空列表不 500。"""
    _client_for, _ = sm_client
    c = _client_for("T_chem")
    assert c.get(
        "/api/knowledge-map/class/c1/kp/M1-T2-KP14/weak-students").json()["students"] == []
    assert c.get(
        "/api/knowledge-map/class/c1/kp/NO-SUCH-KP/weak-students").json()["students"] == []


# ---------------------------------------------------------------------------
# 星图坐标持久化（star-map-interactions REQ-SMI-006）：只插入缺失、从不覆盖
# ---------------------------------------------------------------------------

def test_layout_empty_then_insert(sm_client):
    _client_for, _ = sm_client
    c = _client_for("T_chem")
    assert c.get("/api/knowledge-map/layout").json()["layout"] == {}
    r = c.post("/api/knowledge-map/layout", json=[
        {"kp_id": "M1-T2-KP06", "x": 1.5, "y": -2.0, "z": 3.25},
        {"kp_id": "M1-T2-KP14", "x": 10, "y": 20, "z": 30},
    ])
    assert r.status_code == 200 and r.json()["added"] == 2
    layout = c.get("/api/knowledge-map/layout").json()["layout"]
    assert layout["M1-T2-KP06"] == [1.5, -2.0, 3.25]
    assert layout["M1-T2-KP14"] == [10, 20, 30]


def test_layout_never_overwrites_existing(sm_client):
    """并发首访幂等：已有坐标不被后来者覆盖，只补缺失的。"""
    _client_for, _ = sm_client
    c = _client_for("T_chem")
    c.post("/api/knowledge-map/layout", json=[{"kp_id": "M1-T2-KP06", "x": 1, "y": 2, "z": 3}])
    r = c.post("/api/knowledge-map/layout", json=[
        {"kp_id": "M1-T2-KP06", "x": 999, "y": 999, "z": 999},
        {"kp_id": "M1-T2-KP14", "x": 4, "y": 5, "z": 6},
    ])
    assert r.json() == {"success": True, "added": 1, "skipped": 1}
    layout = c.get("/api/knowledge-map/layout").json()["layout"]
    assert layout["M1-T2-KP06"] == [1, 2, 3], "已有坐标不得被覆盖"
    assert layout["M1-T2-KP14"] == [4, 5, 6]


def test_layout_permissions_and_validation(sm_client):
    _client_for, _ = sm_client
    from app.middleware.auth import create_access_token
    from fastapi.testclient import TestClient
    stu = TestClient(app)
    stu.headers.update({"Authorization": f"Bearer {create_access_token('S1', 'student', school_id='sch1')}"})
    assert stu.get("/api/knowledge-map/layout").status_code == 403
    assert stu.post("/api/knowledge-map/layout", json=[]).status_code == 403
    teacher = _client_for("T_chem")
    assert teacher.post("/api/knowledge-map/layout", json=[{"kp_id": "X"}]).status_code == 422
