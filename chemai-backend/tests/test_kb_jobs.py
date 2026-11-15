"""kb-ingest-ui 后端测试：迁移幂等、启动中断标记、并发约束、影子构建-切换、读侧回落。

摄取线程体在本测试中被打桩（chroma/嵌入/摄取函数全部替换为内存假件）——
测的是任务机制与切换语义，不是嵌入本身（嵌入冒烟在 env-check.md 人工验证过）。
"""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.middleware.auth import create_access_token
from app.models.database import Base, KBJob, get_db
from app.utils.migrate import create_kb_jobs_table, create_kb_collection_state_table


class _FakeCollection:
    def __init__(self, name, store):
        self.name = name
        self._store = store

    def count(self):
        return len(self._store.get(self.name, []))


class _FakeClient:
    """chroma PersistentClient 假件：集合注册表 + 删除追踪。"""
    def __init__(self):
        self.store: dict[str, list] = {"kb_chunks": ["c1", "c2"]}
        self.deleted: list[str] = []

    def list_collections(self):
        return [_FakeCollection(n, self.store) for n in list(self.store)]

    def get_collection(self, name):
        if name not in self.store:
            raise ValueError(f"no such collection {name}")
        return _FakeCollection(name, self.store)

    def create_collection(self, name, metadata=None):
        self.store.setdefault(name, [])
        return _FakeCollection(name, self.store)

    def delete_collection(self, name):
        self.deleted.append(name)
        self.store.pop(name, None)


@pytest.fixture
def engine():
    e = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(e)
    create_kb_jobs_table(e)
    create_kb_collection_state_table(e)
    s = sessionmaker(bind=e, expire_on_commit=False)()
    yield e, s
    s.close()


@pytest.fixture
def kb_env(engine, monkeypatch):
    """打桩环境：假 chroma、假嵌入、假摄取（写 3 块进影子集合）。"""
    from app.services import kb_jobs
    e, session = engine
    fake_client = _FakeClient()

    import app.services.kb_ingest.ingest as ingest_mod
    monkeypatch.setattr(ingest_mod, "ingest_directory",
                        lambda kb_dir, collection, embedder, known_codes, module="", path_prefix="":
                        {"docs": 2, "chunks": 3, "inserted_docs": 2, "skipped_docs": 0,
                         "failed": [], "durationMs": 1}
                        or collection.add(ids=["x"], documents=["d"], embeddings=[[0.0]]))
    monkeypatch.setattr(kb_jobs, "_session_factory", lambda: sessionmaker(bind=e, expire_on_commit=False))
    monkeypatch.setattr("chromadb.PersistentClient", lambda path, settings=None: fake_client)
    monkeypatch.setattr("app.services.vector_search.get_text_encoder", lambda: object())
    monkeypatch.setattr(kb_jobs, "list_modules", lambda: ["必修二"])
    return e, session, fake_client


def _drain(timeout: float = 5.0):
    """等进程内摄取线程收尾（registry 线程表清空即完成）。"""
    from app.services import kb_jobs
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not kb_jobs._threads:
            return
        time.sleep(0.05)


def test_migrations_idempotent():
    e = create_engine("sqlite://")
    create_kb_jobs_table(e)
    create_kb_jobs_table(e)
    create_kb_collection_state_table(e)
    create_kb_collection_state_table(e)
    with e.connect() as conn:
        row = conn.execute(text("SELECT active_collection FROM kb_collection_state WHERE id=1")).fetchone()
        assert row[0] == "kb_chunks"


def test_mark_interrupted_on_startup(engine):
    e, session = engine
    session.add_all([
        KBJob(job_id="j1", module="必修二", status="running"),
        KBJob(job_id="j2", module="必修二", status="queued"),
        KBJob(job_id="j3", module="必修二", status="succeeded"),
    ])
    session.commit()
    from app.services.kb_jobs import mark_interrupted_on_startup
    n = mark_interrupted_on_startup(e)
    assert n == 2
    statuses = {j.job_id: j.status for j in session.query(KBJob).all()}
    assert statuses == {"j1": "interrupted", "j2": "interrupted", "j3": "succeeded"}


def test_submit_unknown_module_rejected(kb_env):
    from app.services import kb_jobs
    _, session, _ = kb_env
    with pytest.raises(ValueError):
        kb_jobs.submit_job(session, "不存在的册", "A1")


def test_concurrent_same_module_rejected(kb_env, monkeypatch):
    """同册并发：第一个在跑时第二个触发必须被拒（并发约束 D6）。"""
    from app.services import kb_jobs
    e, session, _ = kb_env

    release = {"event": False}
    import app.services.kb_ingest.ingest as ingest_mod
    def slow_ingest(kb_dir, collection, embedder, known_codes, module="", path_prefix=""):
        while not release["event"]:
            time.sleep(0.02)
        return {"docs": 1, "chunks": 1, "inserted_docs": 1, "skipped_docs": 0, "failed": [], "durationMs": 1}
    monkeypatch.setattr(ingest_mod, "ingest_directory", slow_ingest)

    job = kb_jobs.submit_job(session, "必修二", "A1")
    deadline = time.time() + 5
    while time.time() < deadline:
        session.expire_all()
        if session.get(KBJob, job.job_id).status == "running":
            break
        time.sleep(0.02)

    with pytest.raises(RuntimeError, match="进行中"):
        kb_jobs.submit_job(session, "必修二", "A2")

    release["event"] = True
    _drain()


def test_shadow_build_then_switch_and_cleanup(kb_env):
    """成功路径：影子集合建成 → 指向切换 → 旧集合删除；任务落 succeeded。"""
    from app.services import kb_jobs
    e, session, fake_client = kb_env

    job = kb_jobs.submit_job(session, "必修二", "A1")
    _drain()

    session.expire_all()
    done = session.get(KBJob, job.job_id)
    assert done.status == "succeeded", done.error
    assert done.progress["chunks"] == 3

    with e.connect() as conn:
        active = conn.execute(text("SELECT active_collection FROM kb_collection_state WHERE id=1")).fetchone()[0]
    assert active == done.shadow_collection
    assert active in fake_client.store            # 新家在
    assert "kb_chunks" in fake_client.deleted     # 旧家已清
    assert "kb_chunks" not in fake_client.store


def test_ingest_failure_keeps_old_index(kb_env, monkeypatch):
    """失败路径：摄取抛错 → 任务 failed、影子清理、指向不变、旧索引完好。"""
    from app.services import kb_jobs
    e, session, fake_client = kb_env

    import app.services.kb_ingest.ingest as ingest_mod
    def boom(kb_dir, collection, embedder, known_codes, module="", path_prefix=""):
        raise RuntimeError("嵌入失败：EMBEDDING_CACHE_DIR 缺失")
    monkeypatch.setattr(ingest_mod, "ingest_directory", boom)

    job = kb_jobs.submit_job(session, "必修二", "A1")
    _drain()

    session.expire_all()
    done = session.get(KBJob, job.job_id)
    assert done.status == "failed"
    assert "EMBEDDING_CACHE_DIR" in (done.error or "")

    with e.connect() as conn:
        active = conn.execute(text("SELECT active_collection FROM kb_collection_state WHERE id=1")).fetchone()[0]
    assert active == "kb_chunks"                  # 指向未动
    assert fake_client.store.get("kb_chunks") == ["c1", "c2"]  # 旧索引完好
    assert done.shadow_collection not in fake_client.store     # 影子已清


def test_resolver_falls_back_when_active_missing(engine):
    """读侧回落：指向的集合不存在 → 回落 kb_chunks；缺行 → 缺省名。"""
    from app.services.kb_jobs import resolve_kb_collection, active_collection_name
    e, session = engine
    fake = _FakeClient()
    # 指向不存在的集合 → 回落缺省 kb_chunks
    session.execute(text("UPDATE kb_collection_state SET active_collection='kb_chunks_ghost' WHERE id=1"))
    session.commit()
    assert active_collection_name(session) == "kb_chunks_ghost"
    assert resolve_kb_collection(session, fake).name == "kb_chunks"
    # 连缺省都没有 → None（不抛）
    fake.store.clear()
    assert resolve_kb_collection(session, fake) is None


def test_job_endpoints_roles(client_kb):
    """/api/kb/jobs 端点：触发门 knowledge:update；轮询门 knowledge:read。"""
    client = client_kb["client"]
    assert client.post("/api/kb/jobs", json={"module": "必修二"},
                       headers=client_kb["teacher_hdr"]).status_code == 403
    assert client.post("/api/kb/jobs", json={"module": "不存在的册"},
                       headers=client_kb["admin_hdr"]).status_code == 400
    assert client.get("/api/kb/jobs/nope", headers=client_kb["admin_hdr"]).status_code == 404


@pytest.fixture
def client_kb(engine):
    """kb_admin router 镜像 + 两种角色 header。"""
    from app.api import kb_admin as kb_admin_mod
    _, session = engine
    app = FastAPI()
    app.include_router(kb_admin_mod.router)

    def override_get_db():
        yield session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield {
            "client": c,
            "admin_hdr": {"Authorization": f"Bearer {create_access_token('A1', 'admin', school_id='sch1')}"},
            "teacher_hdr": {"Authorization": f"Bearer {create_access_token('T1', 'teacher', school_id='sch1')}"},
        }
