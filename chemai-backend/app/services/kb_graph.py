"""图谱服务（KB-T11）：prerequisite 环检测 + 物质 upsert。

环检测语义：prerequisite 边 (from_kp → to_kp) 表示"from 是 to 的前置"。
提议一条 from→to 边会成环 ⟺ 从 to 出发沿**已接受**的 prerequisite 边能走到 from。
只认 accepted+prerequisite：提议队列里的边还没被老师接受，不该卡死后续提议；
relates_to 是"知识点↔物质"关联，无方向语义，不参与环。
"""
import json
import logging

logger = logging.getLogger(__name__)


def would_create_cycle(conn, from_kp: str, to_kp: str) -> bool:
    """加 from→to 前置边是否成环。conn 是已打开的 SQLAlchemy 连接。"""
    if from_kp == to_kp:
        return True
    # BFS：从 to_kp 出发，沿"谁是谁的前置"的方向（from→to）前进，能回到 from 即成环
    frontier = [to_kp]
    visited = {to_kp}
    while frontier:
        current = frontier.pop()
        rows = conn.execute(text(
            "SELECT to_kp FROM knowledge_edges "
            "WHERE from_kp = :cur AND edge_type = 'prerequisite' "
            "AND status = 'active'"), {"cur": current}).fetchall()
        for (nxt,) in rows:
            if nxt == from_kp:
                return True
            if nxt not in visited:
                visited.add(nxt)
                frontier.append(nxt)
    return False


def upsert_substance(db, formula: str, name: str, source_doc: str = "",
                     aliases: list | None = None) -> str:
    """按规范化化学式 upsert 物质节点，返回 substance_id。幂等。"""
    from app.models.database import KBSubstance

    sid = "sub-" + "".join(c for c in formula if c.isalnum()) or formula
    row = db.query(KBSubstance).filter_by(substance_id=sid).first()
    if row is None:
        row = KBSubstance(substance_id=sid, name=name, formula=formula,
                          aliases_json=json.dumps([formula, name] + (aliases or []),
                                                  ensure_ascii=False),
                          source_doc=source_doc)
        db.add(row)
    else:
        row.name = name or row.name
        if source_doc and not row.source_doc:
            row.source_doc = source_doc
    return sid


# 迁移函数在 app.utils.migrate 里 import 本模块会形成环依赖（migrate 被 app 启动拉起），
# 所以环检测的 SQL 用 text() 直写，不在模块顶层 import database。
from sqlalchemy import text  # noqa: E402
