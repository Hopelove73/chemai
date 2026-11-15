"""KB 文档与分块集中管理（球球式 docs + chunks 管理的对应物）。

数据事实：
  · 正文真源是 `content/kb/**/*.md`；chroma 的 `kb_chunks` 是派生索引（当下 160 文档 / 812 块）。
  · 因此"禁用某块"不能写进 chroma metadata（重摄取会覆盖），落 `kb_chunk_overrides`，
    检索时按 id 排除。

本模块只做读与禁用开关；改正文/重摄取仍走既有 KB 流程（编辑 md → 触发摄取）。
"""
import threading
import time
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.database import KbChunkOverride

_TTL_SECONDS = 30.0
_disabled_cache: set[str] = set()
_disabled_at: float = 0.0
_guard = threading.Lock()


def _collection():
    import chromadb
    from chromadb.config import Settings as ChromaSettings
    from app.core.config import settings
    from app.services.kb_jobs import active_collection_name, DEFAULT_COLLECTION, resolve_kb_collection
    from app.models.database import get_session_factory

    client = chromadb.PersistentClient(
        path=settings.CHROMA_DB_PATH,
        settings=ChromaSettings(anonymized_telemetry=False),
    )
    s = get_session_factory()()
    try:
        name = active_collection_name(s)
        return resolve_kb_collection(s, client) or client.get_collection(name)
    except Exception:
        try:
            return client.get_collection(DEFAULT_COLLECTION)
        except Exception:
            return None
    finally:
        s.close()


def disabled_chunk_ids(db: Session | None = None) -> set[str]:
    """当前禁用的分块 id（30s 缓存；写路径会 invalidate）。"""
    global _disabled_cache, _disabled_at
    now = time.monotonic()
    with _guard:
        if _disabled_cache and now - _disabled_at < _TTL_SECONDS:
            return set(_disabled_cache)
    own = db
    close = False
    if own is None:
        from app.models.database import get_session_factory
        own = get_session_factory()()
        close = True
    try:
        ids = {r.chunk_id for r in own.query(KbChunkOverride).filter(KbChunkOverride.disabled.is_(True)).all()}
    finally:
        if close:
            own.close()
    with _guard:
        _disabled_cache = ids
        _disabled_at = now
    return set(ids)


def invalidate() -> None:
    global _disabled_cache, _disabled_at
    with _guard:
        _disabled_cache = set()
        _disabled_at = 0.0


def list_documents(db: Session | None = None) -> list[dict]:
    """按 source_path 聚合：每份文档的分块数、模块、主题、标题。"""
    col = _collection()
    if col is None:
        return []
    metas = col.get(include=["metadatas"])["metadatas"] or []
    disabled = disabled_chunk_ids(db)
    ids = col.get(include=[])["ids"] or []
    docs: dict[str, dict] = {}
    for cid, meta in zip(ids, metas):
        m = meta or {}
        path = m.get("source_path") or "?"
        d = docs.setdefault(path, {
            "source_path": path,
            "title": m.get("title") or path,
            "module": m.get("module") or "",
            "theme": m.get("theme") or "",
            "doc_type": m.get("doc_type") or "",
            "chunks": 0,
            "disabled_chunks": 0,
        })
        d["chunks"] += 1
        if cid in disabled:
            d["disabled_chunks"] += 1
    out = sorted(docs.values(), key=lambda x: (x["module"], x["source_path"]))
    return out


def list_chunks(source_path: str, db: Session | None = None) -> list[dict]:
    """某文档的全部分块（按 chunk_no 排序），带正文与知识点、禁用标记。"""
    col = _collection()
    if col is None:
        return []
    res = col.get(where={"source_path": source_path},
                  include=["metadatas", "documents"])
    disabled = disabled_chunk_ids(db)
    out = []
    for cid, meta, doc in zip(res["ids"] or [], res["metadatas"] or [], res["documents"] or []):
        m = meta or {}
        out.append({
            "chunk_id": cid,
            "chunk_no": m.get("chunk_no"),
            "section_no": m.get("section_no"),
            "section_title": m.get("section_title") or "",
            "title_path": m.get("title_path") or "",
            "kp_ids": m.get("kp_ids") or "[]",
            "body": m.get("body") or doc or "",
            "disabled": cid in disabled,
        })
    out.sort(key=lambda x: (x["chunk_no"] or 0, x["section_no"] or 0))
    return out


def set_chunk_disabled(db: Session, chunk_id: str, disabled: bool, operator: str = "") -> None:
    row = db.get(KbChunkOverride, chunk_id)
    if row is None:
        row = KbChunkOverride(chunk_id=chunk_id)
        db.add(row)
    row.disabled = disabled
    row.updated_by = operator
    row.updated_at = datetime.utcnow()
    db.commit()
    invalidate()
