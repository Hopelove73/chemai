"""KB2c 审定映射导入（seam）：kp_tag_map_m*.json mapped 桶 → kp_tag_map 表。

钉子：幂等（重跑全跳过）、换 kp 要更新、引用词典外 kp 当场炸、
仓库两份映射文件能合并导入（85 + 13 条）。
"""
import json

import pytest
from sqlalchemy import create_engine, text

from app.models.database import Base
from app.services.kb_tag_map_import import import_tag_map


def _engine(tmp_path, name="t.db"):
    engine = create_engine(f"sqlite:///{tmp_path}/{name}")
    Base.metadata.create_all(engine, tables=[Base.metadata.tables["kp_tag_map"]])
    return engine


def _write(tmp_path, mapped):
    p = tmp_path / "map.json"
    p.write_text(json.dumps({"mapped": mapped}, ensure_ascii=False), encoding="utf-8")
    return str(p)


def test_import_inserts_mapped_bucket_and_skips_comment(tmp_path):
    engine = _engine(tmp_path)
    path = _write(tmp_path, {
        "_comment": "说明",
        "硫及其化合物": {"kp": "M2-T2-KP01", "confidence": "medium",
                   "status": "reviewed", "accepted_by": "t",
                   "accepted_at": "2026-10-27"},
    })
    report = import_tag_map(engine, path, {"M2-T2-KP01"})
    assert report == {"inserted": 1, "skipped": 0, "updated": 0}
    with engine.connect() as conn:
        row = conn.execute(text(
            "SELECT kp_id, confidence, accepted_by FROM kp_tag_map")).fetchone()
    assert row == ("M2-T2-KP01", "medium", "t")


def test_import_is_idempotent_and_updates_changed_kp(tmp_path):
    engine = _engine(tmp_path)
    mapped = {"氨的性质": {"kp": "M2-T2-KP07", "confidence": "high"}}
    path = _write(tmp_path, mapped)
    import_tag_map(engine, path, {"M2-T2-KP07", "M2-T2-KP08"})
    assert import_tag_map(engine, path, {"M2-T2-KP07"}) == \
        {"inserted": 0, "skipped": 1, "updated": 0}

    # 审定整改：同一标签改挂另一知识点 → UPDATE，不许重复行
    _write(tmp_path, {"氨的性质": {"kp": "M2-T2-KP08", "confidence": "high"}})
    report = import_tag_map(engine, path, {"M2-T2-KP07", "M2-T2-KP08"})
    assert report == {"inserted": 0, "skipped": 0, "updated": 1}
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM kp_tag_map")).scalar() == 1
        assert conn.execute(text("SELECT kp_id FROM kp_tag_map")).scalar() == "M2-T2-KP08"


def test_import_rejects_kp_outside_dictionary(tmp_path):
    engine = _engine(tmp_path)
    path = _write(tmp_path, {"硫及其化合物": {"kp": "M2-T2-KP99"}})
    with pytest.raises(ValueError, match="M2-T2-KP99"):
        import_tag_map(engine, path, {"M2-T2-KP01"})
    with engine.connect() as conn:
        assert conn.execute(text("SELECT COUNT(*) FROM kp_tag_map")).scalar() == 0


def test_repo_tag_maps_import_end_to_end(tmp_path):
    """仓库映射文件合并导入：M1 90 + M2 14 + M3 44 + M6 12 = 160 条（KB4a 12 + KB4b 电子转移改道 + KB4c 镁/磷/铁盐转正），重导全跳过。"""
    from pathlib import Path
    from app.services.kb_dictionary import load_all_dictionaries
    kb = Path(__file__).resolve().parents[1] / "content" / "kb"
    known = {it["code"] for it in load_all_dictionaries(str(kb))["items"]}
    engine = _engine(tmp_path)
    total = {"inserted": 0, "skipped": 0, "updated": 0}
    for name in ("kp_tag_map_m1.json", "kp_tag_map_m2.json", "kp_tag_map_m3.json",
                 "kp_tag_map_m6.json"):
        r = import_tag_map(engine, str(kb / name), known)
        for k in total:
            total[k] += r[k]
    assert total == {"inserted": 160, "skipped": 0, "updated": 0}
    again = import_tag_map(engine, str(kb / "kp_tag_map_m2.json"), known)
    assert again == {"inserted": 0, "skipped": 14, "updated": 0}
