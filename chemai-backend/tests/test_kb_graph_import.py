"""KB-T12 审校包落库流（seam）：proposals.json → 提议边/物质落库。

纪律（设计 §4）：
  · 落库形态沿用 propose→accept 队列：边以 status='proposed' 入库，老师采纳后转 active（存量生效态词汇）；
  · prerequisite 提议带环检测——**成环的提议拒绝入队**（fail-closed）；
  · relates_to 同步 upsert 物质节点（kb_substances 幂等）；
  · 审校包的 kp 必须在词典中（复用词典 codes 校验）。
"""
import json

import pytest
from sqlalchemy import create_engine, text

from app.services.kb_graph_import import import_proposals


@pytest.fixture()
def engine(tmp_path):
    e = create_engine(f"sqlite:///{tmp_path}/imp.db")
    with e.begin() as conn:
        conn.execute(text("""
            CREATE TABLE knowledge_points (kp_id VARCHAR(64) PRIMARY KEY, name VARCHAR(256))
        """))
        for c in ("M1-T2-KP01", "M1-T2-KP06", "M1-T2-KP07", "M1-T2-KP08"):
            conn.execute(text("INSERT INTO knowledge_points VALUES (:c, :n)"),
                         {"c": c, "n": c})
        conn.execute(text("""
            CREATE TABLE knowledge_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                from_kp VARCHAR(128) NOT NULL, to_kp VARCHAR(128) NOT NULL,
                status VARCHAR(16) NOT NULL DEFAULT 'active',
                reason TEXT, proposed_by VARCHAR(64), created_at TIMESTAMP,
                edge_type VARCHAR(32), confidence FLOAT, source_doc VARCHAR(256)
            )
        """))
        conn.execute(text("""
            CREATE TABLE kb_substances (
                substance_id VARCHAR(64) PRIMARY KEY, name VARCHAR(128) NOT NULL,
                aliases_json TEXT, source_doc VARCHAR(256), created_at TIMESTAMP
            )
        """))
    yield e
    e.dispose()


PROPOSALS = {
    "doc": "氧化还原反应.md", "kp": "M1-T2-KP06",
    "edges": [
        {"edge_type": "prerequisite", "from_kp": "M1-T2-KP01", "to_kp": "M1-T2-KP06",
         "reason": "先有分类框架"},
        {"edge_type": "relates_to", "kp": "M1-T2-KP06", "substance": "氯气",
         "formula": "Cl2", "role": "氧化剂"},
    ],
}


def test_import_creates_proposed_edges_and_substances(engine, tmp_path):
    p = tmp_path / "x.proposals.json"
    p.write_text(json.dumps(PROPOSALS, ensure_ascii=False), encoding="utf-8")
    report = import_proposals(engine, str(p), known_codes={"M1-T2-KP01", "M1-T2-KP06"})

    assert report["edges_proposed"] == 1
    assert report["substances_upserted"] == 1
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT from_kp, to_kp, status, edge_type, source_doc FROM knowledge_edges"
        )).fetchone()
        assert (row.from_kp, row.to_kp, row.status, row.edge_type) == \
               ("M1-T2-KP01", "M1-T2-KP06", "proposed", "prerequisite")
        assert "氧化还原反应.md" in row.source_doc
        sub = conn.execute(text(
            "SELECT substance_id, name FROM kb_substances")).fetchone()
        assert (sub.substance_id, sub.name) == ("sub-Cl2", "氯气")


def test_import_is_idempotent(engine, tmp_path):
    p = tmp_path / "x.proposals.json"
    p.write_text(json.dumps(PROPOSALS, ensure_ascii=False), encoding="utf-8")
    import_proposals(engine, str(p), known_codes={"M1-T2-KP01", "M1-T2-KP06"})
    report2 = import_proposals(engine, str(p), known_codes={"M1-T2-KP01", "M1-T2-KP06"})
    assert report2["edges_proposed"] == 0 and report2["duplicates"] == 1
    assert report2["substances_upserted"] == 0, "物质 upsert 也幂等"
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM knowledge_edges")).scalar() == 1
        assert conn.execute(text("SELECT COUNT(*) FROM kb_substances")).scalar() == 1


def test_import_treats_active_edge_as_duplicate(engine, tmp_path):
    """同一对边已 active（老师采纳过）再导入 → 记 duplicates 不插重复行。

    2026-10-28 实测：查重只认 proposed 时，重导会插出并行 proposed 行
    （同一对边一 active 一 proposed），批量采纳后永远清不干净。"""
    p = tmp_path / "x.proposals.json"
    p.write_text(json.dumps(PROPOSALS, ensure_ascii=False), encoding="utf-8")
    import_proposals(engine, str(p), known_codes={"M1-T2-KP01", "M1-T2-KP06"})
    with engine.connect() as conn:
        conn.execute(text("UPDATE knowledge_edges SET status='active'"))
        conn.commit()
    report = import_proposals(engine, str(p), known_codes={"M1-T2-KP01", "M1-T2-KP06"})
    assert report["edges_proposed"] == 0 and report["duplicates"] == 1
    with engine.connect() as conn:
        rows = conn.execute(text(
            "SELECT status FROM knowledge_edges")).fetchall()
        assert [r[0] for r in rows] == ["active"], "不得出现并行 proposed 行"


def test_import_rejects_cycle_proposal(engine, tmp_path):
    """A→B 已接受；再提 B→A（成环）→ 拒绝入队，报告说明原因。"""
    p1 = tmp_path / "a.proposals.json"
    p1.write_text(json.dumps({
        "doc": "a.md", "kp": "M1-T2-KP06",
        "edges": [{"edge_type": "prerequisite", "from_kp": "M1-T2-KP01",
                   "to_kp": "M1-T2-KP06", "reason": "r"}],
    }, ensure_ascii=False), encoding="utf-8")
    codes = {"M1-T2-KP01", "M1-T2-KP06", "M1-T2-KP07", "M1-T2-KP08"}
    import_proposals(engine, str(p1), known_codes=codes)
    # 老师接受 A→B
    with engine.begin() as conn:
        conn.execute(text("UPDATE knowledge_edges SET status='active'"))

    p2 = tmp_path / "b.proposals.json"
    p2.write_text(json.dumps({
        "doc": "b.md", "kp": "M1-T2-KP01",
        "edges": [{"edge_type": "prerequisite", "from_kp": "M1-T2-KP06",
                   "to_kp": "M1-T2-KP01", "reason": "反提成环"}],
    }, ensure_ascii=False), encoding="utf-8")
    report = import_proposals(engine, str(p2), known_codes=codes)
    assert report["edges_proposed"] == 0
    assert report["rejected"] and "环" in report["rejected"][0]["message"]
    with engine.connect() as conn:
        assert conn.execute(text(
            "SELECT COUNT(*) FROM knowledge_edges WHERE from_kp='M1-T2-KP06'")).scalar() == 0


def test_import_rejects_unknown_kp(engine, tmp_path):
    p = tmp_path / "bad.proposals.json"
    bad = json.loads(json.dumps(PROPOSALS, ensure_ascii=False))
    bad["edges"][0]["from_kp"] = "不存在的码"
    p.write_text(json.dumps(bad, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(Exception, match="词典"):
        import_proposals(engine, str(p), known_codes={"M1-T2-KP01", "M1-T2-KP06"})


def test_import_batch_directory(engine, tmp_path):
    """目录级导入：扫全部 *.proposals.json（批一 11 篇直接可跑）。"""
    d = tmp_path / "proposals"
    d.mkdir()
    for i in range(3):
        (d / f"d{i}.proposals.json").write_text(json.dumps({
            "doc": f"d{i}.md", "kp": "M1-T2-KP06",
            "edges": [{"edge_type": "relates_to", "kp": "M1-T2-KP06",
                       "substance": f"物质{i}", "formula": f"X{i}", "role": "示例"}],
        }, ensure_ascii=False), encoding="utf-8")
    report = import_proposals(engine, str(d), known_codes={"M1-T2-KP06"})
    assert report["files"] == 3
    assert report["substances_upserted"] == 3
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM kb_substances")).scalar() == 3
