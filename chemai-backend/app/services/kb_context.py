"""kb_context（KB-T13）：知识库检索装配——向量 top4 + 图增强 + 降级 + 无命中阈值。

接入点（T14）：chemistry_tutor 组 prompt 前调用 build_kb_context，text 非空
则拼进「参考资料」段；命中与降级状态写入回合日志（ADR-0004：审计不上前端）。

形态（设计 §5.1，grill Q5）：
  · 向量主导：Chroma `kb_chunks` 余弦 top4（默认不过滤册别，检索质量问题由
    语料质量与评测校准，不加旋钮）；
  · 图增强：命中块的知识点上溯先修链（≤2 跳、只认 active prerequisite）+
    该 kp 文档 frontmatter 的 equations，作为附加上下文段；
  · 无命中阈值：最高余弦距离 > KB_MISS_THRESHOLD → miss（模型按纪律直说不知道）；
  · 降级：Chroma 不可用/超时 → 空资料不阻塞回合。
"""
import json
import logging
import time

from sqlalchemy import text

logger = logging.getLogger(__name__)

COLLECTION_NAME = "kb_chunks"

# 拒答文案（两条路径共用：向量距离不够、或命中块全被管理员禁用）
KB_MISS_TEXT = ("【知识库边界】知识库中没有与该问题直接相关的内容。"
                "如不确定，请明确说明本知识库暂未覆盖。")
KB_MISS_THRESHOLD = 0.39      # 强命中线：最好块距离 ≤0.39 照常作答
KB_WEAK_THRESHOLD = 0.45      # 拒答线：>0.45 视为不相关；0.39~0.45 为弱命中带
# KB4a（2026-10-28）引入"弱命中"中间态，终结阈值逐年下探：语料覆盖越全，
# "未覆盖内容"的语义近邻越近（先例 0.45→0.40→0.39），单硬阈值必然把
# 擦线的覆盖内问题误拒（17 道金集题落在 0.39-0.45 带）。三态口径：
#   d ≤ 0.39        ok    照常作答；
#   0.39 < d ≤ 0.45 weak  照常给资料，text 头部加覆盖提示，coverage=weak
#                         （tutor 照常注入、审计/评测单列观察）；
#   d > 0.45        miss  拒答边界纪律。
# 阈值仍随语料扩大复扫（评测 --threshold/--weak-threshold 扫描定值）。
PREREQ_MAX_DEPTH = 2
KB_MISS_SENTINEL = ""         # 降级/无命中时 text 为空串——prompt 不拼资料段
KB_WEAK_NOTE = ("【覆盖提示】知识库对该问题的直接覆盖较浅（弱命中）。"
                "回答可参考以下资料，但须注明这是基于有限资料的推断，"
                "结论保持谨慎；与资料冲突时以教材为准。")


def build_kb_context(question: str, collection, encoder, graph_engine,
                     top_k: int = 4, miss_threshold: float = None,
                     weak_threshold: float = None,
                     exclude_ids: set | None = None,
                     graph_enabled: bool = True) -> dict:
    """检索装配。返回 {text, hits, mode: ok|weak|miss|degraded, coverage, degraded, ms}。

    text 为空 = 本轮无资料可拼（prompt 侧跳过资料段）。
    miss_threshold/weak_threshold 可调（评测校准用），缺省 KB_MISS_THRESHOLD /
    KB_WEAK_THRESHOLD。mode=weak 时 text 头部带覆盖提示，hits 照常交付。
    exclude_ids：被管理员禁用的分块（kb_chunk_overrides），检索后按 id 排除；
    多取一些候选以抵消被排除的块。
    """
    exclude_ids = exclude_ids or set()
    threshold = KB_MISS_THRESHOLD if miss_threshold is None else miss_threshold
    weak_line = KB_WEAK_THRESHOLD if weak_threshold is None else weak_threshold
    started = time.time()
    out = {"text": KB_MISS_SENTINEL, "hits": [], "mode": "degraded",
           "degraded": True, "ms": 0,
           # 检索配置快照：问答日志/召回测试都要能回答「当时用的哪套参数」
           "config": {"top_k": top_k, "miss_threshold": threshold,
                      "weak_threshold": weak_line, "graph": graph_enabled}}
    try:
        query_vec = encoder.encode([question])[0]
        # SentenceTransformer 返回 numpy 数组，chroma 0.4.22 只收 list
        query_vec = query_vec.tolist() if hasattr(query_vec, "tolist") else list(query_vec)
        res = collection.query(query_embeddings=[query_vec],
                               n_results=min(top_k + len(exclude_ids), 20),
                               include=["metadatas", "documents", "distances"])
    except Exception as e:
        logger.warning("kb 检索降级: %s", e)
        out["ms"] = int((time.time() - started) * 1000)
        return out

    ids = res["ids"][0] or []
    if not ids:
        out["ms"] = int((time.time() - started) * 1000)
        return out

    metas = res["metadatas"][0] or []
    docs = res["documents"][0] or []
    dists = res["distances"][0] or []

    # 拒答线：最好的一块都不相关 → miss（模型直说不知道，防幻觉硬闸门）
    if not dists or min(dists) > weak_line:
        out["mode"] = "miss"
        out["text"] = KB_MISS_TEXT
        out["ms"] = int((time.time() - started) * 1000)
        return out

    # 相邻块抑制：同文档同节只留最高分（切块的重叠不许变成结果重复）
    hits, seen = [], set()
    for cid, meta, doc, dist in zip(ids, metas, docs, dists):
        if cid in exclude_ids:      # 管理员禁用的块不参与装配
            continue
        if len(hits) >= top_k:
            break
        key = (meta.get("source_path"), meta.get("section_no"))
        if key in seen:
            continue
        seen.add(key)
        hits.append({"id": cid, "title_path": meta.get("title_path", ""),
                     "body": meta.get("body") or doc,
                     "source_path": meta.get("source_path", ""),
                     "kp_ids": _loads(meta.get("kp_ids")), "distance": dist,
                     "section_no": meta.get("section_no")})

    if not hits:
        # 命中的块全被禁用（或全被相邻块抑制）→ 与"没检索到"同义，走拒答
        out["mode"] = "miss"
        out["text"] = KB_MISS_TEXT
        out["ms"] = int((time.time() - started) * 1000)
        return out

    enh = _graph_enhancement(hits, graph_engine) if graph_enabled else {"extra_context": ""}
    # 弱命中带：资料照常交付，text 头部加覆盖提示（KB4a 三态中间态）
    weak_hit = min(dists) > threshold
    out.update({"hits": hits, "mode": "weak" if weak_hit else "ok",
                "coverage": "weak" if weak_hit else "strong",
                "degraded": False,
                "text": (f"{KB_WEAK_NOTE}\n{_render(hits, enh)}" if weak_hit
                         else _render(hits, enh)),
                "enhancement": enh,
                "ms": int((time.time() - started) * 1000)})
    return out


def _loads(raw) -> list:
    try:
        return json.loads(raw) if isinstance(raw, str) else (raw or [])
    except Exception:
        return []


def prerequisite_chain(conn, kp_id: str, max_depth: int = PREREQ_MAX_DEPTH) -> list:
    """上溯先修链（BFS ≤max_depth，只认 active prerequisite）。"""
    chain, frontier, visited = [], [kp_id], {kp_id}
    depth = 0
    while frontier and depth < max_depth:
        nxt_level = []
        for cur in frontier:
            rows = conn.execute(text(
                "SELECT from_kp FROM knowledge_edges "
                "WHERE to_kp = :cur AND edge_type='prerequisite' AND status='active'"),
                {"cur": cur}).fetchall()
            for (up,) in rows:
                if up not in visited:
                    visited.add(up)
                    chain.append(up)
                    nxt_level.append(up)
        frontier = nxt_level
        depth += 1
    return chain


def _graph_enhancement(hits: list, graph_engine) -> dict:
    """命中 kp 的并集 → 先修链 + 方程式 + 易错定向。图挂了降级为空（向量结果照常交付）。"""
    if graph_engine is None:
        return {"prerequisites": [], "equations": [], "misconceptions": []}
    try:
        kps = {k for h in hits for k in h["kp_ids"]}
        if not kps:
            return {"prerequisites": [], "equations": [], "misconceptions": []}
        with graph_engine.connect() as conn:
            prereqs: list = []
            seen = set()
            for kp in kps:
                for up in prerequisite_chain(conn, kp):
                    if up not in seen and up not in kps:  # 已命中知识点不重复报先修
                        seen.add(up)
                        prereqs.append(up)
            equations: list = []
            if kps:
                rows = conn.execute(text(
                    "SELECT kp_id, equations_json FROM kb_frontmatter WHERE kp_id IN :ks"
                ).bindparams().bindparams(__import__('sqlalchemy').bindparam(
                    "ks", expanding=True)), {"ks": list(kps)}).fetchall()
                for kp_id, raw in rows:
                    for eq in _loads(raw):
                        equations.append({"kp": kp_id, "equation": eq})
            misconceptions: list = _top_misconceptions(hits, conn)
        return {"prerequisites": prereqs, "equations": equations,
                "misconceptions": misconceptions}
    except Exception as e:
        logger.warning("kb 图增强降级: %s", e)
        return {"prerequisites": [], "equations": [], "misconceptions": []}


def _top_misconceptions(hits: list, conn) -> list:
    """KB2b：检索侧易错定向段。只取最相关命中块的知识点（hits[0] 的 kp_ids），
    上限 3 条——这是给 tutor 的提醒，不是全文背诵。存量库无该列 → 静默空。"""
    try:
        top_kps = list(hits[0]["kp_ids"]) if hits else []
        if not top_kps:
            return []
        rows = conn.execute(text(
            "SELECT kp_id, misconceptions_json FROM kb_frontmatter WHERE kp_id IN :ks"
        ).bindparams().bindparams(__import__('sqlalchemy').bindparam(
            "ks", expanding=True)), {"ks": top_kps}).fetchall()
        out: list = []
        for kp_id, raw in rows:
            for m in _loads(raw):
                if isinstance(m, dict) and m.get("wrong") and m.get("right"):
                    out.append({"kp": kp_id, "wrong": m["wrong"],
                                "right": m["right"], "why": m.get("why", "")})
                if len(out) >= 3:
                    return out
        return out
    except Exception:
        return []


def _render(hits: list, enh: dict) -> str:
    """装配「参考资料」段：每块带来源路径；图增强单独小节。"""
    lines = ["【参考资料】（来自知识库，回答时优先依据以下内容）"]
    for i, h in enumerate(hits, 1):
        lines.append(f"[{i}] {h['title_path']}（来源: {h['source_path']}）")
        lines.append(h["body"])
    prereqs = enh.get("prerequisites") or []
    equations = enh.get("equations") or []
    mis = enh.get("misconceptions") or []
    if prereqs or equations or mis:
        lines.append("【关联知识点】")
        if prereqs:
            lines.append("先修：讲解前可先确认学生是否掌握 " + "、".join(prereqs))
        for e in equations:
            lines.append(f"相关方程式（{e['kp']}）：{e['equation']}")
    if mis:
        lines.append("【常见易错提醒】（该知识点学生高频错误，回答时注意针对性纠正）")
        for m in mis:
            line = f"误：{m['wrong']} → 正：{m['right']}"
            if m.get("why"):
                line += f"（错因：{m['why']}）"
            lines.append(line)
    return "\n".join(lines)
