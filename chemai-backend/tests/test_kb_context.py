"""KB-T13 kb_context（seam）：向量 top4 + 图增强 + 降级 + 无命中阈值。

组装的是「参考资料」文本段与审计信息，不做任何 LLM 调用。
DegradableRetriever 模拟降级/超时/无命中三路。
"""
import json

import pytest
from sqlalchemy import create_engine, text

from app.services.kb_context import (KB_MISS_SENTINEL, build_kb_context,
                                      prerequisite_chain)


class FakeCollection:
    """语料库里预置 4 块：氧化还原两块、胶体一块、铁盐一块（问题命中前两）。"""

    def __init__(self, fail=False, empty=False, far=False, band=False):
        self.fail = fail
        self.empty = empty
        self.far = far
        self.band = band
        self.rows = [
            {"id": "kb1::001", "dist": 0.2,
             "doc": "氧化还原反应 › 是什么\n有电子转移（化合价升降）的反应叫氧化还原反应。",
             "meta": {"title_path": "氧化还原反应 › 是什么", "body": "有电子转移（化合价升降）的反应叫氧化还原反应。",
                       "module": "必修一", "kp_ids": json.dumps(["M1-T2-KP06"]), "source_path": "必修一/氧化还原反应.md"}},
            {"id": "kb1::002", "dist": 0.25,
             "doc": "氧化还原反应 › 常见易错点\n氧化剂发生氧化反应——错。",
             "meta": {"title_path": "氧化还原反应 › 常见易错点", "body": "氧化剂发生氧化反应——错。",
                       "module": "必修一", "kp_ids": json.dumps(["M1-T2-KP06"]), "source_path": "必修一/氧化还原反应.md"}},
            {"id": "kb2::001", "dist": 0.55,
             "doc": "分散系与胶体 › 是什么\n胶体是分散质粒径 1~100nm 的分散系。",
             "meta": {"title_path": "分散系与胶体 › 是什么", "body": "胶体是分散质粒径 1~100nm 的分散系。",
                       "module": "必修一", "kp_ids": json.dumps(["M1-T2-KP02"]), "source_path": "必修一/分散系与胶体.md"}},
            {"id": "kb3::001", "dist": 0.6,
             "doc": "铁盐与亚铁盐及转化 › 是什么\nFe3+/Fe2+ 相互转化。",
             "meta": {"title_path": "铁盐与亚铁盐及转化 › 是什么", "body": "Fe3+/Fe2+ 相互转化。",
                       "module": "必修一", "kp_ids": json.dumps(["M1-T2-KP19"]), "source_path": "必修一/铁盐.md"}},
        ]

    def query(self, query_embeddings, n_results, include=None):
        if self.fail:
            raise RuntimeError("chroma down")
        if self.empty:
            return {"ids": [[]], "metadatas": [[]], "documents": [[]], "distances": [[]]}
        # 远距离阈值测试：全部命中但距离都 > 0.45；弱命中带：0.39 < d ≤ 0.45
        rows = self.rows if not self.far else [
            {**r, "dist": 0.9} for r in self.rows]
        if self.band:
            rows = [{**r, "dist": d} for r, d in
                    zip(self.rows, (0.40, 0.42, 0.44, 0.46))]
        rows = sorted(rows, key=lambda r: r["dist"])[:n_results]
        return {"ids": [[r["id"] for r in rows]],
                "metadatas": [[r["meta"] for r in rows]],
                "documents": [[r["doc"] for r in rows]],
                "distances": [[r["dist"] for r in rows]]}


class FakeEncoder:
    def encode(self, texts, **kw):
        return [[0.1] * 8 for _ in texts]


@pytest.fixture()
def graph_db():
    """A→B→C 先修链 + 各 kp 的方程式字段（走 knowledge_points.description 约定外的新列）。

    方程式来自知识点文档 frontmatter——为免再迁一列，检索侧图增强读
    kb_frontmatter 表（T13 建）：kp_id → equations_json。
    """
    e = create_engine("sqlite:///:memory:")
    with e.begin() as conn:
        conn.execute(text("CREATE TABLE knowledge_edges "
                          "(from_kp VARCHAR(128), to_kp VARCHAR(128), "
                          "status VARCHAR(16), edge_type VARCHAR(32))"))
        for f, t in (("M1-T2-KP01", "M1-T2-KP06"), ("M1-T2-KP06", "M1-T2-KP07")):
            conn.execute(text("INSERT INTO knowledge_edges VALUES (:f,:t,'active','prerequisite')"),
                         {"f": f, "t": t})
        conn.execute(text("CREATE TABLE kb_frontmatter "
                          "(kp_id VARCHAR(64) PRIMARY KEY, equations_json TEXT, "
                          "misconceptions_json TEXT)"))
        conn.execute(text("INSERT INTO kb_frontmatter VALUES ('M1-T2-KP06', :j, :mis)"),
                     {"j": json.dumps(["Fe + CuSO4 = FeSO4 + Cu"], ensure_ascii=False),
                      "mis": json.dumps([
                          {"wrong": "氯水就是液态氯", "right": "氯水是混合物", "why": "名字像"},
                          {"wrong": "干燥氯气漂白", "right": "漂白的是HClO", "why": ""},
                          {"wrong": "Fe与Cl2生成FeCl2", "right": "强氧化剂到高价FeCl3", "why": "套用经验"},
                          {"wrong": "氯气尾气用水吸收", "right": "用NaOH吸收", "why": "溶解度有限"},
                      ], ensure_ascii=False)})
    yield e
    e.dispose()


def test_builds_reference_block_with_sources(graph_db):
    out = build_kb_context("什么是氧化还原反应", collection=FakeCollection(),
                           encoder=FakeEncoder(), graph_engine=graph_db)
    assert out["degraded"] is False and out["mode"] == "ok"
    text_out = out["text"]
    assert "【参考资料】" in text_out and "氧化还原反应 › 是什么" in text_out
    assert "来源" in text_out and "氧化还原反应.md" in text_out, "每段带来源路径"
    # 图增强：命中 M1-T2-KP06 → 先修链带出 M1-T2-KP01 + 方程式
    assert "先修" in text_out and "M1-T2-KP01" in text_out
    assert "Fe + CuSO4" in text_out, "kp 的 equations 进增强段"


def test_misconceptions_section_capped_at_three(graph_db):
    """KB2b 检索侧：最相关命中块的知识点带易错提醒，上限 3 条防 prompt 膨胀。"""
    out = build_kb_context("什么是氧化还原反应", collection=FakeCollection(),
                           encoder=FakeEncoder(), graph_engine=graph_db)
    text_out = out["text"]
    assert "【常见易错提醒】" in text_out
    assert text_out.count("误：") == 3, "4 条存库只注入 3 条"
    assert "氯水就是液态氯" in text_out and "氯水是混合物" in text_out
    assert "错因：名字像" in text_out and "氯气尾气用水吸收" not in text_out, "第 4 条被截断"


def test_dedup_same_document_adjacent_chunks(graph_db):
    """同文档相邻块只保留高分块（切块重叠不得变成结果重复）。"""
    out = build_kb_context("氧化还原", collection=FakeCollection(),
                           encoder=FakeEncoder(), graph_engine=graph_db)
    srcs = [h["source_path"] + str(h["section_no"]) for h in out["hits"]]
    assert len(srcs) == len(set(srcs)), "同文档同节不得重复入选"


def test_chroma_down_degrades_gracefully(graph_db):
    out = build_kb_context("q", collection=FakeCollection(fail=True),
                           encoder=FakeEncoder(), graph_engine=graph_db)
    assert out["degraded"] is True and out["hits"] == []
    assert KB_MISS_SENTINEL in out["text"] or out["text"] == "", "降级不炸"
    assert (out.get("enhancement") or {}).get("misconceptions", []) == [], "降级路径易错段为空"


def test_old_schema_without_misconceptions_column_still_enhances(tmp_path):
    """存量库 kb_frontmatter 无 misconceptions_json 列：只丢易错段，先修链与方程式照常。"""
    e = create_engine(f"sqlite:///{tmp_path}/old.db")
    with e.begin() as conn:
        conn.execute(text("CREATE TABLE knowledge_edges "
                          "(from_kp VARCHAR(128), to_kp VARCHAR(128), "
                          "status VARCHAR(16), edge_type VARCHAR(32))"))
        conn.execute(text("INSERT INTO knowledge_edges VALUES "
                          "('M1-T2-KP01','M1-T2-KP06','active','prerequisite')"))
        conn.execute(text("CREATE TABLE kb_frontmatter "
                          "(kp_id VARCHAR(64) PRIMARY KEY, equations_json TEXT)"))
        conn.execute(text("INSERT INTO kb_frontmatter VALUES "
                          "('M1-T2-KP06', '[\"Fe + CuSO4 = FeSO4 + Cu\"]')"))
    out = build_kb_context("什么是氧化还原反应", collection=FakeCollection(),
                           encoder=FakeEncoder(), graph_engine=e)
    assert "先修" in out["text"] and "Fe + CuSO4" in out["text"], "旧 schema 不拖垮先修/方程式"
    assert "【常见易错提醒】" not in out["text"], "旧 schema 只丢易错段"


def test_no_hit_below_threshold_returns_miss(graph_db):
    out = build_kb_context("q", collection=FakeCollection(far=True),
                           encoder=FakeEncoder(), graph_engine=graph_db)
    assert out["mode"] == "miss" and out["hits"] == []
    assert "暂未覆盖" in out["text"], "无命中给模型明确信号"


def test_strong_hit_reports_coverage_strong(graph_db):
    out = build_kb_context("什么是氧化还原反应", collection=FakeCollection(),
                           encoder=FakeEncoder(), graph_engine=graph_db)
    assert out["mode"] == "ok" and out["coverage"] == "strong"
    assert "【覆盖提示】" not in out["text"]


def test_weak_band_returns_weak_mode_with_coverage_note(graph_db):
    """KB4a 三态：最好块距离落在 0.39~0.45 带 → weak；资料照常交付 + 覆盖提示。"""
    out = build_kb_context("q", collection=FakeCollection(band=True),
                           encoder=FakeEncoder(), graph_engine=graph_db)
    assert out["mode"] == "weak" and out["coverage"] == "weak"
    assert out["hits"], "弱命中不是拒答，资料照常交付"
    assert "【覆盖提示】" in out["text"] and "【参考资料】" in out["text"]
    assert out["text"].index("【覆盖提示】") < out["text"].index("【参考资料】"), \
        "提示在资料段之前"


def test_weak_band_boundary_strong_at_line(graph_db):
    """边界：最好块 ≤0.39 仍是强命中（弱带是开区间下界）。"""
    e = create_engine("sqlite:///:memory:")
    with e.begin() as conn:
        conn.execute(text("CREATE TABLE knowledge_edges (from_kp VARCHAR(128), "
                          "to_kp VARCHAR(128), status VARCHAR(16), edge_type VARCHAR(32))"))
        conn.execute(text("CREATE TABLE kb_frontmatter (kp_id VARCHAR(64) PRIMARY KEY, "
                          "equations_json TEXT, misconceptions_json TEXT)"))
    col = FakeCollection()
    col.rows = [{**col.rows[0], "dist": 0.39}]
    out = build_kb_context("q", collection=col, encoder=FakeEncoder(), graph_engine=e)
    assert out["mode"] == "ok" and out["coverage"] == "strong"


def test_prerequisite_chain_walks_upstream(graph_db):
    with graph_db.connect() as conn:
        chain = prerequisite_chain(conn, "M1-T2-KP07")
    assert set(chain) == {"M1-T2-KP01", "M1-T2-KP06"}, "上溯先修链（≤2 跳默认）"
