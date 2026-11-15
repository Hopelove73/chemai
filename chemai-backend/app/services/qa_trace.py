"""问答证据链落库（rag-observability）。

设计纪律：
  · **异步 + 失败静默**：观测绝不能拖垮回答（写失败只落服务日志）。
  · **快照语义**：hits 存块 id + 分数 + 前 200 字，块之后被删改也能事后复盘。
  · 开关 `QA_TRACE_ENABLED`（默认开）：出问题可一键停写。
"""
import logging
import os
import threading
from datetime import datetime
from uuid import uuid4

logger = logging.getLogger(__name__)


def enabled() -> bool:
    return os.getenv("QA_TRACE_ENABLED", "1") != "0"


def _summarize_hits(kb_out: dict) -> list[dict]:
    out = []
    for h in (kb_out.get("hits") or []):
        dist = h.get("distance")
        out.append({
            "chunk_id": h.get("id"),
            "source_path": h.get("source_path"),
            "section_no": h.get("section_no"),
            "title_path": h.get("title_path"),
            "score": round(1 - float(dist), 4) if dist is not None else None,
            "from_graph": bool(h.get("from_graph")),
            "body_preview": (h.get("body") or "")[:200],
        })
    return out


def _write(trace_id, payload):
    from app.models.database import QaTrace, get_session_factory
    session = None
    try:
        session = get_session_factory()()
        session.add(QaTrace(trace_id=trace_id, created_at=datetime.utcnow(), **payload))
        session.commit()
    except Exception:
        logger.warning("qa trace 写入失败（不影响回答）", exc_info=True)
    finally:
        if session is not None:
            session.close()


def record_trace(*, question: str, answer: str, kb_out: dict, model_name: str,
                 latency_ms: int, user_id: str = "", role: str = "student",
                 conversation_id: str = "", index_version: str = "") -> str | None:
    """异步写一条 trace；返回 trace_id（关闭或异常时返回 None）。"""
    if not enabled():
        return None
    try:
        trace_id = f"tr_{uuid4().hex[:16]}"
        hits = _summarize_hits(kb_out)
        payload = {
            "user_id": user_id or None,
            "role": role or None,
            "conversation_id": conversation_id or None,
            "question": question,
            "retrieval_config": kb_out.get("config") or None,
            "hits": hits,
            "zero_hit": len(hits) == 0,
            "top1_score": hits[0]["score"] if hits else None,
            "answer": answer,
            "refusal_flag": kb_out.get("mode") == "miss",
            "latency_ms": int(latency_ms) if latency_ms is not None else None,
            "model_name": model_name or None,
            "kb_index_version": index_version or None,
        }
        threading.Thread(target=_write, args=(trace_id, payload), daemon=True).start()
        return trace_id
    except Exception:
        logger.warning("qa trace 构造失败（不影响回答）", exc_info=True)
        return None
