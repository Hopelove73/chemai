"""RAG 运营三件套 + 角色只读矩阵。

seam：
  · `GET /api/rag/metrics` — 五项指标口径
  · `GET/POST /api/rag/traces*` — 筛选、标注校验、回流金标
  · `POST /api/rag/retrieval-test` — 临时参数（假检索资源替换，不碰真 chroma）
  · 评测 run/compare — 构造两次运行验证「新增失败/修复」归类
  · `GET /api/roles/overview` — 与 ROLE_PERMISSIONS 逐项一致（防漂移）
"""
from datetime import datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import rag_ops as rag_ops_mod
from app.api import roles as roles_mod
from app.middleware.auth import (
    ALL_ROLES, ROLE_PERMISSIONS, create_access_token,
)
from app.models.database import (
    Base, GoldenQuestion, QaTrace, RagEvalRun, RagRetrievalTest, get_db,
)
from app.utils.migrate import create_rag_tables


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    create_rag_tables(e)
    s = sessionmaker(bind=e, expire_on_commit=False)()
    yield e, s
    s.close()


@pytest.fixture
def client(engine, monkeypatch):
    e, session = engine
    app = FastAPI()
    app.include_router(rag_ops_mod.router)
    app.include_router(roles_mod.router)

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c


def hdr(role, user_id="A1"):
    return {"Authorization": f"Bearer {create_access_token(user_id, role, school_id='sch1')}"}


def _trace(db, tid, *, zero_hit=False, refusal=False, top1=0.8, feedback="none",
           label=None, reason=None, days_ago=0):
    db.add(QaTrace(
        trace_id=tid, user_id="S1", role="student", question=f"问题{tid}",
        hits=[] if zero_hit else [{"chunk_id": "c1", "score": top1}],
        zero_hit=zero_hit, top1_score=None if zero_hit else top1,
        answer="回答", refusal_flag=refusal, latency_ms=100, model_name="m",
        feedback=feedback, ops_label=label, bad_reason=reason,
        created_at=datetime.utcnow() - timedelta(days=days_ago),
    ))
    db.commit()


def test_metrics_rates(client, engine):
    _, db = engine
    _trace(db, "t1")
    _trace(db, "t2", zero_hit=True, refusal=True)
    _trace(db, "t3", top1=0.3)          # 低分命中
    _trace(db, "t4", feedback="down")

    m = client.get("/api/rag/metrics?days=7", headers=hdr("admin")).json()
    assert m["total"] == 4
    assert m["zero_hit_rate"] == 0.25
    assert m["hit_rate"] == 0.75
    assert m["refusal_rate"] == 0.25
    assert m["negative_feedback_rate"] == 0.25
    assert m["low_score_rate"] == round(1 / 3, 4)   # 3 个有命中里 1 个低分


def test_traces_filters_and_detail(client, engine):
    _, db = engine
    _trace(db, "t1")
    _trace(db, "t2", zero_hit=True)
    assert client.get("/api/rag/traces?zero_hit=yes", headers=hdr("admin")).json()["total"] == 1
    assert client.get("/api/rag/traces?refusal=yes", headers=hdr("admin")).json()["total"] == 0

    detail = client.get("/api/rag/traces/t1", headers=hdr("admin")).json()["trace"]
    assert detail["answer"] == "回答" and "retrieval_config" in detail

    assert client.get("/api/rag/traces/nope", headers=hdr("admin")).status_code == 404


def test_label_validation_and_golden(client, engine):
    _, db = engine
    _trace(db, "t1")
    bad = client.post("/api/rag/traces/t1/label",
                      json={"label": "bad"}, headers=hdr("admin"))
    assert bad.status_code == 400          # 坏标注必须给原因

    ok = client.post("/api/rag/traces/t1/label",
                     json={"label": "bad", "bad_reason": "幻觉"}, headers=hdr("admin"))
    assert ok.status_code == 200

    g = client.post("/api/rag/traces/t1/to-golden", headers=hdr("admin")).json()
    assert g["golden_id"] and g["expected_chunks"] == 1
    listed = client.get("/api/rag/golden-questions", headers=hdr("admin")).json()
    assert listed["total"] == 1 and listed["questions"][0]["source"] == "trace"


def test_golden_crud_and_permissions(client):
    body = {"question": "电解池阴阳极怎么判断", "expected_chunks": [], "match_mode": "any",
            "status": "active"}
    created = client.post("/api/rag/golden-questions", json=body, headers=hdr("admin")).json()
    gid = created["question"]["id"]
    upd = client.put(f"/api/rag/golden-questions/{gid}",
                     json={**body, "status": "paused"}, headers=hdr("admin"))
    assert upd.json()["question"]["status"] == "paused"
    assert client.delete(f"/api/rag/golden-questions/{gid}", headers=hdr("admin")).status_code == 200

    # 教师无 knowledge:update
    assert client.post("/api/rag/golden-questions", json=body,
                       headers=hdr("teacher", "T1")).status_code == 403


def test_retrieval_test_uses_overrides(client, monkeypatch):
    """临时参数：top_k 生效、返回 applied/default 配置、不写 trace。"""
    class _Col:
        def query(self, query_embeddings=None, n_results=4, include=None):
            hits = [("c1", {"source_path": "A.md", "section_no": 1, "body": "x", "title_path": "A"}, "x", 0.1),
                    ("c2", {"source_path": "A.md", "section_no": 2, "body": "y", "title_path": "A"}, "y", 0.2)]
            hits = hits[:n_results]
            return {"ids": [[h[0] for h in hits]], "metadatas": [[h[1] for h in hits]],
                    "documents": [[h[2] for h in hits]], "distances": [[h[3] for h in hits]]}

    class _Enc:
        def encode(self, texts):
            return [[0.0, 0.0]]

    monkeypatch.setattr(rag_ops_mod, "_kb_runtime",
                        lambda: {"collection": _Col(), "encoder": _Enc(), "graph_engine": None,
                                 "exclude_ids": set()})
    r = client.post("/api/rag/retrieval-test",
                    json={"query": "电子排布", "top_k": 2, "threshold": 0.5,
                          "graph_enabled": False}, headers=hdr("admin"))
    assert r.status_code == 200
    body = r.json()
    assert body["applied_config"]["top_k"] == 2
    assert body["applied_config"]["graph_enabled"] is False
    assert len(body["hits"]) == 2

    # 越界参数
    assert client.post("/api/rag/retrieval-test", json={"query": "x", "top_k": 99},
                       headers=hdr("admin")).status_code == 400


def test_eval_compare_classification(client, engine):
    _, db = engine
    b = RagEvalRun(run_id="b1", status="succeeded",
                   items=[{"id": 1, "passed": True}, {"id": 2, "passed": False},
                          {"id": 3, "passed": True}, {"id": 4, "passed": False}])
    c = RagEvalRun(run_id="c1", status="succeeded",
                   items=[{"id": 1, "passed": False}, {"id": 2, "passed": True},
                          {"id": 3, "passed": True}, {"id": 4, "passed": False}])
    db.add_all([b, c]); db.commit()

    d = client.get("/api/rag/eval-comparison?baseline=b1&candidate=c1",
                   headers=hdr("admin")).json()["diff"]
    assert [x["id"] for x in d["new_fail"]] == [1]
    assert [x["id"] for x in d["fixed"]] == [2]
    assert [x["id"] for x in d["still_fail"]] == [4]
    assert d["still_pass_count"] == 1


def test_role_overview_matches_matrix(client):
    body = client.get("/api/roles/overview", headers=hdr("admin")).json()
    assert body["read_only"] is True
    by_role = {r["role"]: r for r in body["roles"]}
    assert set(by_role) == set(ALL_ROLES)
    for role in ALL_ROLES:
        expected = {res: sorted(acts) for res, acts in ROLE_PERMISSIONS.get(role, {}).items()}
        assert by_role[role]["permissions"] == expected, f"{role} 矩阵与代码不一致"

    assert client.get("/api/roles/overview", headers=hdr("teacher", "T1")).status_code == 403


def test_retrieval_test_records_history(client, monkeypatch, engine):
    """召回测试要留历史（进页能看到最近跑过什么），且历史不写问答指标。"""
    class _Col:
        def query(self, query_embeddings=None, n_results=4, include=None):
            hits = [("c1", {"source_path": "A.md", "section_no": 1, "body": "x", "title_path": "A"}, "x", 0.1)]
            return {"ids": [["c1"]], "metadatas": [[hits[0][1]]], "documents": [["x"]],
                    "distances": [[0.1]]}

    class _Enc:
        def encode(self, texts):
            return [[0.0, 0.0]]

    monkeypatch.setattr(rag_ops_mod, "_kb_runtime",
                        lambda: {"collection": _Col(), "encoder": _Enc(),
                                 "graph_engine": None, "exclude_ids": set()})
    r = client.post("/api/rag/retrieval-test",
                    json={"query": "电解池", "top_k": 2, "threshold": 0.5,
                          "graph_enabled": True}, headers=hdr("admin"))
    assert r.status_code == 200

    _, db = engine
    assert db.query(RagRetrievalTest).count() == 1
    listed = client.get("/api/rag/retrieval-tests", headers=hdr("admin")).json()
    assert listed["tests"][0]["query"] == "电解池"
    assert listed["tests"][0]["hits_count"] == 1

    # 历史不写 qa_traces（线上指标只统计真实问答）
    assert db.query(QaTrace).count() == 0
