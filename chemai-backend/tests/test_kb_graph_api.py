"""KB：教师图谱可视化端点——/api/knowledge-map/teacher/graph。

钉子：全量节点/边一次返回、节点字段齐全（词典+错误率+文档+方程式）、
统计表缺失时静默空（内存测试库无 kp_tag_map/questions）。
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.pool import StaticPool

from app.main import app
from app.middleware.auth import create_access_token


@pytest.fixture()
def gclient(tmp_path, patch_get_db):
    from app.api import knowledge_map as km_mod

    e = create_engine(
        f"sqlite:///{tmp_path}/g.db",
        connect_args={"check_same_thread": False}, poolclass=StaticPool)
    with e.begin() as conn:
        conn.execute(text("""
            CREATE TABLE knowledge_points (
                kp_id VARCHAR(64) PRIMARY KEY, name VARCHAR(256),
                category VARCHAR(128), description TEXT,
                pubchem_cid VARCHAR(64), question_count INTEGER,
                error_rate FLOAT, code VARCHAR(32), theme VARCHAR(64),
                module VARCHAR(32), order_no INTEGER,
                created_at TIMESTAMP, updated_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE knowledge_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_kp VARCHAR(128), to_kp VARCHAR(128),
                status VARCHAR(16) DEFAULT 'active',
                reason TEXT, proposed_by VARCHAR(64),
                edge_type VARCHAR(32), confidence FLOAT,
                source_doc VARCHAR(256), created_at TIMESTAMP
            )
        """))
        conn.execute(text("""
            CREATE TABLE kb_frontmatter (
                kp_id VARCHAR(64) PRIMARY KEY, module VARCHAR(32),
                theme VARCHAR(64), equations_json TEXT,
                prerequisites_json TEXT, misconceptions_json TEXT,
                source_doc VARCHAR(256), updated_at TIMESTAMP
            )
        """))
        conn.execute(text(
            "INSERT INTO knowledge_points VALUES "
            "('M1-T2-KP06','氧化还原反应','T2','电子转移',NULL,0,NULL,"
            "'M1-T2-KP06','T2 常见的无机物及其应用','必修一',6,NULL,NULL)"))
        conn.execute(text(
            "INSERT INTO knowledge_points VALUES "
            "('M1-T2-KP12','氯气的性质','T2','氯气性质',NULL,0,NULL,"
            "'M1-T2-KP12','T2 常见的无机物及其应用','必修一',12,NULL,NULL)"))
        conn.execute(text(
            "INSERT INTO knowledge_edges (from_kp, to_kp, status, edge_type, reason) "
            "VALUES ('M1-T2-KP06','M1-T2-KP12','active','prerequisite','强弱定价态')"))

    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=e, expire_on_commit=False)

    def override_get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    from app.middleware.auth import PermissionChecker
    app.dependency_overrides[km_mod.get_db] = override_get_db
    app.dependency_overrides[
        PermissionChecker("question", "read")] = lambda: {"user_id": "teacher_zhou"}
    token = create_access_token("teacher_zhou", "subject_lead", "school_cs_001")
    with __import__("contextlib").closing(TestClient(app)) as c:
        c.headers.update({"Authorization": f"Bearer {token}"})
        yield c, e
    app.dependency_overrides.clear()
    e.dispose()


def test_graph_returns_nodes_and_edges(gclient):
    c, _ = gclient
    r = c.get("/api/knowledge-map/teacher/graph")
    assert r.status_code == 200
    d = r.json()
    assert d["success"] and len(d["nodes"]) == 2 and len(d["edges"]) == 1
    node = {n["kp_id"]: n for n in d["nodes"]}["M1-T2-KP06"]
    assert node["name"] == "氧化还原反应" and node["module"] == "必修一"
    assert node["description"] == "电子转移"
    assert node["error_rate"] is None and node["answers"] == 0, "无统计表 → 静默空"
    edge = d["edges"][0]
    assert (edge["from_kp"], edge["to_kp"], edge["status"]) == \
        ("M1-T2-KP06", "M1-T2-KP12", "active")


def test_graph_includes_frontmatter_when_present(gclient):
    """kb_frontmatter 有行时，节点带 doc 与 equations。"""
    c, e = gclient
    with e.begin() as conn:
        conn.execute(text(
            "INSERT INTO kb_frontmatter VALUES ('M1-T2-KP06','必修一','T2',"
            ":eqj, '[]', '[]', '氧化还原反应.md', NULL)"),
            {"eqj": json.dumps(["Fe + CuSO4 = FeSO4 + Cu"], ensure_ascii=False)})
    r = c.get("/api/knowledge-map/teacher/graph")
    node = {n["kp_id"]: n for n in r.json()["nodes"]}["M1-T2-KP06"]
    assert node["doc"] == "氧化还原反应.md"
    assert node["equations"] == ["Fe + CuSO4 = FeSO4 + Cu"]


def test_graph_nodes_carry_reference_notes(gclient, monkeypatch):
    """KB4c-B：节点带 reference_notes（_load_reference_notes 打桩返回固定组）。"""
    c, e = gclient
    monkeypatch.setattr(
        "app.api.knowledge_map._load_reference_notes",
        lambda: {"M1-T2-KP06": [{"id": 21, "text": "氧化性 Cl₂>Br₂>Fe³⁺>I₂>S"}]})
    r = c.get("/api/knowledge-map/teacher/graph")
    node = {n["kp_id"]: n for n in r.json()["nodes"]}["M1-T2-KP06"]
    assert node["reference_notes"] == [{"id": 21, "text": "氧化性 Cl₂>Br₂>Fe³⁺>I₂>S"}]
    other = {n["kp_id"]: n for n in r.json()["nodes"]}["M1-T2-KP12"]
    assert other["reference_notes"] == [], "无细则节点为空数组"


def test_graph_reference_notes_empty_loader_renders_empty(gclient, monkeypatch):
    """_load_reference_notes 返回空表（文件缺失/损坏的降级出口）时：
    接口不炸、字段为空数组。真实文件的缺失路径由 _read_reference_notes 的
    try/except 承担（写入即告警降级），此处钉接口侧契约。"""
    c, e = gclient
    monkeypatch.setattr("app.api.knowledge_map._load_reference_notes", lambda: {})
    r = c.get("/api/knowledge-map/teacher/graph")
    assert r.status_code == 200
    assert all(n["reference_notes"] == [] for n in r.json()["nodes"])


def test_reference_notes_reader_degrades_on_missing_file(tmp_path, monkeypatch):
    """真实读取器：文件不存在 → 空表不炸（模块级缓存键含 mtime=0）。"""
    import app.api.knowledge_map as km
    monkeypatch.setattr(km, "_NOTES_PATH", tmp_path / "nope.json")
    km._read_reference_notes.cache_clear()
    assert km._read_reference_notes(0) == {}
    km._read_reference_notes.cache_clear()
