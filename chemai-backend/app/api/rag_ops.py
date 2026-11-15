"""RAG 运营三件套端点（问答日志 / 召回测试 / 评测回归）+ 角色只读矩阵。

门：读 `knowledge:read`（教师可读问答列表？不 —— 问答日志含学生提问，统一
`knowledge:update`，只有 admin/超管）；召回测试与评测写同理。
角色矩阵单独在 `roles.py`。
"""
import threading
import time
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker
from app.models.database import (
    GoldenQuestion, OperationLog, QaTrace, RagEvalRun, RagRetrievalTest, get_db,
)

router = APIRouter(prefix="/api/rag", tags=["RAG 运营"])

LOW_SCORE_THRESHOLD = 0.5   # top1_score（1-distance）低于此值视为低分命中


# ─────────────────────────────────────────────────────────────
# 问答日志
# ─────────────────────────────────────────────────────────────

def _trace_row(t: QaTrace, with_answer: bool = False) -> dict:
    row = {
        "trace_id": t.trace_id,
        "user_id": t.user_id,
        "role": t.role,
        "conversation_id": t.conversation_id,
        "question": t.question,
        "hits_count": len(t.hits or []),
        "zero_hit": bool(t.zero_hit),
        "top1_score": t.top1_score,
        "refusal_flag": bool(t.refusal_flag),
        "latency_ms": t.latency_ms,
        "model_name": t.model_name,
        "feedback": t.feedback or "none",
        "ops_label": t.ops_label,
        "bad_reason": t.bad_reason,
        "created_at": t.created_at.isoformat() if t.created_at else None,
    }
    if with_answer:
        row.update({
            "answer": t.answer,
            "hits": t.hits or [],
            "retrieval_config": t.retrieval_config,
            "kb_index_version": t.kb_index_version,
            "feedback_text": t.feedback_text,
        })
    return row


@router.get("/traces")
async def list_traces(
    days: int = 7,
    refusal: Optional[str] = None,     # yes / no
    zero_hit: Optional[str] = None,
    low_score: Optional[str] = None,
    feedback: Optional[str] = None,    # down
    student_id: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "update")),
):
    q = db.query(QaTrace).filter(
        QaTrace.created_at >= datetime.utcnow() - timedelta(days=max(1, min(days, 180))))
    if refusal == "yes":
        q = q.filter(QaTrace.refusal_flag.is_(True))
    elif refusal == "no":
        q = q.filter(QaTrace.refusal_flag.is_(False))
    if zero_hit == "yes":
        q = q.filter(QaTrace.zero_hit.is_(True))
    if low_score == "yes":
        q = q.filter(QaTrace.top1_score.isnot(None), QaTrace.top1_score < LOW_SCORE_THRESHOLD)
    if feedback:
        q = q.filter(QaTrace.feedback == feedback)
    if student_id:
        q = q.filter(QaTrace.user_id == student_id)
    total = q.count()
    rows = q.order_by(QaTrace.created_at.desc()).offset(max(0, offset)).limit(
        max(1, min(limit, 200))).all()
    return {"success": True, "total": total, "traces": [_trace_row(t) for t in rows]}


@router.get("/traces/{trace_id}")
async def get_trace(
    trace_id: str,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "update")),
):
    t = db.get(QaTrace, trace_id)
    if t is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    return {"success": True, "trace": _trace_row(t, with_answer=True)}


class FeedbackBody(BaseModel):
    feedback: str            # up / down / none
    text: Optional[str] = None


@router.post("/traces/{trace_id}/feedback")
async def set_feedback(
    trace_id: str,
    body: FeedbackBody,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "update")),
):
    if body.feedback not in ("up", "down", "none"):
        raise HTTPException(status_code=400, detail="feedback 只能是 up/down/none")
    t = db.get(QaTrace, trace_id)
    if t is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    t.feedback = body.feedback
    t.feedback_text = body.text
    db.commit()
    return {"success": True, "trace_id": trace_id, "feedback": t.feedback}


class LabelBody(BaseModel):
    label: str               # good / bad
    bad_reason: Optional[str] = None
    corrected_answer: Optional[str] = None


BAD_REASONS = ("检索错", "chunk差", "缺知识", "幻觉", "不完整", "超纲", "误拒")


@router.post("/traces/{trace_id}/label")
async def label_trace(
    trace_id: str,
    body: LabelBody,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("knowledge", "update")),
):
    if body.label not in ("good", "bad"):
        raise HTTPException(status_code=400, detail="label 只能是 good/bad")
    if body.label == "bad" and body.bad_reason not in BAD_REASONS:
        raise HTTPException(status_code=400, detail=f"bad_reason 必须是 {BAD_REASONS} 之一")
    t = db.get(QaTrace, trace_id)
    if t is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    t.ops_label = body.label
    t.bad_reason = body.bad_reason if body.label == "bad" else None
    if body.corrected_answer:
        t.answer = body.corrected_answer
    db.add(OperationLog(user_id=current_user.get("user_id"), action="qa_trace_label",
                        target_type="qa_trace", target_id=trace_id,
                        detail={"label": body.label, "bad_reason": body.bad_reason}))
    db.commit()
    return {"success": True, "trace_id": trace_id}


@router.post("/traces/{trace_id}/to-golden")
async def trace_to_golden(
    trace_id: str,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("knowledge", "update")),
):
    """把一条问答回流成金标问题（期望块 = 当时命中的块）。"""
    t = db.get(QaTrace, trace_id)
    if t is None:
        raise HTTPException(status_code=404, detail="记录不存在")
    expected = [{"chunk_id": h.get("chunk_id"),
                 "source_path": h.get("source_path"),
                 "section_no": h.get("section_no")}
                for h in (t.hits or [])]
    g = GoldenQuestion(
        question=t.question,
        expected_chunks=expected,
        match_mode="any",
        status="draft",
        source="trace",
        source_trace_id=trace_id,
    )
    db.add(g)
    db.commit()
    return {"success": True, "golden_id": g.id, "expected_chunks": len(expected)}


@router.get("/metrics")
async def rag_metrics(
    days: int = 7,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "update")),
):
    since = datetime.utcnow() - timedelta(days=max(1, min(days, 180)))
    base = db.query(QaTrace).filter(QaTrace.created_at >= since,
                                    QaTrace.role != "retrieval_test")
    total = base.count()
    zero_hit = base.filter(QaTrace.zero_hit.is_(True)).count()
    refusal = base.filter(QaTrace.refusal_flag.is_(True)).count()
    down = base.filter(QaTrace.feedback == "down").count()
    low = base.filter(QaTrace.top1_score.isnot(None),
                      QaTrace.top1_score < LOW_SCORE_THRESHOLD).count()
    hit = max(0, total - zero_hit)
    return {
        "success": True,
        "window_days": days,
        "total": total,
        "hit_rate": round(hit / total, 4) if total else None,
        "zero_hit_rate": round(zero_hit / total, 4) if total else None,
        "refusal_rate": round(refusal / total, 4) if total else None,
        "negative_feedback_rate": round(down / total, 4) if total else None,
        "low_score_rate": round(low / hit, 4) if hit else None,
        "low_score_threshold": LOW_SCORE_THRESHOLD,
    }


# ─────────────────────────────────────────────────────────────
# 召回测试
# ─────────────────────────────────────────────────────────────

class RetrievalTestBody(BaseModel):
    query: str
    top_k: int = 4
    threshold: float = 0.5      # 弱命中线（越大越宽松？此处用于 weak 判定）
    graph_enabled: bool = True


def _kb_runtime():
    """与线上同一条检索管线（tutoring 的资源装配）。"""
    from agent.tools.tutoring import _kb_resources
    from app.services.kb_chunks import disabled_chunk_ids
    res = _kb_resources()
    if not res.get("collection"):
        raise HTTPException(status_code=503, detail="向量库不可用（chroma 未就绪或未摄取）")
    res["exclude_ids"] = disabled_chunk_ids()
    return res


@router.post("/retrieval-test")
async def retrieval_test(
    body: RetrievalTestBody,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("knowledge", "update")),
):
    """临时参数下跑同一条检索管线；**不落库、不改默认配置、不写 trace**。"""
    from app.services.kb_context import build_kb_context
    if not body.query.strip():
        raise HTTPException(status_code=400, detail="query 不能为空")
    if not 1 <= body.top_k <= 20:
        raise HTTPException(status_code=400, detail="top_k 取值范围 1–20")

    started = time.monotonic()
    try:
        out = build_kb_context(
            body.query,
            **_kb_runtime(),
            top_k=body.top_k,
            miss_threshold=body.threshold,
            weak_threshold=body.threshold,
            graph_enabled=body.graph_enabled,
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"检索失败：{e}")
    elapsed = int((time.monotonic() - started) * 1000)
    hits = [{
        "chunk_id": h.get("id"),
        "source_path": h.get("source_path"),
        "section_no": h.get("section_no"),
        "title_path": h.get("title_path"),
        "body": (h.get("body") or "")[:400],
        "score": round(1 - float(h["distance"]), 4) if h.get("distance") is not None else None,
        "from_graph": bool(h.get("from_graph")),
    } for h in (out.get("hits") or [])]
    # 留历史：进页就能看到最近跑过什么（失败不阻断本次结果）
    try:
        db.add(RagRetrievalTest(
            query=body.query.strip(), top_k=body.top_k, threshold=body.threshold,
            graph_enabled=body.graph_enabled, mode=out.get("mode"),
            hits_count=len(hits), top1_score=hits[0]["score"] if hits else None,
            elapsed_ms=elapsed, created_by=current_user.get("user_id", ""),
        ))
        db.commit()
    except Exception:
        db.rollback()

    return {"success": True, "mode": out.get("mode"), "elapsed_ms": elapsed,
            "applied_config": {"top_k": body.top_k, "threshold": body.threshold,
                               "graph_enabled": body.graph_enabled},
            "default_config": {"top_k": 4, "threshold": None, "graph_enabled": True},
            "hits": hits}


class BadCaseBody(BaseModel):
    query: str
    hits: list = []
    reason: str = "检索错"


@router.post("/retrieval-test/bad-case")
async def retrieval_bad_case(
    body: BadCaseBody,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "update")),
):
    """把一次召回测试的结果记为坏案例（写一条 trace，source 只用于筛选）。

    **不计入线上指标**：metrics 只统计真实问答；这里 role 记 'retrieval_test'。
    """
    hits = body.hits or []
    top1 = hits[0].get("score") if hits else None
    db.add(QaTrace(
        trace_id=f"rt_{uuid4().hex[:14]}",
        user_id="retrieval_test", role="retrieval_test",
        question=body.query, hits=hits, zero_hit=len(hits) == 0,
        top1_score=top1, answer=None, refusal_flag=False,
        ops_label="bad", bad_reason=body.reason if body.reason in BAD_REASONS else "检索错",
        feedback="none",
    ))
    db.commit()
    return {"success": True}


@router.get("/retrieval-tests")
async def list_retrieval_tests(
    limit: int = 10,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "update")),
):
    """最近 N 次召回测试（进页展示用）。"""
    rows = (db.query(RagRetrievalTest)
            .order_by(RagRetrievalTest.created_at.desc())
            .limit(max(1, min(limit, 50))).all())
    return {"success": True, "tests": [{
        "id": r.id, "query": r.query, "top_k": r.top_k, "threshold": r.threshold,
        "graph_enabled": bool(r.graph_enabled), "mode": r.mode,
        "hits_count": r.hits_count, "top1_score": r.top1_score,
        "elapsed_ms": r.elapsed_ms, "created_by": r.created_by,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    } for r in rows]}


# ─────────────────────────────────────────────────────────────
# 金标集
# ─────────────────────────────────────────────────────────────

class GoldenBody(BaseModel):
    question: str
    expected_chunks: list = []
    expected_answer: Optional[str] = None
    match_mode: str = "any"      # any / all
    tags: Optional[str] = None
    status: str = "draft"


def _golden_row(g: GoldenQuestion) -> dict:
    return {
        "id": g.id, "question": g.question,
        "expected_chunks": g.expected_chunks or [],
        "expected_answer": g.expected_answer,
        "match_mode": g.match_mode, "tags": g.tags, "status": g.status,
        "source": g.source, "source_trace_id": g.source_trace_id,
        "created_at": g.created_at.isoformat() if g.created_at else None,
    }


@router.get("/golden-questions")
async def list_golden(
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "read")),
):
    rows = db.query(GoldenQuestion).order_by(GoldenQuestion.id.desc()).all()
    return {"success": True, "total": len(rows), "questions": [_golden_row(g) for g in rows]}


@router.post("/golden-questions")
async def create_golden(
    body: GoldenBody,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "update")),
):
    if body.match_mode not in ("any", "all"):
        raise HTTPException(status_code=400, detail="match_mode 只能是 any/all")
    if not body.question.strip():
        raise HTTPException(status_code=400, detail="问题不能为空")
    g = GoldenQuestion(question=body.question.strip(), expected_chunks=body.expected_chunks,
                       expected_answer=body.expected_answer, match_mode=body.match_mode,
                       tags=body.tags, status=body.status, source="manual")
    db.add(g)
    db.commit()
    return {"success": True, "question": _golden_row(g)}


@router.put("/golden-questions/{gid}")
async def update_golden(
    gid: int,
    body: GoldenBody,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "update")),
):
    g = db.get(GoldenQuestion, gid)
    if g is None:
        raise HTTPException(status_code=404, detail="金标问题不存在")
    g.question = body.question.strip() or g.question
    g.expected_chunks = body.expected_chunks
    g.expected_answer = body.expected_answer
    g.match_mode = body.match_mode
    g.tags = body.tags
    g.status = body.status
    db.commit()
    return {"success": True, "question": _golden_row(g)}


@router.delete("/golden-questions/{gid}")
async def delete_golden(
    gid: int,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "update")),
):
    g = db.get(GoldenQuestion, gid)
    if g is None:
        raise HTTPException(status_code=404, detail="金标问题不存在")
    db.delete(g)
    db.commit()
    return {"success": True}


# ─────────────────────────────────────────────────────────────
# 评测运行（异步 job + 逐题结果 + baseline 对比）
# ─────────────────────────────────────────────────────────────

def _run_eval(run_id: str):
    from app.models.database import get_session_factory
    from app.services.kb_context import build_kb_context

    session = get_session_factory()()
    try:
        run = session.get(RagEvalRun, run_id)
        run.status = "running"
        session.commit()

        questions = session.query(GoldenQuestion).filter(
            GoldenQuestion.status == "active").all()
        if not questions:
            run.status = "failed"
            run.error = "没有 active 状态的金标问题"
            run.finished_at = datetime.utcnow()
            session.commit()
            return

        resources = _kb_runtime()
        # 评测要的是"检索到了什么"，所以阈值放到不可能 miss（2.0）
        items, passed, refusal_ok, refusal_total = [], 0, 0, 0
        for g in questions:
            try:
                out = build_kb_context(g.question, **resources, top_k=6,
                                       miss_threshold=2.0, weak_threshold=2.0)
                hits = out.get("hits") or []
            except Exception as e:  # 单题失败不拖垮整轮
                items.append({"id": g.id, "question": g.question, "error": str(e),
                              "passed": False})
                continue
            expected = g.expected_chunks or []
            exp_ids = [c.get("chunk_id") for c in expected if c.get("chunk_id")]

            # 严格按块 id 匹配：同文档同小节可能有多个块，回退匹配会把「没召回」
            # 误判成「召回」（实测：禁用某个金标的全部期望块后仍判通过）。
            # 重摄取后 id 变化会让金标失效（stale 自动标记尚未实现）——那是可见的
            # 失败，比悄悄判过要好。
            def _hit(cid):
                return any(h.get("id") == cid for h in hits)

            if not exp_ids:
                ok = False
            elif g.match_mode == "all":
                ok = all(_hit(c) for c in exp_ids)
            else:
                ok = any(_hit(c) for c in exp_ids)
            passed += 1 if ok else 0

            # 拒答正确率：金标标注"应拒答"（expected_chunks 为空）时应 miss
            expects_refusal = len(exp_ids) == 0
            if expects_refusal:
                refusal_total += 1
                if not hits:
                    refusal_ok += 1

            items.append({
                "id": g.id, "question": g.question, "passed": ok,
                "match_mode": g.match_mode,
                "expected": exp_ids,
                "hit_ids": [h.get("id") for h in hits],
                "hit_paths": [h.get("source_path") for h in hits],
            })

        run = session.get(RagEvalRun, run_id)
        run.status = "succeeded"
        run.metrics = {
            "total": len(questions),
            "passed": passed,
            "hit_rate": round(passed / len(questions), 4),
            "refusal_total": refusal_total,
            "refusal_correct": refusal_ok,
            "refusal_accuracy": round(refusal_ok / refusal_total, 4) if refusal_total else None,
        }
        run.items = items
        run.finished_at = datetime.utcnow()
        session.commit()
    except Exception as e:
        try:
            run = session.get(RagEvalRun, run_id)
            run.status = "failed"
            run.error = str(e)[:500]
            run.finished_at = datetime.utcnow()
            session.commit()
        except Exception:
            pass
    finally:
        session.close()


@router.post("/evals/run")
async def run_eval(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("knowledge", "update")),
):
    running = db.query(RagEvalRun).filter(
        RagEvalRun.status.in_(("queued", "running"))).first()
    if running:
        raise HTTPException(status_code=409, detail=f"已有评测在跑（{running.run_id}）")
    active = db.query(GoldenQuestion).filter(GoldenQuestion.status == "active").count()
    if active == 0:
        raise HTTPException(status_code=400, detail="没有 active 的金标问题，先勾选启用")

    run = RagEvalRun(run_id=f"ev_{uuid4().hex[:12]}", status="queued",
                     created_by=current_user.get("user_id", ""),
                     config_snapshot={"top_k": 6, "match_mode": "per-question"})
    db.add(run)
    db.commit()
    threading.Thread(target=_run_eval, args=(run.run_id,), daemon=True).start()
    return {"success": True, "run_id": run.run_id, "questions": active}


@router.get("/evals")
async def list_evals(
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "read")),
):
    rows = db.query(RagEvalRun).order_by(RagEvalRun.created_at.desc()).limit(20).all()
    return {"success": True, "runs": [{
        "run_id": r.run_id, "status": r.status, "metrics": r.metrics,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "error": r.error,
    } for r in rows]}


@router.get("/evals/{run_id}")
async def get_eval(
    run_id: str,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "read")),
):
    r = db.get(RagEvalRun, run_id)
    if r is None:
        raise HTTPException(status_code=404, detail="评测不存在")
    return {"success": True, "run": {
        "run_id": r.run_id, "status": r.status, "metrics": r.metrics,
        "items": r.items or [], "error": r.error,
        "config": r.config_snapshot,
        "created_at": r.created_at.isoformat() if r.created_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
    }}


@router.get("/eval-comparison")
async def compare_evals(
    baseline: str,
    candidate: str,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "read")),
):
    b = db.get(RagEvalRun, baseline)
    c = db.get(RagEvalRun, candidate)
    if b is None or c is None:
        raise HTTPException(status_code=404, detail="评测不存在")
    if b.status != "succeeded" or c.status != "succeeded":
        raise HTTPException(status_code=400, detail="两次评测都必须是 succeeded")

    bmap = {i["id"]: i for i in (b.items or []) if "id" in i}
    cmap = {i["id"]: i for i in (c.items or []) if "id" in i}
    new_fail, fixed, still_fail, still_pass = [], [], [], []
    for gid, bi in bmap.items():
        ci = cmap.get(gid)
        if ci is None:
            continue
        bp, cp = bool(bi.get("passed")), bool(ci.get("passed"))
        item = {"id": gid, "question": bi.get("question"),
                "baseline_hit_ids": bi.get("hit_ids"), "candidate_hit_ids": ci.get("hit_ids")}
        if bp and not cp:
            new_fail.append(item)
        elif not bp and cp:
            fixed.append(item)
        elif not bp and not cp:
            still_fail.append(item)
        else:
            still_pass.append(item)
    return {"success": True,
            "baseline": {"run_id": b.run_id, "metrics": b.metrics},
            "candidate": {"run_id": c.run_id, "metrics": c.metrics},
            "diff": {"new_fail": new_fail, "fixed": fixed,
                     "still_fail": still_fail, "still_pass_count": len(still_pass)}}
