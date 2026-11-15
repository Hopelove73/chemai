"""KB 摄取任务机制（kb-ingest-ui）—— 进程内 registry + 影子集合构建-切换。

硬约束（kb-ingest-ui design D2/D3）：
- 一切 chroma 写操作留在主 FastAPI 进程内（chemai.db 文件锁 + WAL 竞态，
  HANDOFF 坑 3/10），所以摄取在线程池里跑、直接连主 chroma 实例路径。
- 影子集合构建-切换：新索引建成才切 `kb_collection_state.active_collection`，
  失败删除影子集合 —— 摄取失败不影响既有检索索引。
- 同册并发：registry 条件检查 + DB 条件更新双保险；拒绝而非排队。
- 服务重启：startup 调 mark_interrupted_on_startup 把非终结态标 interrupted。
"""
import threading
import traceback
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.models.database import KBJob, OperationLog

CONTENT_KB = Path(__file__).resolve().parents[2] / "content" / "kb"
DEFAULT_COLLECTION = "kb_chunks"
# 进程内互斥：同册一个线程。跨进程不设防（单机单 uvicorn 是部署事实）。
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()
_threads: dict[str, threading.Thread] = {}
# 线程体的会话工厂（测试注入点：默认主库；测试换内存库防写穿）
_session_factory = None


def _session():
    from app.models.database import get_session_factory
    factory = _session_factory or get_session_factory
    return factory()()


def _module_lock(module: str) -> threading.Lock:
    with _locks_guard:
        if module not in _locks:
            _locks[module] = threading.Lock()
        return _locks[module]


def list_modules() -> list[str]:
    """content/kb 下的册目录（reference 是素材库不是册）。"""
    if not CONTENT_KB.exists():
        return []
    return sorted(d.name for d in CONTENT_KB.iterdir() if d.is_dir() and d.name != "reference")


def mark_interrupted_on_startup(engine) -> int:
    """启动期：进程异常退出留下的 queued/running 一律标 interrupted。"""
    with engine.begin() as conn:
        result = conn.execute(text(
            "UPDATE kb_jobs SET status='interrupted', "
            "error='服务重启中断，可重跑', finished_at=CURRENT_TIMESTAMP "
            "WHERE status IN ('queued','running')"))
    return result.rowcount


def active_collection_name(db: Session) -> str:
    """读侧解析（resolver）：kb_collection_state 的活跃集合名；缺省 kb_chunks。"""
    try:
        row = db.execute(text("SELECT active_collection FROM kb_collection_state WHERE id=1")).fetchone()
        return (row[0] if row else None) or DEFAULT_COLLECTION
    except Exception:
        return DEFAULT_COLLECTION


def resolve_kb_collection(db: Session, client):
    """给读侧用：按 state 行取活跃 chroma 集合；集合不存在时回落缺省名再回落 None。

    从不抛异常 —— 检索挂了不能拖死回合（与 tutoring._kb_resources 的降级纪律一致）。
    """
    name = active_collection_name(db)
    try:
        names = {c.name for c in client.list_collections()}
        if name in names:
            return client.get_collection(name)
        if DEFAULT_COLLECTION in names:
            return client.get_collection(DEFAULT_COLLECTION)
    except Exception:
        pass
    return None


def _run_ingest(job_id: str, module: str) -> None:
    """线程体：影子构建 → 成功切换 → 清旧集合；失败清理影子。"""
    from app.core.config import settings

    session = _session()
    job = session.get(KBJob, job_id)
    try:
        job.status = "running"
        session.commit()

        import chromadb
        from chromadb.config import Settings as ChromaSettings
        client = chromadb.PersistentClient(path=settings.CHROMA_DB_PATH,
                                           settings=ChromaSettings(anonymized_telemetry=False))
        shadow = f"kb_chunks_shadow_{job_id[-8:]}"
        job.shadow_collection = shadow
        session.commit()
        existing = {c.name for c in client.list_collections()}
        collection = (client.get_collection(shadow) if shadow in existing
                      else client.create_collection(shadow, metadata={"hnsw:space": "cosine"}))

        from app.services.kb_ingest.ingest import ingest_directory
        from app.services.vector_search import get_text_encoder
        from app.services.kb_dictionary import load_all_dictionaries
        known = {it["code"] for it in load_all_dictionaries(str(CONTENT_KB))["items"]}
        embedder = get_text_encoder()
        report = ingest_directory(CONTENT_KB / module, collection=collection, embedder=embedder,
                                  known_codes=known, module=module, path_prefix=module)
        if report.get("failed"):
            raise RuntimeError(f"{len(report['failed'])} 篇文档摄取失败: {report['failed'][:3]}")

        # 切换：读旧活跃名 → state 行指向影子 → 成功后删旧集合（影子即新家）
        old = active_collection_name(session)
        session.execute(text(
            "UPDATE kb_collection_state SET active_collection=:n, updated_at=CURRENT_TIMESTAMP WHERE id=1"),
            {"n": shadow})
        session.commit()
        if old and old != shadow and old in {c.name for c in client.list_collections()}:
            client.delete_collection(old)

        job = session.get(KBJob, job_id)
        job.status = "succeeded"
        job.progress = {k: report.get(k) for k in ("docs", "chunks", "inserted_docs", "skipped_docs", "durationMs")}
        job.finished_at = datetime.utcnow()
        session.commit()
    except Exception as e:  # noqa: BLE001 —— 线程体必须吞掉一切并把失败落到任务行
        session.rollback()
        job = session.get(KBJob, job_id)
        job.status = "failed"
        job.error = f"{e}"[:1000]
        job.finished_at = datetime.utcnow()
        session.commit()
        # 清影子集合（失败不得留半成品，也不得动旧索引）
        try:
            if job.shadow_collection:
                client_local = None
                import chromadb as _cd
                from chromadb.config import Settings as _CS
                from app.core.config import settings as _settings
                client_local = _cd.PersistentClient(path=_settings.CHROMA_DB_PATH,
                                                    settings=_CS(anonymized_telemetry=False))
                names = {c.name for c in client_local.list_collections()}
                if job.shadow_collection in names:
                    client_local.delete_collection(job.shadow_collection)
        except Exception:
            traceback.print_exc()
    finally:
        session.close()
        with _locks_guard:
            _threads.pop(job_id, None)


def submit_job(db: Session, module: str, created_by: str) -> KBJob:
    """触发摄取：同册未终结任务存在 → 拒绝（并发约束 D6）；否则建任务 + 起线程。"""
    if module not in list_modules():
        raise ValueError(f"未知册名: {module!r}")

    existing = (db.query(KBJob)
                .filter(KBJob.module == module, KBJob.status.in_(("queued", "running")))
                .order_by(KBJob.created_at.desc()).first())
    if existing:
        raise RuntimeError(f"该册已有进行中的任务 {existing.job_id}")

    job = KBJob(job_id=f"kbj_{uuid4().hex[:16]}", module=module, status="queued",
                created_by=created_by)
    db.add(job)
    db.commit()

    lock = _module_lock(module)
    if not lock.acquire(blocking=False):
        db.rollback()
        raise RuntimeError("该册摄取正在执行中（进程内互斥）")
    lock.release()

    t = threading.Thread(target=_with_module_lock, args=(lock, job.job_id, module), daemon=True)
    _threads[job.job_id] = t
    t.start()
    return job


def _with_module_lock(lock: threading.Lock, job_id: str, module: str) -> None:
    with lock:
        _run_ingest(job_id, module)


def sync_dictionary(db: Session, operator: str) -> dict:
    """词典同步（幂等 DB 写，复用 kb_dictionary 服务函数）+ 审计留痕。"""
    from app.services.kb_dictionary import load_all_dictionaries
    from app.services.kb_dictionary import sync_dictionary as _sync
    data = load_all_dictionaries(str(CONTENT_KB))
    report = _sync(db, data)
    db.add(OperationLog(
        user_id=operator, action="kb_dictionary_sync",
        target_type="kb_dictionary", target_id="all", detail={"report": report}))
    db.commit()
    return report


def sync_tag_map(db: Session, operator: str) -> dict:
    """标签映射导入（幂等，复用 kb_tag_map_import 服务函数）。"""
    from app.services.kb_tag_map_import import import_tag_map
    from app.services.kb_dictionary import load_all_dictionaries
    from app.models.database import OperationLog
    known = {it["code"] for it in load_all_dictionaries(str(CONTENT_KB))["items"]}
    report = import_tag_map(db.get_bind(), str(CONTENT_KB), known)
    db.add(OperationLog(
        user_id=operator, action="kb_tag_map_sync",
        target_type="kb_tag_map", target_id="all", detail={"report": report}))
    db.commit()
    return report
