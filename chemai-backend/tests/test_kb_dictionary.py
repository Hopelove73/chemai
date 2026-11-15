"""KB1a 知识点词典（TDD 钉子，seam 已在任务拆解确认）：

  · 迁移 seam：`migrate_knowledge_points_kb` 给 knowledge_points 补
    code/theme/module/order_no 四列，重复跑不许报错（照 exam_records 迁移的
    "column exists, skip" 模式）；
  · 词典 seam：`load_dictionary` 拒绝结构错误（缺字段/重复 code/order 非正整数）；
    `sync_dictionary` 以 kp_id=code 幂等 upsert——首跑 inserted、重跑全跳过、
    内容变更记 updated，不许产生重复行。
"""
import json

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.models.database import Base
from app.services.kb_dictionary import (DictionaryError, load_all_dictionaries,
                                          load_dictionary, sync_dictionary)


# ── 迁移 seam ──────────────────────────────────────────────────────

def _old_shape_table(engine):
    """建一张"迁移前形状"的 knowledge_points（只有现有 9 列，无词典四列）。"""
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE knowledge_points (
                kp_id VARCHAR(64) PRIMARY KEY,
                name VARCHAR(256) NOT NULL,
                category VARCHAR(128),
                description TEXT,
                pubchem_cid VARCHAR(64),
                question_count INTEGER,
                error_rate FLOAT,
                created_at TIMESTAMP,
                updated_at TIMESTAMP
            )
        """))


def test_migration_adds_dictionary_columns_idempotent(tmp_path):
    from app.utils.migrate import migrate_knowledge_points_kb
    engine = create_engine(f"sqlite:///{tmp_path}/m.db")
    _old_shape_table(engine)

    migrate_knowledge_points_kb(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("knowledge_points")}
    assert {"code", "theme", "module", "order_no"} <= cols, "词典四列必须补齐"

    migrate_knowledge_points_kb(engine)  # 重跑：不许报错、不许重复加列
    cols2 = {c["name"] for c in inspect(engine).get_columns("knowledge_points")}
    assert cols2 == cols


def test_migration_on_fresh_create_all_is_noop(tmp_path):
    """全新库（create_all 已含新列）跑迁移 = 无操作，不许炸。"""
    from app.utils.migrate import migrate_knowledge_points_kb
    engine = create_engine(f"sqlite:///{tmp_path}/f.db")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables["knowledge_points"]])
    migrate_knowledge_points_kb(engine)  # 不抛即过


def test_kb_frontmatter_migration_adds_misconceptions_column_idempotent(tmp_path):
    """KB2b：存量 kb_frontmatter 补 misconceptions_json 列，重跑幂等。"""
    from app.utils.migrate import migrate_kb_frontmatter
    engine = create_engine(f"sqlite:///{tmp_path}/kfm.db")
    with engine.begin() as conn:
        conn.execute(text("""
            CREATE TABLE kb_frontmatter (
                kp_id VARCHAR(64) PRIMARY KEY,
                module VARCHAR(32),
                theme VARCHAR(64),
                equations_json TEXT,
                prerequisites_json TEXT,
                source_doc VARCHAR(256),
                updated_at TIMESTAMP
            )
        """))

    migrate_kb_frontmatter(engine)
    cols = {c["name"] for c in inspect(engine).get_columns("kb_frontmatter")}
    assert "misconceptions_json" in cols

    migrate_kb_frontmatter(engine)  # 重跑不炸不重复加列
    cols2 = {c["name"] for c in inspect(engine).get_columns("kb_frontmatter")}
    assert cols2 == cols


# ── 词典 seam ──────────────────────────────────────────────────────

@pytest.fixture()
def dict_path(tmp_path):
    p = tmp_path / "kp.json"
    p.write_text(json.dumps({
        "module": "必修一",
        "items": [
            {"code": "M1-T2-KP06", "name": "氧化还原反应",
             "theme": "T2", "order_no": 8, "description": "电子转移"},
            {"code": "M1-T3-KP01", "name": "原子结构",
             "theme": "T3", "order_no": 28, "description": ""},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_load_rejects_duplicate_code(tmp_path):
    p = tmp_path / "dup.json"
    p.write_text(json.dumps({"items": [
        {"code": "A", "name": "x", "theme": "T", "order_no": 1},
        {"code": "A", "name": "y", "theme": "T", "order_no": 2},
    ]}), encoding="utf-8")
    with pytest.raises(DictionaryError, match="重复"):
        load_dictionary(str(p))


def test_load_rejects_missing_fields_and_bad_order(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text(json.dumps({"items": [
        {"code": "A", "name": "", "theme": "T", "order_no": 0},
    ]}), encoding="utf-8")
    with pytest.raises(DictionaryError):
        load_dictionary(str(p))


def test_real_m1_dictionary_loads():
    """仓库里那份必修一词典必须过校验——防止手改数据把管线改坏。"""
    from pathlib import Path
    p = Path(__file__).resolve().parents[1] / "content" / "kb" / "kp_dictionary_m1.json"
    data = load_dictionary(str(p))
    assert len(data["items"]) >= 40, "必修一词典应达到 spec 下限 40 条"
    order = sorted(it["order_no"] for it in data["items"])
    assert order == list(range(1, len(order) + 1)), "order_no 必须 1..N 连续不重"
    themes = {it["theme"].split()[0] for it in data["items"]}
    assert themes <= {"T1", "T2", "T3", "T4", "T5"}, "theme 必须落在课标五主题"


def test_real_m2_dictionary_loads():
    """必修二词典：38 条、order 连续、主题合法、与 M1 不重码不重名。"""
    from pathlib import Path
    kb = Path(__file__).resolve().parents[1] / "content" / "kb"
    m1 = load_dictionary(str(kb / "kp_dictionary_m1.json"))["items"]
    data = load_dictionary(str(kb / "kp_dictionary_m2.json"))
    items = data["items"]
    assert len(items) == 39, "必修二词典 38 条 + KB4c 磷及其化合物（374 清单审计 gap）"
    assert sorted(it["order_no"] for it in items) == list(range(1, len(items) + 1))
    themes = {it["theme"].split()[0] for it in items}
    assert themes <= {"T1", "T2", "T3", "T4", "T5"}
    assert {it["code"] for it in items}.isdisjoint({it["code"] for it in m1})
    assert {it["name"] for it in items}.isdisjoint({it["name"] for it in m1}), \
        "同名知识点会让文档标题与检索来源混淆"


def test_load_all_dictionaries_merges_and_rejects_cross_duplicate(tmp_path):
    """摄取 known_codes 必须覆盖全册；跨文件重码要在加载期炸。"""
    from pathlib import Path
    kb = Path(__file__).resolve().parents[1] / "content" / "kb"
    data = load_all_dictionaries(str(kb))
    codes = {it["code"] for it in data["items"]}
    assert {"M1-T2-KP06", "M2-T4-KP12", "M3-T2-KP04",
            "M4-T2-KP04", "M5-T2-KP04", "M6-T1-KP01",
            "M1-T2-KP28", "M2-T2-KP13"} <= codes
    assert data["sources"] == ["kp_dictionary_m1.json", "kp_dictionary_m2.json",
                               "kp_dictionary_m3.json", "kp_dictionary_m4.json",
                               "kp_dictionary_m5.json", "kp_dictionary_m6.json"]

    bad = tmp_path / "d"
    bad.mkdir()
    for name in ("kp_dictionary_m1.json", "kp_dictionary_m2.json"):
        (bad / name).write_text(json.dumps({"items": [
            {"code": "X-01", "name": "x", "theme": "T", "order_no": 1}]},
            ensure_ascii=False), encoding="utf-8")
    with pytest.raises(DictionaryError, match="跨词典文件重复"):
        load_all_dictionaries(str(bad))


def test_real_m3_dictionary_loads():
    """M3 预研稿：32 条、order 连续、三主题（化学反应原理模块）、跨册不重码。"""
    from pathlib import Path
    kb = Path(__file__).resolve().parents[1] / "content" / "kb"
    m1 = load_dictionary(str(kb / "kp_dictionary_m1.json"))["items"]
    m2 = load_dictionary(str(kb / "kp_dictionary_m2.json"))["items"]
    data = load_dictionary(str(kb / "kp_dictionary_m3.json"))
    items = data["items"]
    assert len(items) == 32, "M3 预研稿按人教选必一审定 32 条"
    assert sorted(it["order_no"] for it in items) == list(range(1, len(items) + 1))
    themes = {it["theme"].split()[0] for it in items}
    assert themes == {"T1", "T2", "T3"}, "化学反应原理模块三主题"
    prev = {it["code"] for it in m1} | {it["code"] for it in m2}
    assert {it["code"] for it in items}.isdisjoint(prev)
    assert {it["name"] for it in items}.isdisjoint({it["name"] for it in m1 + m2})


def test_sync_is_idempotent_upsert(dict_path, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/s.db")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables["knowledge_points"]])
    Session = sessionmaker(bind=engine)
    data = load_dictionary(dict_path)

    with Session() as db:
        report = sync_dictionary(db, data)
        db.commit()  # 提交归调用方（与 CLI/真实用法同口径）
    assert report["inserted"] == 2 and report["updated"] == 0

    with Session() as db:
        report2 = sync_dictionary(db, data)  # 重跑：全跳过，不许重复行
        assert report2["inserted"] == 0 and report2["updated"] == 0
        from app.models.database import KnowledgePoint
        assert db.query(KnowledgePoint).count() == 2
        row = db.query(KnowledgePoint).filter_by(code="M1-T2-KP06").one()
        assert row.name == "氧化还原反应" and row.module == "必修一" and row.order_no == 8


def test_sync_updates_changed_content(dict_path, tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/u.db")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables["knowledge_points"]])
    Session = sessionmaker(bind=engine)
    data = load_dictionary(dict_path)
    with Session() as db:
        sync_dictionary(db, data)
        db.commit()

    data["items"][0]["name"] = "氧化还原反应（修订）"
    with Session() as db:
        report = sync_dictionary(db, data)
        db.commit()
    assert report["updated"] == 1 and report["inserted"] == 0
    with Session() as db:
        from app.models.database import KnowledgePoint
        row = db.query(KnowledgePoint).filter_by(code="M1-T2-KP06").one()
        assert row.name == "氧化还原反应（修订）"
