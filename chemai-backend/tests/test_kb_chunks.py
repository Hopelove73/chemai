"""KB 文档/分块集中管理测试：聚合、禁用标记、**禁用真的影响检索**。

seam：
  1. `kb_chunks.list_documents / list_chunks`（chroma 用假集合替换，测聚合与标记）
  2. `kb_chunks.set_chunk_disabled` + `disabled_chunk_ids`（DB 真值 + 缓存失效）
  3. `kb_context.build_kb_context(exclude_ids=...)`（检索结果按 id 排除；全被排除 → miss）
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import Base
from app.services import kb_chunks
from app.services.kb_context import build_kb_context


class FakeCollection:
    """最小的 chroma 集合替身：只实现 get() 与 query()。"""

    def __init__(self, rows, query_hits=None):
        self.rows = rows              # [(id, meta, doc)]
        self.query_hits = query_hits or []  # [(id, meta, doc, distance)]

    def get(self, where=None, include=None, limit=None):
        rows = self.rows
        if where and "source_path" in where:
            rows = [r for r in rows if (r[1] or {}).get("source_path") == where["source_path"]]
        return {"ids": [r[0] for r in rows],
                "metadatas": [r[1] for r in rows],
                "documents": [r[2] for r in rows]}

    def query(self, query_embeddings=None, n_results=4, include=None):
        hits = self.query_hits[:n_results]
        # chroma 的形状是「每条 query 一个列表」，这里只有一条 query → 外面再包一层
        return {"ids": [[h[0] for h in hits]],
                "metadatas": [[h[1] for h in hits]],
                "documents": [[h[2] for h in hits]],
                "distances": [[h[3] for h in hits]]}


class FakeEncoder:
    def encode(self, texts):
        return [[0.0, 0.0]]


ROWS = [
    ("c1", {"source_path": "A.md", "chunk_no": 1, "section_no": 1, "module": "必修一",
            "theme": "T1", "title": "A", "doc_type": "concept"}, "A 第一块"),
    ("c2", {"source_path": "A.md", "chunk_no": 2, "section_no": 1, "module": "必修一",
            "theme": "T1", "title": "A", "doc_type": "concept"}, "A 第二块"),
    ("c3", {"source_path": "B.md", "chunk_no": 1, "section_no": 1, "module": "必修二",
            "theme": "T2", "title": "B", "doc_type": "method"}, "B 第一块"),
]


@pytest.fixture
def db_session(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine, expire_on_commit=False)()
    monkeypatch.setattr(kb_chunks, "_collection", lambda: FakeCollection(ROWS))
    kb_chunks.invalidate()
    yield s
    s.close()
    kb_chunks.invalidate()


def test_list_documents_aggregates(db_session):
    docs = {d["source_path"]: d for d in kb_chunks.list_documents(db_session)}
    assert docs["A.md"]["chunks"] == 2
    assert docs["B.md"]["chunks"] == 1
    assert docs["A.md"]["module"] == "必修一"
    assert docs["A.md"]["disabled_chunks"] == 0


def test_disable_marks_chunk_and_documents(db_session):
    kb_chunks.set_chunk_disabled(db_session, "c1", True, operator="A1")
    assert "c1" in kb_chunks.disabled_chunk_ids(db_session)

    docs = {d["source_path"]: d for d in kb_chunks.list_documents(db_session)}
    assert docs["A.md"]["disabled_chunks"] == 1

    chunks = kb_chunks.list_chunks("A.md", db_session)
    by_id = {c["chunk_id"]: c for c in chunks}
    assert by_id["c1"]["disabled"] is True
    assert by_id["c2"]["disabled"] is False

    # 恢复
    kb_chunks.set_chunk_disabled(db_session, "c1", False, operator="A1")
    assert "c1" not in kb_chunks.disabled_chunk_ids(db_session)


def test_disabled_chunk_is_excluded_from_retrieval(db_session):
    """核心语义：被禁用的块不能再出现在资料里（否则开关就是假的）。"""
    hits = [
        ("c1", {"source_path": "A.md", "section_no": 1, "body": "第一块", "title_path": "A"},
         "A 第一块", 0.10),
        ("c2", {"source_path": "A.md", "section_no": 2, "body": "第二块", "title_path": "A"},
         "A 第二块", 0.12),
        ("c3", {"source_path": "B.md", "section_no": 1, "body": "B 块", "title_path": "B"},
         "B 第一块", 0.20),
    ]
    col = FakeCollection(ROWS, query_hits=hits)

    out = build_kb_context("问题", collection=col, encoder=FakeEncoder(), graph_engine=None,
                           top_k=3, miss_threshold=0.5, weak_threshold=0.9,
                           exclude_ids={"c1"})
    ids = [h["id"] for h in out["hits"]]
    assert "c1" not in ids
    assert ids == ["c2", "c3"]
    assert "第一块" not in out["text"]


def test_all_excluded_becomes_miss(db_session):
    hits = [("c1", {"source_path": "A.md", "section_no": 1, "body": "第一块"}, "A", 0.1)]
    col = FakeCollection(ROWS, query_hits=hits)
    out = build_kb_context("问题", collection=col, encoder=FakeEncoder(), graph_engine=None,
                           top_k=2, miss_threshold=0.5, weak_threshold=0.9,
                           exclude_ids={"c1"})
    assert out["hits"] == []
    assert out["mode"] == "miss"
