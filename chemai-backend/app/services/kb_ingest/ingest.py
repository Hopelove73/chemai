"""摄取编排（T9）：扫描 → 校验 → 切块 → 指纹比对 → 嵌入 → Chroma 落库。

幂等：按文档比对指纹集合——全同则整篇跳过；有变则按 source_path 整篇先删
后插（防修订后孤儿块）。Chroma 无跨库事务，离线摄取可接受中间态。
交付隔离：document 存"标题路径+正文"（嵌入同构文本），正文另存 metadata.body。
"""
import json
import logging
import time
from pathlib import Path

from .chunker import parse_doc
from .misconceptions import parse_misconceptions
from .validator import validate_doc

logger = logging.getLogger(__name__)

COLLECTION_NAME = "kb_chunks"


def ingest_directory(kb_dir, collection, embedder, known_codes: set,
                     module: str = "", path_prefix: str = "") -> dict:
    """摄取目录下全部 .md，返回报告。collection 由调用方给（CLI 传真实库，
    测试传内存实例）。

    path_prefix：写入 chroma source_path 的目录前缀（如 '必修一'）。source_path
    是检索结果的"身份"（评测期望、前端来源展示都读它）——多册入库后同名文档
    靠前缀区分，不能只存文件名。指纹仍按完整 source_path 计算，前缀变化 =
    全量重摄（一次性成本，换来身份稳定）。
    """
    kb_dir = Path(kb_dir)
    started = time.time()
    report = {"docs": 0, "chunks": 0, "inserted_docs": 0, "skipped_docs": 0,
              "failed": [], "durationMs": 0}
    files = sorted(kb_dir.rglob("*.md"))
    for f in files:
        rel = f"{path_prefix}/{f.relative_to(kb_dir).as_posix()}" if path_prefix \
            else f.relative_to(kb_dir).as_posix()
        report["docs"] += 1
        try:
            markdown = f.read_text(encoding="utf-8")
            meta = validate_doc(rel, markdown, known_codes)
            meta["misconceptions"] = parse_misconceptions(markdown)
            doc = parse_doc(rel, markdown, meta)
        except Exception as e:
            report["failed"].append({"source_path": rel, "stage": "validate",
                                     "message": str(e)})
            logger.warning("摄取校验失败 %s: %s", rel, e)
            continue
        try:
            n = _sync_doc(collection, embedder, doc, module=module or meta.get("module", ""))
            if n == 0:
                report["skipped_docs"] += 1
            else:
                report["inserted_docs"] += 1
                report["chunks"] += n
            if n == 0:
                report["chunks"] += _stored_count(collection, rel)
        except Exception as e:
            report["failed"].append({"source_path": rel, "stage": "write",
                                     "message": str(e)})
            logger.exception("摄取落库失败 %s: %s", rel, e)
    report["durationMs"] = int((time.time() - started) * 1000)
    return report


def _doc_fingerprints(collection, source_path: str) -> set:
    got = collection.get(where={"source_path": source_path},
                         include=["metadatas"])
    return {m.get("fingerprint") for m in (got.get("metadatas") or [])}


def _stored_count(collection, source_path: str) -> int:
    return len(collection.get(where={"source_path": source_path})["ids"])


def _sync_doc(collection, embedder, doc, module: str) -> int:
    """返回本次实际写入的块数；0 = 跳过（指纹全同）。

    文档入库同时 upsert kb_frontmatter（每个 kp_id 一行，equations/prerequisites
    来自 frontmatter）——图增强（kb_context）读这张表。跳过分支也执行：文档
    修订只改正文时指纹变了才会进来，但 equations 变了指纹未必变（frontmatter
    不参与切块），所以 upsert 放在指纹判断之前。
    """
    _upsert_frontmatter(doc)
    new_fps = {c.content_fingerprint for c in doc.chunks}
    if _doc_fingerprints(collection, doc.source_path) == new_fps:
        return 0
    collection.delete(where={"source_path": doc.source_path})
    texts = [f"{c.title_path}\n{c.body}" for c in doc.chunks]
    vectors = embedder.embed(texts)
    collection.upsert(
        ids=[f"{doc.source_path}::{c.chunk_no:03d}" for c in doc.chunks],
        documents=texts,
        embeddings=vectors,
        metadatas=[{
            "source_path": c.source_path, "title_path": c.title_path,
            "title": c.title, "section_title": c.section_title,
            "section_no": c.section_no, "part_no": c.part_no,
            "chunk_no": c.chunk_no, "fingerprint": c.content_fingerprint,
            "module": module, "doc_type": c.doc_type, "theme": c.theme,
            "kp_ids": json.dumps(c.kp_ids, ensure_ascii=False),
            "body": c.body,
        } for c in doc.chunks],
    )
    return len(doc.chunks)


def _upsert_frontmatter(doc) -> None:
    """kp_ids 里的每个 kp 一行 kb_frontmatter（equations/prerequisites 同源）。"""
    from app.models.database import get_session_factory, KBFrontmatter
    if not doc.meta.get("kp_ids"):
        return
    db = get_session_factory()()
    try:
        for kp in doc.meta["kp_ids"]:
            row = db.query(KBFrontmatter).filter_by(kp_id=kp).first()
            values = {"module": doc.meta.get("module", ""),
                      "theme": doc.meta.get("theme", ""),
                      "equations_json": json.dumps(doc.meta.get("equations", []),
                                                   ensure_ascii=False),
                      "prerequisites_json": json.dumps(doc.meta.get("prerequisites", []),
                                                       ensure_ascii=False),
                      "misconceptions_json": json.dumps(doc.meta.get("misconceptions") or [],
                                                        ensure_ascii=False),
                      "source_doc": doc.source_doc if hasattr(doc, "source_doc") else doc.source_path}
            if row is None:
                db.add(KBFrontmatter(kp_id=kp, **values))
            else:
                for k, v in values.items():
                    setattr(row, k, v)
        db.commit()
    except Exception as e:
        db.rollback()
        logger.warning("kb_frontmatter upsert 失败 %s: %s", doc.source_path, e)
    finally:
        db.close()
