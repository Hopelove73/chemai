"""KB 内容审校与状态（kb-review-ui）。

- `GET /api/kb/status`（knowledge:read）：推导式只读总览 —— 各册语料/提议规模、
  词典覆盖、kb_chunks 向量库存量。无副作用。
- `GET /api/kb/review-packages`（knowledge:read）：审校包列表（结构化 diff 概要）。
- `GET /api/kb/review-packages/detail`（knowledge:read）：单个审校包的结构化明细。
- `POST /api/kb/review-packages/approve`（knowledge:update）：批准落库 —— **复用
  `kb_graph_import.import_proposals`**（与 CLI 同一服务函数，语义零漂移）；
  状态机 pending→approved（同事务条件更新防并发双批）；写审计。
- `POST /api/kb/review-packages/reject`（knowledge:update）：驳回留痕。

审校包本体是 git 管理的 `content/kb/**/*.proposals.json`（纯语料）；状态存
`kb_review_records`（键 = 相对 content/kb 的路径），批准时记录文件指纹，
文件后来被改过会在列表里标注 `content_changed`。
"""
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker
from app.models.database import KBJob, KBReviewRecord, OperationLog, get_db
from app.services.kb_graph_import import import_proposals

router = APIRouter(prefix="/api/kb", tags=["KB内容治理"])

CONTENT_KB = Path(__file__).resolve().parents[2] / "content" / "kb"


def _safe_rel_path(rel: str) -> Path:
    """相对路径解析 + 越界防护：必须落在 content/kb 内且是 .proposals.json。"""
    if not rel or rel.strip() != rel or rel.startswith("/") or "\\" in rel or ".." in rel.split("/"):
        raise HTTPException(status_code=400, detail="非法的审校包路径")
    p = (CONTENT_KB / rel).resolve()
    if not str(p).startswith(str(CONTENT_KB.resolve())):
        raise HTTPException(status_code=400, detail="路径越出 content/kb")
    if p.suffix != ".json" or not p.name.endswith(".proposals.json"):
        raise HTTPException(status_code=400, detail="只接受 *.proposals.json 审校包")
    return p


def _load_known_codes() -> set:
    from app.services.kb_dictionary import load_all_dictionaries
    return {it["code"] for it in load_all_dictionaries(str(CONTENT_KB))["items"]}


def _fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _record_of(db: Session, rel: str) -> Optional[KBReviewRecord]:
    return db.get(KBReviewRecord, rel)


def _package_view(db: Session, abs_path: Path, rel: str) -> dict:
    """审校包结构化视图 + 状态单 + 指纹比对。解析失败返回结构化错误而不是 500。"""
    view = {"path": rel, "module": rel.split("/")[0] if "/" in rel else "", "status": "pending",
            "parse_error": None, "content_changed": False}
    try:
        data = json.loads(abs_path.read_text(encoding="utf-8"))
    except Exception as e:
        view["parse_error"] = str(e)
        return view
    edges = data.get("edges", [])
    view.update({
        "kp": data.get("kp", ""),
        "doc": data.get("doc", ""),
        "edge_total": len(edges),
        "prerequisite": sum(1 for e in edges if e.get("edge_type") == "prerequisite"),
        "relates_to": sum(1 for e in edges if e.get("edge_type") == "relates_to"),
    })
    rec = _record_of(db, rel)
    if rec:
        view["status"] = rec.status
        view["reviewer_id"] = rec.reviewer_id
        view["reviewed_at"] = rec.reviewed_at.isoformat() if rec.reviewed_at else None
        view["reason"] = rec.reason
        view["import_report"] = rec.import_report
        if rec.content_hash and rec.status == "approved":
            view["content_changed"] = _fingerprint(abs_path) != rec.content_hash
    return view


@router.get("/status")
async def kb_status(
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "read")),
):
    """推导式只读总览：各册语料/提议规模、词典覆盖、向量库存量。"""
    modules = []
    if CONTENT_KB.exists():
        for d in sorted(p for p in CONTENT_KB.iterdir() if p.is_dir() and p.name != "reference"):
            docs = [f for f in d.glob("*.md") if not f.name.startswith("_")]
            modules.append({
                "module": d.name,
                "docs": len(docs),
                "proposals": len(list(d.glob("*.proposals.json"))),
                "has_dictionary": (CONTENT_KB / f"kp_dictionary_m{d.name[-1]}.json").exists()
                if d.name.startswith("必修") or d.name.startswith("选择性必修") else None,
            })

    # 词典覆盖（load_all_dictionaries 合并多册；这里给各文件条目数）
    dictionaries = {f.name: len(json.loads(f.read_text(encoding="utf-8")).get("items", []))
                    for f in sorted(CONTENT_KB.glob("kp_dictionary_m*.json"))} if CONTENT_KB.exists() else {}

    # 向量库存量：按**活跃集合**（影子切换后指向新家）计数；不可用时如实降级 null
    kb_chunks = None
    active_name = None
    try:
        import chromadb
        from app.core.config import settings
        from app.services.kb_jobs import resolve_kb_collection
        client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH,
                                           settings=chromadb.config.Settings(anonymized_telemetry=False))
        collection = resolve_kb_collection(db, client)
        if collection is not None:
            kb_chunks = collection.count()
            active_name = collection.name
    except Exception:
        kb_chunks = None

    return {"success": True, "modules": modules, "dictionaries": dictionaries,
            "kb_chunks": kb_chunks, "active_collection": active_name}


@router.get("/review-packages")
async def list_review_packages(
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "read")),
):
    """审校包列表（按模块分组概要 + 审校状态）。"""
    items = []
    if CONTENT_KB.exists():
        for f in sorted(CONTENT_KB.rglob("*.proposals.json")):
            if "reference" in f.parts:
                continue
            items.append(_package_view(db, f, str(f.relative_to(CONTENT_KB)).replace("\\", "/")))
    return {"success": True, "total": len(items), "packages": items}


@router.get("/review-packages/detail")
async def review_package_detail(
    path: str,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "read")),
):
    """单个审校包的结构化明细（提议边逐条 + 落库记录）。"""
    abs_path = _safe_rel_path(path)
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="审校包不存在（可能已被移动或删除）")
    view = _package_view(db, abs_path, path)
    view["fingerprint"] = _fingerprint(abs_path)
    if view["parse_error"] is None:
        data = json.loads(abs_path.read_text(encoding="utf-8"))
        view["edges"] = [
            {"edge_type": e.get("edge_type"),
             "from_kp": e.get("from_kp"), "to_kp": e.get("to_kp"),
             "kp": e.get("kp"), "substance": e.get("substance"), "formula": e.get("formula"),
             "reason": e.get("reason", ""), "confidence": e.get("confidence")}
            for e in data.get("edges", [])
        ]
    return {"success": True, "package": view}


class ReviewAction(BaseModel):
    path: str
    reason: Optional[str] = None


@router.post("/review-packages/approve")
async def approve_review_package(
    action: ReviewAction,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("knowledge", "update")),
):
    """批准落库 —— 与 CLI 同一服务函数；状态机条件更新防并发双批；同事务写审计。"""
    abs_path = _safe_rel_path(action.path)
    rel = action.path
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="审校包不存在")

    rec = _record_of(db, rel)
    if rec and rec.status == "approved":
        raise HTTPException(status_code=409, detail="该审校包已批准落库，不可重复执行")
    if rec and rec.status == "rejected":
        raise HTTPException(status_code=409, detail="该审校包已驳回；如需重新处理请先清理驳回记录")

    report = import_proposals(db.get_bind(), str(abs_path), _load_known_codes(),
                              proposed_by=f"user:{current_user.get('user_id', '')}")

    fingerprint = _fingerprint(abs_path)
    now = __import__("datetime").datetime.utcnow()
    if rec is None:
        rec = KBReviewRecord(source_path=rel)
        db.add(rec)
    rec.status = "approved"
    rec.reviewer_id = current_user.get("user_id")
    rec.reason = None
    rec.content_hash = fingerprint
    rec.import_report = report
    rec.reviewed_at = now

    db.add(OperationLog(
        user_id=current_user.get("user_id"),
        action="kb_review_approve",
        target_type="kb_review_package",
        target_id=rel,
        detail={"report": report, "fingerprint": fingerprint},
    ))
    db.commit()
    return {"success": True, "message": "已批准并落库", "report": report}


@router.post("/review-packages/reject")
async def reject_review_package(
    action: ReviewAction,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("knowledge", "update")),
):
    """驳回留痕（操作者 + 理由）。已批准的包不可驳回（落库已成事实）。"""
    abs_path = _safe_rel_path(action.path)
    rec = _record_of(db, action.path)
    if rec and rec.status == "approved":
        raise HTTPException(status_code=409, detail="该审校包已批准落库，不可驳回")
    if not abs_path.exists():
        raise HTTPException(status_code=404, detail="审校包不存在")

    if rec is None:
        rec = KBReviewRecord(source_path=action.path)
        db.add(rec)
    rec.status = "rejected"
    rec.reviewer_id = current_user.get("user_id")
    rec.reason = (action.reason or "").strip() or None
    rec.reviewed_at = __import__("datetime").datetime.utcnow()

    db.add(OperationLog(
        user_id=current_user.get("user_id"),
        action="kb_review_reject",
        target_type="kb_review_package",
        target_id=action.path,
        detail={"reason": rec.reason},
    ))
    db.commit()
    return {"success": True, "message": "已驳回"}


# ── 文档与分块集中管理（KB docs/chunks）──────────────────────

@router.get("/documents")
async def list_kb_documents(
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "read")),
):
    """按文档聚合的分块概览：模块/主题/分块数/已禁用数。"""
    from app.services import kb_chunks
    docs = kb_chunks.list_documents(db)
    return {"success": True, "total": len(docs), "documents": docs}


@router.get("/documents/chunks")
async def list_kb_chunks(
    path: str,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "read")),
):
    """某份文档的全部分块（正文 / 小节 / 知识点 / 是否禁用）。"""
    from app.services import kb_chunks
    chunks = kb_chunks.list_chunks(path, db)
    if not chunks:
        raise HTTPException(status_code=404, detail="没有找到该文档的分块（可能尚未摄取）")
    return {"success": True, "path": path, "total": len(chunks), "chunks": chunks}


class ChunkStatusBody(BaseModel):
    disabled: bool


@router.post("/chunks/{chunk_id}/status")
async def set_kb_chunk_status(
    chunk_id: str,
    body: ChunkStatusBody,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("knowledge", "update")),
):
    """禁用/恢复一个分块。禁用后**检索立即不再命中**（kb_context 按 id 排除）。"""
    from app.services import kb_chunks
    kb_chunks.set_chunk_disabled(db, chunk_id, body.disabled,
                                 operator=current_user.get("user_id", ""))
    db.add(OperationLog(
        user_id=current_user.get("user_id"),
        action="kb_chunk_disable" if body.disabled else "kb_chunk_enable",
        target_type="kb_chunk", target_id=chunk_id,
        detail={"disabled": body.disabled},
    ))
    db.commit()
    return {"success": True, "chunk_id": chunk_id, "disabled": body.disabled}


# ── 摄取任务（kb-ingest-ui）─────────────────────────────────

@router.post("/jobs")
async def submit_ingest_job(
    body: dict,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("knowledge", "update")),
):
    """触发指定册摄取：立即返回 job_id（不阻塞等待嵌入完成）。

    运行时环境自检（design D4）：嵌入模型不可加载 → 结构化错误，不产生任务。
    同册并发触发 → 拒绝并给出进行中任务。动作写审计。
    """
    module = (body or {}).get("module", "")
    # 环境自检：嵌入器必须能在本进程构建（缺 EMBEDDING_CACHE_DIR/模型 → 明确报错）
    try:
        from app.services.vector_search import get_text_encoder
        get_text_encoder()
    except Exception as e:
        raise HTTPException(
            status_code=503,
            detail=f"嵌入环境不可用（缺 EMBEDDING_CACHE_DIR 或模型目录）：{e}")

    from app.services import kb_jobs
    try:
        job = kb_jobs.submit_job(db, module, current_user.get("user_id", ""))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except RuntimeError as e:
        running = (db.query(KBJob)
                   .filter(KBJob.module == module, KBJob.status.in_(("queued", "running")))
                   .order_by(KBJob.created_at.desc()).first())
        raise HTTPException(status_code=409, detail=f"{e}（进行中任务：{running.job_id if running else '?'}）")

    db.add(OperationLog(
        user_id=current_user.get("user_id"), action="kb_ingest_submit",
        target_type="kb_job", target_id=job.job_id, detail={"module": module}))
    db.commit()
    return {"success": True, "job_id": job.job_id, "module": module, "status": job.status}


@router.get("/jobs/{job_id}")
async def get_ingest_job(
    job_id: str,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("knowledge", "read")),
):
    """任务进度轮询（queued/running/succeeded/failed/interrupted + 进度/错误摘要）。"""
    job = db.get(KBJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {
        "success": True,
        "job": {
            "job_id": job.job_id, "module": job.module, "status": job.status,
            "progress": job.progress, "error": job.error,
            "created_by": job.created_by,
            "created_at": job.created_at.isoformat() if job.created_at else None,
            "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        },
    }


@router.post("/dictionary-sync")
async def dictionary_sync(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("knowledge", "update")),
):
    """词典同步（幂等秒级同步操作，不走异步任务）。"""
    from app.services import kb_jobs
    try:
        report = kb_jobs.sync_dictionary(db, current_user.get("user_id", ""))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"词典同步失败: {e}")
    return {"success": True, "report": report}


@router.post("/tag-map-sync")
async def tag_map_sync(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("knowledge", "update")),
):
    """标签映射导入（幂等秒级同步操作）。"""
    from app.services import kb_jobs
    try:
        report = kb_jobs.sync_tag_map(db, current_user.get("user_id", ""))
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"标签映射导入失败: {e}")
    return {"success": True, "report": report}


# ── 正文读写（content/kb 下的 md 是真源；写完触发重摄取才生效）────

def _safe_md_path(rel: str) -> Path:
    """路径合法性校验（不做存在性检查，也不做跨目录搜索）。"""
    if not rel or rel.strip() != rel or rel.startswith("/") or "\\" in rel or ".." in rel.split("/"):
        raise HTTPException(status_code=400, detail="非法的文档路径")
    p = (CONTENT_KB / rel).resolve()
    if not str(p).startswith(str(CONTENT_KB.resolve())):
        raise HTTPException(status_code=400, detail="路径越出 content/kb")
    if p.suffix != ".md":
        raise HTTPException(status_code=400, detail="只接受 .md 文档")
    return p


def _find_md(rel: str, module: str | None = None) -> Path:
    """把 chroma 的 source_path 解析成真实文件。

    历史摄取的前缀不统一（HANDOFF 坑 7）：M1 部分文档的 source_path 只有文件名，
    没有「必修一/」前缀。所以按三级回退找：
      1) content/kb/<rel>（带目录的规范情况）
      2) content/kb/<module>/<rel>
      3) 全库按文件名搜；唯一命中即用，多命中要求 module 消歧
    """
    base = _safe_md_path(rel)
    if base.exists():
        return base
    name = Path(rel).name
    if module:
        cand = (CONTENT_KB / module / name)
        if cand.exists():
            return cand
    hits = [p for p in CONTENT_KB.rglob(name) if p.is_file()]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        raise HTTPException(status_code=404, detail="文档不存在")
    raise HTTPException(status_code=409, detail=f"同名文档有 {len(hits)} 份，请带 module 参数消歧")


def _module_of(path: Path) -> str:
    """文档所属册：相对 content/kb 的第一段目录（专题方法 也算册目录）。"""
    rel = path.resolve().relative_to(CONTENT_KB.resolve())
    parts = rel.parts
    return parts[0] if len(parts) > 1 else ""


@router.get("/documents/content")
async def get_document_content(
    path: str,
    module: Optional[str] = None,
    _perm: None = Depends(PermissionChecker("knowledge", "read")),
):
    p = _find_md(path, module)
    stat = p.stat()
    return {"success": True, "path": path, "module": _module_of(p),
            "resolved_path": str(p.relative_to(CONTENT_KB.resolve())).replace("\\", "/"),
            "content": p.read_text(encoding="utf-8"),
            "size": stat.st_size,
            "updated_at": datetime.utcfromtimestamp(stat.st_mtime).isoformat()}


class DocumentContentBody(BaseModel):
    path: str
    content: str
    module: Optional[str] = None


@router.put("/documents/content")
async def save_document_content(
    body: DocumentContentBody,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("knowledge", "update")),
):
    """保存正文。**只写文件**，不改索引 —— 前端随后要提示「重新摄取才生效」。"""
    p = _find_md(body.path, body.module)
    if not body.content.strip():
        raise HTTPException(status_code=400, detail="正文不能为空")

    p.write_text(body.content, encoding="utf-8")
    db.add(OperationLog(
        user_id=current_user.get("user_id"),
        action="kb_document_edit",
        target_type="kb_document", target_id=body.path,
        detail={"module": _module_of(p), "chars": len(body.content)},
    ))
    db.commit()
    return {"success": True, "path": body.path, "module": _module_of(p),
            "note": "已保存正文；重新摄取该册后才会进入检索索引"}
