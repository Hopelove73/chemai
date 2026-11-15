"""审定映射导入（KB2c）：kp_tag_map_m*.json 的 mapped 桶 → kp_tag_map 表。

migrate.py 的 migrate_kb_tag_map 注释承诺的导入方。真源是 content/kb/
kp_tag_map_m*.json（老师审定）；本模块把 mapped 桶逐条 upsert：
  · 新标签 insert；
  · 同标签同 kp → 跳过（幂等）；
  · 同标签换了 kp → update（JSON 是审定真源，改了就该生效）；
  · kp 不在任何词典文件里 → ValueError（引用错必须在落库前炸）。
tag_count/stats 等审计字段不进表——表只存判定结果。
CLI：python -m app.services.kb_tag_map_import [映射.json ...]
"""
import json
import logging
from pathlib import Path

from sqlalchemy import text

logger = logging.getLogger(__name__)

KB_DIR = Path(__file__).resolve().parents[2] / "content" / "kb"
REQUIRED = ("kp",)


def import_tag_map(engine, path: str, known_codes: set) -> dict:
    """导入单个映射 JSON 的 mapped 桶，返回 {inserted, skipped, updated}。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    mapped = data.get("mapped") or {}
    report = {"inserted": 0, "skipped": 0, "updated": 0}
    with engine.begin() as conn:
        for tag, entry in mapped.items():
            if tag == "_comment":
                continue
            kp = (entry or {}).get("kp") or ""
            if kp not in known_codes:
                raise ValueError(f"{Path(path).name}: 标签 {tag!r} 的 kp {kp!r} 不在词典")
            row = conn.execute(
                text("SELECT kp_id FROM kp_tag_map WHERE tag_text = :t"),
                {"t": tag}).fetchone()
            fields = {
                "t": tag, "k": kp,
                "c": entry.get("confidence"),
                "s": entry.get("status") or "reviewed",
                "b": entry.get("accepted_by"),
                "at": entry.get("accepted_at"),
            }
            if row is None:
                conn.execute(text(
                    "INSERT INTO kp_tag_map (tag_text, kp_id, confidence, status, "
                    "accepted_by, accepted_at) VALUES (:t, :k, :c, :s, :b, :at)"),
                    fields)
                report["inserted"] += 1
            elif row[0] == kp:
                report["skipped"] += 1
            else:
                conn.execute(text(
                    "UPDATE kp_tag_map SET kp_id=:k, confidence=:c, status=:s, "
                    "accepted_by=:b, accepted_at=:at WHERE tag_text=:t"), fields)
                report["updated"] += 1
    return report


def main(argv=None) -> int:
    import sys
    from app.models.database import get_engine
    from app.services.kb_dictionary import load_all_dictionaries
    args = list(sys.argv[1:] if argv is None else argv)
    files = [Path(a) for a in args] if args else sorted(KB_DIR.glob("kp_tag_map_m*.json"))
    if not files:
        print(f"[ERR] 没有映射文件: {KB_DIR}")
        return 2
    known = {it["code"] for it in load_all_dictionaries(str(KB_DIR))["items"]}
    total = {"files": len(files), "inserted": 0, "skipped": 0, "updated": 0}
    for f in files:
        report = import_tag_map(get_engine(), str(f), known)
        for k in ("inserted", "skipped", "updated"):
            total[k] += report[k]
        print(f"[OK] {f.name}: {report}")
    print(f"[OK] 导入完成: {total}")
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
