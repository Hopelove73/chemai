"""草稿 HTTP 端点测试（issue #10 修订历史回看 + issue #11 页面接入门）。

最小 app 挂 diagnosis router + override get_db（StaticPool 内存库），
Bearer token 走 create_access_token；LLM 打桩后经 TestClient 真实调用端点。
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import (
    Base, Student, TeacherClassSubject, get_db,
)
from app.api import diagnosis as diag_mod
from app.middleware.auth import create_access_token


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


@pytest.fixture
def client(db_session, monkeypatch):
    """LLM 打桩 + 最小 app（diagnosis router）。"""
    import sys
    mod = sys.modules["app.services.llm_service"]
    monkeypatch.setattr(mod.llm_service, "generate_learning_plan", lambda **kw: {
        "success": True,
        "content": json.dumps({"plan_title": "测试计划", "weekly_goals": ["水解"]}, ensure_ascii=False),
    })
    monkeypatch.setattr(mod.llm_service, "revise_learning_plan", lambda **kw: {
        "success": True,
        "content": json.dumps({"plan_title": "测试计划v2", "weekly_goals": ["水解专题"]}, ensure_ascii=False),
    })

    app = FastAPI()
    app.include_router(diag_mod.router, prefix="/api/diagnosis", tags=["diagnosis"])

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c


@pytest.fixture
def school(db_session):
    """教师 t1 教 c1 班；学生 s1 在 c1，s2 在 c2。"""
    db_session.add(Student(student_id="s1", name="张三", class_id="c1", status="approved"))
    db_session.add(Student(student_id="s2", name="李四", class_id="c2", status="approved"))
    db_session.add(TeacherClassSubject(id=1, teacher_id="t1", class_id="c1", subject="chemistry"))
    db_session.commit()
    return db_session


@pytest.fixture
def t1_headers():
    token = create_access_token("t1", "teacher", school_id="school1")
    return {"Authorization": f"Bearer {token}"}


# ══ #10 修订历史回看 ═══════════════════════════════════════

def _make_draft(db, student_id="s1"):
    """测试侧造一份待确认草稿：单元外算内容、单元内落库（前置一）。"""
    from app.services.plan_draft_service import plan_draft_service
    from app.services.work_unit import work_unit
    computed = plan_draft_service.compute_draft(db, student_id)
    with work_unit(db):
        d = plan_draft_service.create_draft(db, computed, created_by="t1")
    assert "draft_id" in d, d
    return d


def test_versions_lists_chain_in_order(client, school, t1_headers):
    d = _make_draft(school)
    from app.services.plan_draft_service import plan_draft_service
    from app.services.work_unit import work_unit
    computed = plan_draft_service.compute_revision(school, d["draft_id"], "第二周改水解专题")
    with work_unit(school):
        plan_draft_service.revise_draft(school, computed, "t1")

    r = client.get(f"/api/diagnosis/learning-plan/draft/s1/{d['draft_id']}/versions",
                   headers=t1_headers)
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    versions = body["versions"]
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[1]["feedback"] == "第二周改水解专题"
    assert versions[1]["content"]["plan_title"] == "测试计划v2"


def test_versions_404_unknown_draft(client, school, t1_headers):
    r = client.get("/api/diagnosis/learning-plan/draft/s1/nope/versions", headers=t1_headers)
    assert r.status_code == 404


def test_versions_404_student_mismatch(client, school, t1_headers):
    """草稿属于 s1，用 s2 的路径访问 → 404（不泄露存在性）。
    前提：老师在 s2 的范围内（否则 ScopeGuard 先 403，那是另一条负路径）。"""
    school.add(TeacherClassSubject(id=3, teacher_id="t1", class_id="c2", subject="chemistry"))
    school.commit()
    d = _make_draft(school, "s1")
    r = client.get(f"/api/diagnosis/learning-plan/draft/s2/{d['draft_id']}/versions",
                   headers=t1_headers)
    assert r.status_code == 404


def test_versions_403_out_of_scope_teacher(client, school):
    """教师不教该生所在班 → Data Scope 403。"""
    school.add(TeacherClassSubject(id=2, teacher_id="t9", class_id="c9", subject="chemistry"))
    school.commit()
    d = _make_draft(school, "s1")
    token = create_access_token("t9", "teacher", school_id="school1")
    r = client.get(f"/api/diagnosis/learning-plan/draft/s1/{d['draft_id']}/versions",
                   headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


# ══ #11 页面接入门：创建草稿端点 ══════════════════════════════

def test_create_draft_endpoint(client, school, t1_headers):
    r = client.post("/api/diagnosis/learning-plan/draft/s1", headers=t1_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["success"] is True
    draft = body["draft"]
    assert draft["version"] == 1 and draft["status"] == "awaiting_confirmation"
    assert draft["content"]["plan_title"] == "测试计划"


def test_create_draft_endpoint_regenerates_supersedes(client, school, t1_headers):
    from app.services.plan_draft_service import plan_draft_service
    r1 = client.post("/api/diagnosis/learning-plan/draft/s1", headers=t1_headers).json()
    r2 = client.post("/api/diagnosis/learning-plan/draft/s1", headers=t1_headers).json()
    assert r1["draft"]["draft_id"] != r2["draft"]["draft_id"]
    assert plan_draft_service.get_draft(
        school, r1["draft"]["draft_id"])["status"] == "superseded"


def test_create_draft_endpoint_403_out_of_scope(client, school):
    token = create_access_token("t9", "teacher", school_id="school1")
    r = client.post("/api/diagnosis/learning-plan/draft/s1",
                    headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


# ══ 前置一：REST 决定通路各开一个工作单元 ═══════════════════

def test_approve_endpoint_commits_once(client, school, t1_headers, monkeypatch):
    """通路②：批准 + 发送两条写落进同一个工作单元，整条决定一次提交。

    红绿可证：把 `decide_learning_plan_draft` 里那个 `with work_unit(db)` 拆成两次
    提交（或让 service 自己 commit），这里就数到 2 或 3。
    """
    from sqlalchemy.orm import Session as _Session
    from app.services.plan_draft_service import plan_draft_service
    counts = []
    real = _Session.commit

    def counted(self):
        if self is school:
            counts.append(1)
        return real(self)

    monkeypatch.setattr(_Session, "commit", counted)
    d = _make_draft(school)
    baseline = len(counts)          # 造草稿那一步自己提交过，从这之后开始数

    r = client.post(
        f"/api/diagnosis/learning-plan/draft/s1/{d['draft_id']}/decision",
        headers=t1_headers, json={"action": "approve"})

    assert r.status_code == 200, r.text
    assert len(counts) - baseline == 1, (
        f"一次决定一次提交，实测 {len(counts) - baseline} 次")
    assert plan_draft_service.get_draft(school, d["draft_id"])["status"] == "sent"
