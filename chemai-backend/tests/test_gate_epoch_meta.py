"""T5/T19 验收：`gate_epoch` 盖章与加列解耦、缺列库启动即补、重跑不改写（R5/R12①）。

覆盖测试计划 N8。内存库不走 lifespan（`tests/conftest.py`），所以启动路径只能
直接单测挂载的那个函数——这里同时测它自己幂等。
"""
import sqlite3

import pytest
from sqlalchemy import create_engine

from app.utils.migrate import migrate_agent_gates_last_opened


def _columns(db_path):
    conn = sqlite3.connect(db_path)
    try:
        return {r[1] for r in conn.execute("PRAGMA table_info(agent_gates)")}
    finally:
        conn.close()


def _epoch(db_path):
    conn = sqlite3.connect(db_path)
    try:
        row = conn.execute(
            "SELECT value FROM agent_gate_meta WHERE key = 'gate_epoch'").fetchone()
        return row[0] if row else None
    finally:
        conn.close()


@pytest.fixture
def legacy_db(tmp_path):
    """一个只有旧版 agent_gates（无 last_opened_at）的库副本。"""
    db_path = tmp_path / "legacy.db"
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE agent_gates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            gate_id VARCHAR(80) NOT NULL UNIQUE,
            conversation_id VARCHAR(64) NOT NULL,
            kind VARCHAR(16) NOT NULL,
            tool_name VARCHAR(64) NOT NULL,
            kwargs JSON NOT NULL,
            draft_id VARCHAR(64),
            version INTEGER,
            action VARCHAR(32) NOT NULL,
            message TEXT NOT NULL,
            status VARCHAR(16) NOT NULL,
            created_at TIMESTAMP,
            decided_at TIMESTAMP
        )
    """)
    conn.execute(
        "INSERT INTO agent_gates (gate_id, conversation_id, kind, tool_name, kwargs,"
        " action, message, status, created_at) VALUES"
        " ('g-1', 't-a', 'draft', '', '{}', 'send_student', 'm', 'open',"
        " '2026-01-02 03:04:05')")
    conn.commit()
    conn.close()
    return db_path


def test_missing_column_db_upgraded_on_startup_path(legacy_db):
    """缺列库：补列 + 按 created_at 回填 + 盖章。"""
    assert "last_opened_at" not in _columns(legacy_db)
    engine = create_engine(f"sqlite:///{legacy_db}")

    migrate_agent_gates_last_opened(engine)

    assert "last_opened_at" in _columns(legacy_db)
    conn = sqlite3.connect(legacy_db)
    try:
        row = conn.execute(
            "SELECT last_opened_at, created_at FROM agent_gates").fetchone()
    finally:
        conn.close()
    assert row[0] == row[1] == "2026-01-02 03:04:05"
    assert _epoch(legacy_db)


def test_second_run_does_not_restamp_or_touch_rows(legacy_db):
    """幂等（R12① 的「已有章不改写」）：重启不改章、不改已回填的列值。"""
    engine = create_engine(f"sqlite:///{legacy_db}")
    migrate_agent_gates_last_opened(engine)
    first = _epoch(legacy_db)

    conn = sqlite3.connect(legacy_db)
    conn.execute("UPDATE agent_gates SET last_opened_at = '2026-09-09 09:09:09'")
    conn.commit()
    conn.close()

    migrate_agent_gates_last_opened(engine)

    assert _epoch(legacy_db) == first
    conn = sqlite3.connect(legacy_db)
    try:
        kept = conn.execute("SELECT last_opened_at FROM agent_gates").fetchone()[0]
    finally:
        conn.close()
    assert kept == "2026-09-09 09:09:09"


def test_fresh_empty_database_gets_a_stamp(tmp_path):
    """空库首次启动即有章（R12① 的关键断裂：新装库 create_all 已建含新列的表，
    ALTER 分支永不触发，盖章若挂在加列成功分支上就永远没有章）。"""
    from app.models.database import Base
    db_path = tmp_path / "fresh.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    assert _epoch(db_path) is None

    migrate_agent_gates_last_opened(engine)

    assert _epoch(db_path)
    assert "last_opened_at" in _columns(db_path)


def test_all_gates_rows_end_up_not_null(legacy_db):
    """验收硬条件：跑完之后 `last_opened_at` 无 NULL，排序键没有死角。"""
    engine = create_engine(f"sqlite:///{legacy_db}")
    migrate_agent_gates_last_opened(engine)
    conn = sqlite3.connect(legacy_db)
    try:
        nulls = conn.execute(
            "SELECT COUNT(*) FROM agent_gates WHERE last_opened_at IS NULL").fetchone()[0]
    finally:
        conn.close()
    assert nulls == 0


def test_composite_index_created_for_expiry_job(legacy_db):
    """过期作业与待确认列表都走 status + last_opened_at 这一条过滤查询。"""
    engine = create_engine(f"sqlite:///{legacy_db}")
    migrate_agent_gates_last_opened(engine)
    conn = sqlite3.connect(legacy_db)
    try:
        names = {r[1] for r in conn.execute("PRAGMA index_list(agent_gates)")}
    finally:
        conn.close()
    assert "ix_agent_gates_status_last_opened" in names


# ── T18：布尔门存量收敛只发生在首次盖章那一次 ───────────────────────────

def _insert(db_path, gate_id, kind, status="open"):
    conn = sqlite3.connect(db_path)
    conn.execute(
        "INSERT INTO agent_gates (gate_id, conversation_id, kind, tool_name, kwargs,"
        " action, message, status, created_at) VALUES"
        " (?, ?, ?, '', '{}', 'send_student', 'm', ?, '2026-01-02 03:04:05')",
        (gate_id, f"t-{gate_id}", kind, status))
    conn.commit()
    conn.close()


def _status(db_path, gate_id):
    conn = sqlite3.connect(db_path)
    try:
        return conn.execute(
            "SELECT status FROM agent_gates WHERE gate_id = ?", (gate_id,)).fetchone()[0]
    finally:
        conn.close()


def test_legacy_boolean_gates_converge_once_at_first_stamp(legacy_db):
    """旧编号不含会话 ⇒ 存量布尔门的 `conversation_id` 不可信，首次盖章时收敛；
    草稿门编号按 `draft_id` 稳定、归属可信，一律不动。"""
    _insert(legacy_db, "g-tool-legacy", "boolean")
    engine = create_engine(f"sqlite:///{legacy_db}")

    migrate_agent_gates_last_opened(engine)

    assert _status(legacy_db, "g-tool-legacy") == "superseded"
    assert _status(legacy_db, "g-1") == "open", "草稿门不参与本次收敛"


def test_second_run_keeps_gates_the_teacher_is_waiting_on(legacy_db):
    """收敛 MUST 挂在一次性窗口：否则服务每重启一次就清掉老师正在等的所有布尔门卡。"""
    engine = create_engine(f"sqlite:///{legacy_db}")
    migrate_agent_gates_last_opened(engine)          # 首次：盖章 + 收敛
    _insert(legacy_db, "g-tool-after", "boolean")    # 部署之后新开的门

    migrate_agent_gates_last_opened(engine)          # 重启：已有章 ⇒ 什么都不清

    assert _status(legacy_db, "g-tool-after") == "open"
