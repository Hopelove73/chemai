"""并发回归（bug 狩猎批次 A/B）：并发追工件版本 / 并发复习同一记忆卡。

真实部署形态（文件库 + WAL + 每请求独立 session）下的并发边界：
- 服务层必须把竞态消化成干净结果（成功或 RuntimeError），不许泄漏裸
  OSError/IntegrityError（bug 狩猎 H1：O_EXCL FileExistsError 曾直接 500）。
- 成功的版本号互不重复（互相覆盖 = 数据损坏）。
- 并发复习同一记忆卡不崩（last-write-wins 可接受）。
"""
import threading

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session as _Session

from app.models.database import Base
from app.services import artifact_service
from app.services import memory_card_service as mcs


@pytest.fixture
def wal_engine(tmp_path, monkeypatch):
    """文件库 + WAL：真实部署的并发形态（内存库 + StaticPool 模拟不了）。"""
    monkeypatch.setenv("CHEMAI_WORKSPACE", str(tmp_path / "ws"))
    dbfile = tmp_path / "conc.db"
    engine = create_engine(f"sqlite:///{dbfile}",
                           connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
    yield engine


def _run_concurrently(worker, n=8):
    errors, results = [], []
    barrier = threading.Barrier(n)

    def run(i):
        barrier.wait()
        try:
            results.append(worker(i))
        except Exception as e:  # noqa: BLE001
            errors.append(f"{type(e).__name__}: {e}")

    threads = [threading.Thread(target=run, args=(i,)) for i in range(n)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return errors, results


def test_concurrent_artifact_version_appends(wal_engine):
    """8 线程并发追同一工件：无裸异常泄漏、成功的版本号互不重复。
    （bug 狩猎 H1：O_EXCL FileExistsError 曾直接 500，修复为重读 seq 重试 +
    退避；SQLite 单写者拒掉的写由服务层转干净 RuntimeError。）"""
    seed = _Session(bind=wal_engine)
    art = artifact_service.save_artifact(
        seed, owner_teacher_id="t-1", kind="animation", title="并发", content="v0")
    seed.close()

    def worker(i):
        sess = _Session(bind=wal_engine)
        try:
            out = artifact_service.save_artifact(
                sess, owner_teacher_id="t-1", kind="animation",
                title="并发", content=f"c{i}",
                artifact_id=art["artifact_id"])
            return out["version_seq"]
        finally:
            sess.close()

    errors, versions = _run_concurrently(worker, n=8)
    # 服务层不许泄漏裸异常（RuntimeError 是干净冲突语义，允许）
    assert not [e for e in errors if not e.startswith("RuntimeError")], errors
    assert len(versions) == len(set(versions)), f"版本号重复: {versions}"
    assert all(v >= 2 for v in versions)


def test_concurrent_review_same_card_no_crash(wal_engine):
    """6 线程并发复习同一记忆卡（每线程独立 session，真实请求形态）：
    last-write-wins 可接受，崩不可接受。"""
    seed = _Session(bind=wal_engine)
    card = mcs.ensure_card(seed, "s-1", "q-1")
    seed.commit()
    card_id = card.id
    seed.close()

    def worker(i):
        sess = _Session(bind=wal_engine)
        try:
            mcs.review_card(sess, card_id, is_correct=(i % 2 == 0))
            return True
        finally:
            sess.close()

    errors, _ = _run_concurrently(worker, n=6)
    assert not errors, errors
