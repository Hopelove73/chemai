"""KB-T9 摄取管线（seam）：校验拒绝 + 指纹幂等 + 报告 + KB2b 易错三元提取。

Embedder 用 FakeEmbedder（确定性伪向量，--fake 同款）；Chroma 用内存实例。
kb_frontmatter 的 upsert 走进程单例会话工厂——不隔离会把测试行写进真实
chemai.db（实测发生过：M1-T2-KP06 的 source_doc 被测试文档覆盖），
这里 autouse 把工厂指到内存库。
"""
import json

import chromadb
import pytest
from pathlib import Path
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.models.database import Base
from app.services.kb_ingest.embed import FakeEmbedder
from app.services.kb_ingest.ingest import ingest_directory
from app.services.kb_ingest.validator import validate_doc


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch):
    """把 get_session_factory 的单例重置到本测试的内存库。"""
    import app.models.database as db_mod
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    monkeypatch.setattr(db_mod, "_default_session_factory", factory, raising=False)
    monkeypatch.setattr(db_mod, "_default_engine", engine, raising=False)
    yield engine
    engine.dispose()

GOOD = """---
title: {title}
doc_type: concept
module: 必修一
theme: T2 常见的无机物及其应用
kp_ids: [M1-T2-KP06]
source: 测试
tags: [t]
equations: []
prerequisites: []
---

# {title}

## 是什么

{body}
"""


@pytest.fixture()
def kb_dir(tmp_path):
    d = tmp_path / "kb"
    (d / "必修一").mkdir(parents=True)
    (d / "必修一" / "甲文档.md").write_text(
        GOOD.format(title="甲文档", body="甲的内容。" * 10), encoding="utf-8")
    (d / "必修一" / "乙文档.md").write_text(
        GOOD.format(title="乙文档", body="乙的内容。" * 10), encoding="utf-8")
    return d


@pytest.fixture()
def collection():
    client = chromadb.EphemeralClient(
        settings=chromadb.config.Settings(anonymized_telemetry=False))
    yield client.get_or_create_collection("kb_test")
    client.delete_collection("kb_test")


def test_validate_rejects_missing_field(kb_dir):
    text = GOOD.format(title="缺字段", body="x").replace("source: 测试\n", "")
    with pytest.raises(Exception, match="source"):
        validate_doc("必修一/缺字段.md", text, known_codes={"M1-T2-KP06"})


def test_validate_rejects_unknown_kp_ref(kb_dir):
    text = GOOD.format(title="坏引用", body="x").replace(
        "kp_ids: [M1-T2-KP06]", "kp_ids: [M1-T2-KP99]")
    with pytest.raises(Exception, match="M1-T2-KP99"):
        validate_doc("必修一/坏引用.md", text, known_codes={"M1-T2-KP06"})


def test_validate_rejects_self_prerequisite(kb_dir):
    text = GOOD.format(title="自指", body="x").replace(
        "prerequisites: []", "prerequisites: [M1-T2-KP06]")
    with pytest.raises(Exception, match="自身"):
        validate_doc("必修一/自指.md", text, known_codes={"M1-T2-KP06"})


def test_ingest_writes_chunks_and_reports(kb_dir, collection):
    report = ingest_directory(kb_dir, collection=collection,
                              embedder=FakeEmbedder(dim=16),
                              known_codes={"M1-T2-KP06"})
    assert report["docs"] == 2 and report["failed"] == []
    assert report["chunks"] >= 2
    got = collection.get(include=["metadatas", "documents"])
    assert got["ids"], "向量必须落库"
    meta = got["metadatas"][0]
    assert meta["module"] == "必修一" and meta["source_path"]
    assert json.loads(meta["kp_ids"]) == ["M1-T2-KP06"]
    assert "甲的内容" in "".join(got["documents"]) or \
           "乙的内容" in "".join(got["documents"])


def test_ingest_idempotent_rerun_all_skipped(kb_dir, collection):
    ingest_directory(kb_dir, collection=collection,
                     embedder=FakeEmbedder(dim=16), known_codes={"M1-T2-KP06"})
    report2 = ingest_directory(kb_dir, collection=collection,
                               embedder=FakeEmbedder(dim=16),
                               known_codes={"M1-T2-KP06"})
    assert report2["skipped_docs"] == 2 and report2["inserted_docs"] == 0
    assert collection.count() == report2["chunks"], "重跑不得重复落向量"


def test_ingest_replaces_revised_doc(kb_dir, collection):
    ingest_directory(kb_dir, collection=collection,
                     embedder=FakeEmbedder(dim=16), known_codes={"M1-T2-KP06"})
    before = collection.count()
    # 修订甲文档：正文变长 → 指纹变 → 整篇替换
    (kb_dir / "必修一" / "甲文档.md").write_text(
        GOOD.format(title="甲文档", body="甲的内容改版。" * 30), encoding="utf-8")
    report = ingest_directory(kb_dir, collection=collection,
                              embedder=FakeEmbedder(dim=16),
                              known_codes={"M1-T2-KP06"})
    assert report["inserted_docs"] == 1 and report["skipped_docs"] == 1
    revised = collection.get(where={"source_path": "必修一/甲文档.md"},
                             include=["documents"])["documents"]
    assert revised and "改版" in revised[0], "旧块必须被替换掉"
    assert collection.count() >= before  # 总量不减


def test_ingest_validates_fresh_docs_against_dictionary(kb_dir, collection):
    """新鲜文档必须用最新词典校验——手改 kp 引用要在摄取时当场拒。"""
    (kb_dir / "必修一" / "丙文档.md").write_text(
        GOOD.format(title="丙文档", body="丙。").replace(
            "kp_ids: [M1-T2-KP06]", "kp_ids: [不存在的码]"), encoding="utf-8")
    report = ingest_directory(kb_dir, collection=collection,
                              embedder=FakeEmbedder(dim=16),
                              known_codes={"M1-T2-KP06"})
    assert report["failed"] and report["failed"][0]["stage"] == "validate"
    assert "丙文档" in report["failed"][0]["source_path"]


MISCONCEPTIONS_DOC = """---
title: 易错文档
doc_type: concept
module: 必修一
theme: T2 常见的无机物及其应用
kp_ids: [M1-T2-KP07]
source: 测试
tags: [t]
equations: []
prerequisites: []
---

# 易错文档

## 是什么

正文。

## 常见易错点

- **"氯水就是液态氯"**：错。液氯是纯净物；氯水是混合物。错因是名字像就当一样。
- **"干燥的 Cl₂ 有漂白性"**：错。漂白的是 HClO；无水则无 HClO。
- **"HClO 是强酸"**：不严谨。HClO 是弱酸但强氧化性。
- 这行不是粗体格式，应当被跳过。
"""


def test_ingest_extracts_misconceptions(kb_dir, collection, isolated_db):
    """KB2b：摄取时从「常见易错点」提取三元并存 kb_frontmatter.misconceptions_json。"""
    from app.models.database import KBFrontmatter
    (kb_dir / "必修一" / "易错文档.md").write_text(MISCONCEPTIONS_DOC, encoding="utf-8")
    report = ingest_directory(kb_dir, collection=collection,
                              embedder=FakeEmbedder(dim=16),
                              known_codes={"M1-T2-KP06", "M1-T2-KP07"})
    assert report["failed"] == []
    Session = sessionmaker(bind=isolated_db)
    with Session() as db:
        row = db.query(KBFrontmatter).filter_by(kp_id="M1-T2-KP07").one()
    mis = json.loads(row.misconceptions_json)
    assert len(mis) == 3, "三条粗体条目都要提取，非格式行跳过"
    assert mis[0] == {"wrong": "氯水就是液态氯",
                      "right": "液氯是纯净物；氯水是混合物",
                      "why": "名字像就当一样"}


def test_misconceptions_parse_failures_degrade_to_empty():
    """无小节/空文档/畸形输入 → 空列表，绝不抛错（解析失败不阻塞入库）。"""
    from app.services.kb_ingest.misconceptions import parse_misconceptions
    assert parse_misconceptions("没有任何小节的正文") == []
    assert parse_misconceptions("") == []
    assert parse_misconceptions(None) == []
    assert parse_misconceptions("## 常见易错点\n\n- 没有粗体的行\n") == []
