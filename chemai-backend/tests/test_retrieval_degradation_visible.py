"""相似题检索这一层降级时，事实必须走到出题响应里（工单 VS1）。

三条各钉一件事：
  1. 向量检索这一步抛错 → `retrieval.degraded` 为真、原因带上异常类型，本次退回关键词匹配；
  2. 向量库整个没开起来 → 原因来自服务自己的台账，不是只活在服务器 stdout 的一行 print；
  3. LLM 生成失败那条 `error` 现在真能到前端 —— 模型里以前没有这个字段，pydantic 的
     `extra=ignore` 把 `error="LLM生成失败…"` 直接丢掉，前端只看到 success:false。
"""
import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.api import question as question_mod
from app.middleware.auth import create_access_token
from app.models.database import Base, get_db

TEACHER = {"Authorization": f"Bearer {create_access_token('T1', 'teacher', school_id='sch1')}"}

PAYLOAD = {
    "knowledge_points": ["金属性质"],
    "difficulty": "medium",
    "quantity": 1,
    "question_types": ["choice"],
}


@pytest.fixture
def client(db_session, monkeypatch):
    app = FastAPI()
    app.include_router(question_mod.router, prefix="/api/question")
    app.dependency_overrides[get_db] = lambda: db_session

    from app.services import chemical_balance as bal_mod
    from app.services.exam_bank import exam_bank_service as bank_svc
    from app.services.llm_service import llm_service as llm_svc
    from app.services.vector_search import vector_search_service as vec_svc

    def fake_generate_questions(**kwargs):
        return {
            "success": True,
            "content": json.dumps({
                "questions": [{
                    "content": "钠与水反应的实质是（　　）",
                    "options": ["A. 钠失去电子", "B. 钠得到电子", "C. 水被氧化", "D. 无变化"],
                    "answer": "A",
                    "knowledge_points": ["金属性质"],
                    "difficulty": "medium",
                }]
            }, ensure_ascii=False),
        }

    monkeypatch.setattr(llm_svc, "generate_questions", fake_generate_questions)
    monkeypatch.setattr(bank_svc, "find_similar_questions", lambda **kw: [])
    monkeypatch.setattr(vec_svc, "search_similar", lambda **kw: [])
    monkeypatch.setattr(vec_svc, "layer_status",
                        lambda: {"available": True, "vector_count": 10, "degraded": False,
                                 "embedding_model": "BAAI/bge-large-zh-v1.5",
                                 "last_failure": None})
    monkeypatch.setattr(bal_mod, "audit_chemical_equation",
                        lambda eq: {"overall_status": "passed", "overall_message": "ok"})

    with TestClient(app) as c:
        yield c


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    yield s
    s.close()
    Base.metadata.drop_all(engine)


def test_healthy_retrieval_says_so(client):
    got = client.post("/api/question/generate", json=PAYLOAD, headers=TEACHER).json()
    assert got["success"] is True, got
    assert got["retrieval"]["degraded"] is False, got["retrieval"]


def test_a_vector_step_that_throws_is_reported_as_degraded(client, monkeypatch):
    from app.services.vector_search import vector_search_service as vec_svc

    def boom(**kw):
        raise RuntimeError("Cannot open header file")

    monkeypatch.setattr(vec_svc, "search_similar", boom)
    got = client.post("/api/question/generate", json=PAYLOAD, headers=TEACHER).json()

    retrieval = got["retrieval"]
    assert got["success"] is True, "题还是要生成的，降级不等于失败"
    assert retrieval["degraded"] is True, retrieval
    assert "RuntimeError" in retrieval["reason"] and "Cannot open header file" in retrieval["reason"], retrieval
    # 退回去处用了谁：这条通路只剩关键词匹配
    assert retrieval["used"] in ("none", "simple"), retrieval


def test_a_library_that_never_opened_reports_the_reason_from_its_ledger(client, monkeypatch):
    """库没开起来时不会有异常抛出（`search_similar` 直接返回 []），原因只能在台账里。"""
    from app.services.vector_search import vector_search_service as vec_svc

    monkeypatch.setattr(vec_svc, "layer_status", lambda: {
        "available": False, "vector_count": None, "degraded": True,
        "embedding_model": "BAAI/bge-large-zh-v1.5",
        "last_failure": {"stage": "打开向量库", "reason": "Cannot open header file（本轮只用简单匹配）",
                         "at": "2026-09-28 01:00:00"}})
    got = client.post("/api/question/generate", json=PAYLOAD, headers=TEACHER).json()

    retrieval = got["retrieval"]
    assert retrieval["degraded"] is True and retrieval["available"] is False, retrieval
    assert "Cannot open header file" in retrieval["reason"], retrieval


def test_the_llm_failure_reason_reaches_the_client(client, monkeypatch):
    """`error` 字段以前根本不在响应模型里，这句话在响应边界就被丢掉了。"""
    from app.services.llm_service import llm_service as llm_svc

    monkeypatch.setattr(llm_svc, "generate_questions",
                        lambda **kw: {"success": False, "content": ""})
    got = client.post("/api/question/generate", json=PAYLOAD, headers=TEACHER).json()

    assert got["success"] is False
    assert got.get("error"), "失败原因没进响应，前端只能说'生成失败'"
    assert got.get("retrieval") is not None


def _row(exam_id):
    from app.models.database import HistoricalExam

    return HistoricalExam(exam_id=exam_id, source="测试卷", year=2024,
                          question_number=exam_id[-1], content=f"{exam_id} 的题干",
                          answer="A", knowledge_points=["金属性质"], difficulty=3)


def test_a_vector_hit_becomes_an_actual_reference_question(client, db_session, monkeypatch):
    """命中要真的进提示词 —— 表里那些题内存列表查不到，必须落到 `historical_exams`。

    这条同时钉两件事：`search_similar` 回的是原题 id（不是 `::kp-3`），以及
    `get_reference()` 的表回退。少了任意一件，向量这一层跑对了老师也拿不到锚定真题的题。
    """
    from app.services.llm_service import llm_service as llm_svc
    from app.services.vector_search import vector_search_service as vec_svc

    for i in ("q_db_1", "q_db_2", "q_db_3"):
        db_session.add(_row(i))
    db_session.commit()

    seen = {}

    def spy(**kwargs):
        seen["rag"] = kwargs.get("rag_context")
        return {"success": True, "content": json.dumps(
            {"questions": [{"content": "生成的题", "options": ["A. a", "B. b"], "answer": "A",
                           "knowledge_points": ["金属性质"], "difficulty": "medium"}]},
            ensure_ascii=False)}

    monkeypatch.setattr(llm_svc, "generate_questions", spy)
    monkeypatch.setattr(vec_svc, "search_similar", lambda **kw: [
        {"exam_id": f"q_db_{i}", "chunk_id": f"q_db_{i}::kp-0",
         "similarity": 0.9, "match_method": "vector"} for i in (1, 2, 3)])

    got = client.post("/api/question/generate", json=PAYLOAD, headers=TEACHER).json()

    assert got["retrieval"]["used"] == "vector", got["retrieval"]
    assert got["retrieval"]["vector_hits"] == 3, got["retrieval"]
    assert seen["rag"] is not None and len(seen["rag"]) == 3, seen
    assert {r["exam_id"] for r in seen["rag"]} == {"q_db_1", "q_db_2", "q_db_3"}, seen["rag"]
    assert seen["rag"][0]["content"] == "q_db_1 的题干", seen["rag"][0]


def test_hits_that_resolve_to_nothing_do_not_claim_vector(client, monkeypatch):
    """命中 5 条却一条都没解析出来时，不许写 used=vector —— 那正是这条要修的谎。"""
    from app.services.vector_search import vector_search_service as vec_svc

    monkeypatch.setattr(vec_svc, "search_similar", lambda **kw: [
        {"exam_id": "q_not_anywhere", "chunk_id": "q_not_anywhere::kp-0",
         "similarity": 0.95, "match_method": "vector"}])

    got = client.post("/api/question/generate", json=PAYLOAD, headers=TEACHER).json()

    retrieval = got["retrieval"]
    assert retrieval["vector_hits"] == 1, retrieval
    assert retrieval["used"] == "none", f"题没进提示词，used 却说进了：{retrieval}"
    assert retrieval["degraded"] is False, retrieval


def test_used_follows_the_layer_that_actually_supplied_the_questions(client, db_session, monkeypatch):
    """`used` 由进了提示词的题各自带着的 match_method 倒推，不在各分支手写。

    向量层在 `query()` 抛异常时会退回简单匹配排序，返回的条目带着 `match_method="simple"` ——
    那种情况下报 used=vector 就是自相矛盾，而这个字段的全部意义就是"这次到底靠什么锚的题"。
    """
    from app.services.vector_search import vector_search_service as vec_svc

    for i in ("q_db_1", "q_db_2", "q_db_3"):
        db_session.add(_row(i))
    db_session.commit()

    monkeypatch.setattr(vec_svc, "search_similar", lambda **kw: [
        {"exam_id": f"q_db_{i}", "chunk_id": f"q_db_{i}::kp-0",
         "similarity": 0.7, "match_method": "simple"} for i in (1, 2, 3)])
    got = client.post("/api/question/generate", json=PAYLOAD, headers=TEACHER).json()
    assert got["retrieval"]["used"] == "simple", got["retrieval"]

    monkeypatch.setattr(vec_svc, "search_similar", lambda **kw: [
        {"exam_id": "q_db_1", "chunk_id": "q_db_1::kp-0", "similarity": 0.9, "match_method": "vector"},
        {"exam_id": "q_db_2", "chunk_id": "q_db_2::kp-0", "similarity": 0.7, "match_method": "simple"},
        {"exam_id": "q_db_3", "chunk_id": "q_db_3::kp-0", "similarity": 0.7, "match_method": "simple"}])
    got2 = client.post("/api/question/generate", json=PAYLOAD, headers=TEACHER).json()
    assert got2["retrieval"]["used"] == "vector+simple", got2["retrieval"]


def test_a_blueprint_id_alone_anchors_the_variant_question(client, db_session, monkeypatch):
    """选蓝本时，那道题必须真的进提示词 —— 而且只看 id，题面由后端自己查。

    两个坑叠着，这条通路从有那天起就没生效过：以前要求 `variant_qid` 与 `variant_source`
    都非空，而前端两处发的键名是 `variant_source_id`（模型里没这个字段，pydantic 直接丢）。
    蓝本还大多只在 `historical_exams` 表里，内存那份只有 12 条。
    """
    from app.services.llm_service import llm_service as llm_svc

    db_session.add(_row("q_blueprint"))
    db_session.commit()
    seen = {}

    def spy(**kwargs):
        seen["rag"] = kwargs.get("rag_context")
        return {"success": True, "content": json.dumps(
            {"questions": [{"content": "变种题", "options": ["A. a", "B. b"], "answer": "A",
                           "knowledge_points": ["金属性质"], "difficulty": "medium"}]},
            ensure_ascii=False)}

    monkeypatch.setattr(llm_svc, "generate_questions", spy)
    got = client.post("/api/question/generate",
                      json={**PAYLOAD, "variant_qid": "q_blueprint"}, headers=TEACHER).json()

    assert got["success"] is True, got
    assert got["retrieval"]["used"] == "blueprint", got["retrieval"]
    assert seen["rag"] and len(seen["rag"]) == 1, seen
    assert seen["rag"][0]["exam_id"] == "q_blueprint", seen["rag"]
    assert seen["rag"][0]["similarity"] == 1.0, seen["rag"]


def test_a_blueprint_that_does_not_exist_falls_back_instead_of_generating_blind(
        client, db_session, monkeypatch):
    """蓝本 id 查不到时退回按知识点检索，而不是空着提示词硬生成。

    以前这里是 `if not request.variant_qid:` —— 一旦带了 id（哪怕查不到），整段检索都被跳过，
    LLM 收到的是"变种题，但没有任何参照题"。
    """
    from app.services.vector_search import vector_search_service as vec_svc

    for i in ("q_db_1", "q_db_2", "q_db_3"):
        db_session.add(_row(i))
    db_session.commit()
    monkeypatch.setattr(vec_svc, "search_similar", lambda **kw: [
        {"exam_id": f"q_db_{i}", "chunk_id": f"q_db_{i}::kp-0",
         "similarity": 0.88, "match_method": "vector"} for i in (1, 2, 3)])

    got = client.post("/api/question/generate",
                      json={**PAYLOAD, "variant_qid": "q_no_such_thing"}, headers=TEACHER).json()

    assert got["success"] is True, got
    assert got["retrieval"]["used"] == "vector", got["retrieval"]
    assert got["retrieval"]["vector_hits"] == 3, got["retrieval"]


@pytest.mark.asyncio
async def test_the_agent_tool_also_anchors_on_a_table_only_blueprint(monkeypatch):
    """agent 那条出题通路同样要查表：老师从题库选的蓝本基本不在内存那 12 条里。"""
    import json as _json

    from agent.tools import generation as gen
    from app.services.exam_bank import exam_bank_service as bank

    monkeypatch.setattr(bank, "get_by_exam_id", lambda exam_id: None)   # 内存里没有
    monkeypatch.setattr(bank, "get_reference",
                        lambda exam_id, db=None: {"exam_id": exam_id, "content": "表里的蓝本题",
                                                  "answer": "A", "knowledge_points": ["金属性质"],
                                                  "difficulty": "medium", "source": "测试卷",
                                                  "year": 2024, "question_number": "1",
                                                  "from": "historical_exams"})
    seen = {}

    class _Svc:
        def generate_questions(self, **kwargs):
            seen["rag"] = kwargs.get("rag_context")
            return {"success": True, "content": _json.dumps({"questions": []})}

    # `app.services/__init__.py` 把实例重导出了，属性名 `app.services.llm_service` 拿到的是
    # LLMService 实例而不是模块 —— 要打的是模块上那个名字，所以走 sys.modules。
    import sys as _sys
    monkeypatch.setattr(_sys.modules["app.services.llm_service"], "llm_service", _Svc())
    await gen.generate_questions(
        knowledge_points="金属性质", difficulty="medium", quantity=1,
        question_types="single_choice:1", variant_qid="q_table_only")

    assert seen["rag"] and seen["rag"][0]["exam_id"] == "q_table_only", seen
    assert seen["rag"][0]["match_method"] == "blueprint", seen["rag"]
