"""审校包落库流（KB-T12）：*.proposals.json → 提议边/物质落库。

设计 §4：文档审校与图谱审校一趟——老师审文档顺手批三元组，本模块把
批过的提议落进队列（status='proposed'），/kmap 的接受流负责转 accepted。
纪律：
  · prerequisite 提议**带环检测**：成环的拒绝入队（fail-closed），报告里说明；
  · relates_to 同步 upsert 物质节点（kb_substances 幂等）；
  · 边/物质引用的 kp 必须在词典 codes 中；
  · 幂等：同 (from,to,edge_type,source_doc) 的已存在提议记 duplicates 不重插。
CLI：python -m app.services.kb_graph_import content/kb/必修一
"""
import json
import logging
from pathlib import Path

from sqlalchemy import text

from .kb_graph import would_create_cycle

logger = logging.getLogger(__name__)


def import_proposals(engine, path: str, known_codes: set,
                     proposed_by: str = "teacher:zhou") -> dict:
    """导入单个 .proposals.json 文件或目录（扫 *.proposals.json）。"""
    p = Path(path)
    files = sorted(p.glob("*.proposals.json")) if p.is_dir() else [p]
    report = {"files": len(files), "edges_proposed": 0, "relates_proposed": 0,
              "duplicates": 0, "substances_upserted": 0, "rejected": []}
    for f in files:
        data = json.loads(f.read_text(encoding="utf-8"))
        _import_one(engine, f.name, data, known_codes, proposed_by, report)
    return report


def _import_one(engine, filename: str, data: dict, known_codes: set,
                proposed_by: str, report: dict) -> None:
    kp_self = data.get("kp") or ""
    if kp_self not in known_codes:
        raise ValueError(f"{filename}: kp 不在词典 {kp_self!r}")

    with engine.begin() as conn:
        for edge in data.get("edges", []):
            etype = edge.get("edge_type")
            if etype not in ("prerequisite", "relates_to"):
                report["rejected"].append({"file": filename,
                                           "message": f"非法 edge_type {etype!r}"})
                continue
            if etype == "prerequisite":
                frm, to = edge.get("from_kp", ""), edge.get("to_kp", "")
                if frm not in known_codes or to not in known_codes:
                    raise ValueError(
                        f"{filename}: prerequisite 引用词典外的知识点 {frm}->{to}")
                if would_create_cycle(conn, frm, to):
                    report["rejected"].append({
                        "file": filename,
                        "message": f"先修提议 {frm}->{to} 会成环，拒绝入队"})
                    continue
                dup = conn.execute(text(
                    "SELECT id FROM knowledge_edges WHERE from_kp=:f AND to_kp=:t "
                    "AND edge_type=:e AND status IN ('proposed', 'active')"),
                    {"f": frm, "t": to, "e": etype}).fetchone()
                if dup:
                    report["duplicates"] += 1
                    continue
                conn.execute(text(
                    "INSERT INTO knowledge_edges "
                    "(from_kp, to_kp, status, reason, proposed_by, edge_type, confidence, source_doc) "
                    "VALUES (:f, :t, 'proposed', :reason, :by, :e, :conf, :doc)"),
                    {"f": frm, "t": to, "e": etype, "reason": edge.get("reason", ""),
                     "by": proposed_by, "conf": edge.get("confidence", 0.8),
                     "doc": data.get("doc", filename)})
                report["edges_proposed"] += 1
            else:
                # relates_to（知识点↔物质）：真源是 kb_substances，knowledge_edges
                # 是知识点间边表不收它——物质幂等即可，不另造第二份状态。
                if edge.get("kp") not in known_codes:
                    raise ValueError(
                        f"{filename}: relates_to 引用词典外的知识点 {edge.get('kp')!r}")
                _upsert_substance(conn, edge, data.get("doc", ""), report)


def _upsert_substance(conn, edge: dict, source_doc: str, report: dict) -> None:
    # 复用 kb_graph.upsert_substance 的 ORM 逻辑：这里只有 conn，
    # 改用 SQL 直写以避免在事务中混用 ORM session。
    sid = "sub-" + "".join(c for c in edge.get("formula", "") if c.isalnum())
    if not sid or sid == "sub-":
        return
    name = edge.get("substance") or edge.get("formula", "")
    aliases = json.dumps([edge.get("formula", ""), name], ensure_ascii=False)
    row = conn.execute(text("SELECT substance_id FROM kb_substances WHERE substance_id=:s"),
                       {"s": sid}).fetchone()
    if row is None:
        conn.execute(text(
            "INSERT INTO kb_substances (substance_id, name, aliases_json, source_doc) "
            "VALUES (:s, :n, :a, :d)"), {"s": sid, "n": name, "a": aliases, "d": source_doc})
        report["substances_upserted"] += 1


def main(argv=None) -> int:
    from app.models.database import get_engine
    path = argv[0] if argv else str(
        Path(__file__).resolve().parents[2] / "content" / "kb" / "必修一")
    from app.services.kb_dictionary import load_all_dictionaries
    dic_dir = Path(__file__).resolve().parents[2] / "content" / "kb"
    known = {it["code"] for it in load_all_dictionaries(str(dic_dir))["items"]}
    report = import_proposals(get_engine(), path, known)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
