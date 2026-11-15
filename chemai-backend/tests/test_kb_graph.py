"""KB-T11 图谱表（seam）：迁移幂等 + prerequisite 环检测。

边语义：prerequisite 边 (from_kp → to_kp) 表示"from 是 to 的前置"。
加一条 from→to 会成环 ⟺ 从 to 沿既有 prerequisite 边能走到 from。
"""
import pytest
from sqlalchemy import create_engine, inspect, text

from app.services.kb_graph import would_create_cycle


@pytest.fixture()
def graph_engine(tmp_path):
    """带 knowledge_edges（旧形状）与 kb_substances（由迁移建）的库。"""
    engine = create_engine(f"sqlite:///{tmp_path}/g.db")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE knowledge_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_kp VARCHAR(64) NOT NULL,
                to_kp VARCHAR(64) NOT NULL,
                status VARCHAR(32) NOT NULL DEFAULT 'proposed',
                reason TEXT,
                proposed_by VARCHAR(64),
                created_at TIMESTAMP
            )
        """))
    yield engine
    engine.dispose()


def test_migration_adds_edge_columns_and_creates_substances(graph_engine):
    from app.utils.migrate import migrate_kb_graph
    migrate_kb_graph(graph_engine)
    cols = {c["name"] for c in inspect(graph_engine).get_columns("knowledge_edges")}
    assert {"edge_type", "confidence", "source_doc"} <= cols
    tables = inspect(graph_engine).get_table_names()
    assert "kb_substances" in tables

    migrate_kb_graph(graph_engine)  # 重跑幂等
    cols2 = {c["name"] for c in inspect(graph_engine).get_columns("knowledge_edges")}
    assert cols2 == cols


def _add_edge(engine, from_kp, to_kp, edge_type="prerequisite", status="active"):
    with engine.begin() as conn:
        conn.execute(text(
            "INSERT INTO knowledge_edges (from_kp, to_kp, status, edge_type) "
            "VALUES (:f, :t, :s, :e)"), {"f": from_kp, "t": to_kp, "s": status, "e": edge_type})


def test_cycle_detected_on_back_edge(graph_engine):
    from app.utils.migrate import migrate_kb_graph
    migrate_kb_graph(graph_engine)
    # 已有 A→B（A 是 B 的前置）
    _add_edge(graph_engine, "A", "B")
    # 再提 B→A：B 已是 A 的后继，反向回填成二环 → 拒
    with graph_engine.connect() as conn:
        assert would_create_cycle(conn, "B", "A") is True
        assert would_create_cycle(conn, "A", "B") is False, "同向重边不是环（去重是另一层的事）"


def test_cycle_detected_transitively(graph_engine):
    from app.utils.migrate import migrate_kb_graph
    migrate_kb_graph(graph_engine)
    _add_edge(graph_engine, "A", "B")
    _add_edge(graph_engine, "B", "C")
    with graph_engine.connect() as conn:
        # 提议 C→A：A→B→C 已在，补 C→A 即三环 → 拒
        assert would_create_cycle(conn, "C", "A") is True
        # 提议 A→C：A 是 C 的更早先修，同向加深不成环 → 允
        assert would_create_cycle(conn, "A", "C") is False


def test_only_accepted_prerequisite_edges_count(graph_engine):
    from app.utils.migrate import migrate_kb_graph
    migrate_kb_graph(graph_engine)
    _add_edge(graph_engine, "A", "B", status="proposed")          # 未接受
    _add_edge(graph_engine, "B", "C", edge_type="relates_to")     # 非先修边
    with graph_engine.connect() as conn:
        assert would_create_cycle(conn, "C", "A") is False, \
            "未接受/非先修边不参与环检测——提议队列不能卡死自己"
