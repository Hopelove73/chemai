"""知识地图服务（S6 / knowledge-edges + knowledge-map-views spec）。

纯函数内核（单测钉住）：三态状态机（锁定/就绪/精通，阈值与四级映射同源）、
环检测、拓扑分层。证据统计（共现/错因迁移）为提议工具提供数据面；
边内容本身由 LLM 依证据提议 + 教师逐边采纳（无外部图谱数据，Q3 定案 B）。
"""

from __future__ import annotations

from app.services.mastery_service import (
    MASTERY_MASTERED_AT,  # noqa: F401  就绪过滤与四级映射同源常量
    MASTERY_PROFICIENT_AT,
)

READY_AT = MASTERY_PROFICIENT_AT   # 前置达标线 = 熟练线 0.7
MASTERED_AT = MASTERY_MASTERED_AT  # 精通线 0.9


# ── 状态机（REQ-KMV-001，纯函数）────────────────────────────────────────

def kp_state(kp: str, mastery: dict[str, float],
             prereqs: dict[str, list[str]], states: dict[str, str] | None = None) -> str:
    """单点三态：locked / ready / mastered。

    - 任一 active 前置未达 READY_AT → locked
    - 全部前置达标且自身 < MASTERED_AT → ready（无边知识点直通就绪判定）
    - 自身 ≥ MASTERED_AT → mastered（前置无关——已精通不因前置回锁，避免误导性回退）
    自身无 BKT 结论（样本不足）按 0 处理（不精通、按前置规则判锁定/就绪）。
    """
    if states is None:
        states = {}
    m = mastery.get(kp)
    m = float(m) if m is not None else 0.0
    if m >= MASTERED_AT:
        return "mastered"
    for pre in prereqs.get(kp, []):
        pre_state = states.get(pre)
        if pre_state == "locked":
            return "locked"
        pre_m = mastery.get(pre)
        pre_m = float(pre_m) if pre_m is not None else 0.0
        if pre_m < READY_AT:
            return "locked"
    return "ready"


def resolve_states(mastery: dict[str, float],
                   active_edges: list[tuple[str, str]]) -> dict[str, str]:
    """全图三态解析。按拓扑分层序计算（有环时环内点最后处理——accept 侧已拒环，
    这里是防御性兜底）。"""
    prereqs: dict[str, list[str]] = {}
    for a, b in active_edges:
        prereqs.setdefault(b, []).append(a)
    nodes = set(mastery) | {n for e in active_edges for n in e}
    states: dict[str, str] = {}
    for layer in topo_layers(active_edges, nodes):
        for kp in layer:
            states[kp] = kp_state(kp, mastery, prereqs, states)
    return states


def topo_layers(edges: list[tuple[str, str]], nodes: set[str]) -> list[list[str]]:
    """Kahn 分层（环防御：环内节点并入最后一层，不丢点）。"""
    prereqs: dict[str, set[str]] = {n: set() for n in nodes}
    children: dict[str, set[str]] = {n: set() for n in nodes}
    for a, b in edges:
        if a in nodes and b in nodes:
            prereqs[b].add(a)
            children[a].add(b)
    layers: list[list[str]] = []
    placed: set[str] = set()
    current = [n for n in nodes if not prereqs[n]]
    while current:
        layers.append(sorted(current))
        placed |= set(current)
        nxt: set[str] = set()
        for n in current:
            for c in children[n]:
                if c not in placed:
                    prereqs[c] -= {n}
                    if not prereqs[c]:
                        nxt.add(c)
        current = sorted(nxt)
    rest = sorted(nodes - placed)
    if rest:
        layers.append(rest)  # 环防御兜底：环内点最后一层可见（accept 已拒环，此处纯防御）
    return layers


def find_cycle(edges: list[tuple[str, str]], extra: tuple[str, str] | None = None) -> list[str] | None:
    """环检测：返回环路径（节点序列），无环 None。extra 为待加入边。"""
    all_edges = list(edges) + ([extra] if extra else [])
    graph: dict[str, set[str]] = {}
    for a, b in all_edges:
        graph.setdefault(a, set()).add(b)
    state: dict[str, int] = {}  # 0=未访问 1=在栈 2=完成
    path: list[str] = []

    def dfs(n: str) -> list[str] | None:
        if state.get(n) == 1:
            i = path.index(n)
            return path[i:] + [n]
        if state.get(n) == 2:
            return None
        state[n] = 1
        path.append(n)
        for c in sorted(graph.get(n, ())):
            r = dfs(c)
            if r:
                return r
        state[n] = 2
        path.pop()
        return None

    for start in sorted(graph):
        r = dfs(start)
        if r:
            return r
    return None


# ── 证据统计（REQ-KED-001：共现 + 错因迁移）────────────────────────────

def evidence_stats(db, limit: int = 20) -> dict:
    """提议工具的数据面。

    - co_occurrence：同卷（record_id）题目知识点对的共现次数（top N）
    - error_migration：前置未掌握（BKT<0.4）学生在后继知识点错误显著的
      (前置, 后继) 计数——以「该生前置错且后继也错」的作答对计数近似
    """
    from sqlalchemy import text

    co_rows = db.execute(text(
        "SELECT q1.knowledge_points AS a, q2.knowledge_points AS b, COUNT(*) AS n "
        "FROM questions q1 JOIN questions q2 "
        "  ON q1.record_id = q2.record_id AND q1.question_id < q2.question_id "
        "WHERE q1.record_id IS NOT NULL "
        "GROUP BY a, b ORDER BY n DESC LIMIT :lim"
    ), {"lim": limit}).fetchall()

    mig_rows = db.execute(text(
        "SELECT qa.knowledge_points AS pre, qb.knowledge_points AS post, "
        "       COUNT(DISTINCT sa.student_id) AS n "
        "FROM student_answers sa "
        "JOIN questions qa ON qa.question_id = sa.question_id "
        "JOIN student_answers sb ON sb.student_id = sa.student_id AND sb.is_correct = 0 "
        "JOIN questions qb ON qb.question_id = sb.question_id "
        "WHERE sa.is_correct = 0 AND qa.record_id IS NOT NULL "
        "GROUP BY pre, post ORDER BY n DESC LIMIT :lim"
    ), {"lim": limit}).fetchall()

    def _pairs(rows):
        out = []
        for a, b, n in rows:
            ka = _first_kp(a)
            kb = _first_kp(b)
            if ka and kb and ka != kb:
                out.append({"pre": ka, "post": kb, "count": int(n)})
        return out

    return {"co_occurrence": _pairs(co_rows), "error_migration": _pairs(mig_rows)}


def _first_kp(kps_json) -> str:
    import json
    if not kps_json:
        return ""
    if isinstance(kps_json, str):
        try:
            kps_json = json.loads(kps_json)
        except ValueError:
            return ""
    return str(kps_json[0]) if kps_json else ""


def accept_edges(db, edges: list[dict], proposed_by: str) -> dict:
    """教师采纳（REQ-KED-002）：逐条校验环（含已 active 边），环则拒并给出路径。

    edges: [{pre, post, reason}]。返回 {accepted: [...], rejected: [...]}。"""
    from app.models.database import KnowledgeEdge

    active = [(e.from_kp, e.to_kp) for e in
              db.query(KnowledgeEdge).filter(KnowledgeEdge.status == "active").all()]
    accepted, rejected = [], []
    for e in edges:
        pre, post = str(e.get("pre") or ""), str(e.get("post") or "")
        if not pre or not post or pre == post:
            rejected.append({**e, "reason_brief": "空知识点或自环"})
            continue
        cycle = find_cycle(active, (pre, post))
        if cycle:
            rejected.append({**e, "reason_brief": f"成环：{' → '.join(cycle)}"})
            continue
        dup = db.query(KnowledgeEdge).filter(
            KnowledgeEdge.from_kp == pre, KnowledgeEdge.to_kp == post).first()
        if dup:
            dup.status = "active"
            dup.reason = str(e.get("reason") or "")[:512]
            accepted.append({"pre": pre, "post": post, "reenabled": True})
            continue
        db.add(KnowledgeEdge(from_kp=pre, to_kp=post, status="active",
                             reason=str(e.get("reason") or "")[:512],
                             proposed_by=proposed_by))
        accepted.append({"pre": pre, "post": post})
        active.append((pre, post))
    db.commit()
    return {"accepted": accepted, "rejected": rejected}
