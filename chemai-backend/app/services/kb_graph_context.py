"""出题接图谱（KB-T15）：按知识点取先修链与方程式素材，注入出题 prompt。

设计 §5.2：出题纯图驱动——先修链控难度阶梯（考先修的题为易）、
kb_frontmatter.equations 做素材池。图谱层任何故障**静默降级**：
出题永不因图谱故障而失败（失败返回空上下文）。
供 agent/tools/generation.py 在调 llm_service 前调用。
"""
import json
import logging

from sqlalchemy import text

from .kb_error_rate import ERROR_RATE_THRESHOLD, error_rates_for_kps

logger = logging.getLogger(__name__)


def kb_graph_context_for_questions(engine, kp_ids: list) -> dict:
    """按知识点（词典 code）取先修链、方程式与易错定向素材。

    返回 {prerequisites, equations, misconceptions, error_rates, hint}。
    engine 为 None / kp 不在图谱中 / 查询失败 → 空上下文（不抛错）。
    """
    out = {"prerequisites": [], "equations": [], "misconceptions": [],
           "error_rates": {}, "hint": ""}
    if engine is None or not kp_ids:
        return out
    try:
        with engine.connect() as conn:
            prereqs: list = []
            seen = set(kp_ids)
            frontier = list(kp_ids)
            depth = 0
            while frontier and depth < 2:  # 先修链上溯 ≤2 跳（与检索增强同深度）
                rows = conn.execute(text(
                    "SELECT from_kp FROM knowledge_edges "
                    "WHERE to_kp IN :ks AND edge_type='prerequisite' "
                    "AND status='active'"
                ).bindparams().bindparams(
                    __import__("sqlalchemy").bindparam("ks", expanding=True)
                ), {"ks": list(frontier)}).fetchall()
                nxt = []
                for (up,) in rows:
                    if up not in seen:
                        seen.add(up)
                        prereqs.append(up)
                        nxt.append(up)
                frontier = nxt
                depth += 1

            equations: list = []
            misconceptions: list = []
            all_kps = list(seen)
            if all_kps:
                rows = conn.execute(text(
                    "SELECT kp_id, equations_json FROM kb_frontmatter WHERE kp_id IN :ks"
                ).bindparams().bindparams(
                    __import__("sqlalchemy").bindparam("ks", expanding=True)
                ), {"ks": all_kps}).fetchall()
                for kp, raw in rows:
                    try:
                        eqs = json.loads(raw) if isinstance(raw, str) else (raw or [])
                    except Exception:
                        eqs = []
                    for eq in eqs:
                        equations.append(f"{eq}（{kp}）")
                # KB2b：易错三元。单独查询——存量库/旧测试夹具可能没有该列，
                # 查询失败只丢易错段，不拖垮先修链与方程式（同一降级纪律）。
                try:
                    mis_rows = conn.execute(text(
                        "SELECT kp_id, misconceptions_json FROM kb_frontmatter "
                        "WHERE kp_id IN :ks"
                    ).bindparams().bindparams(
                        __import__("sqlalchemy").bindparam("ks", expanding=True)
                    ), {"ks": all_kps}).fetchall()
                    for kp, mis_raw in mis_rows:
                        try:
                            mis = json.loads(mis_raw) if isinstance(mis_raw, str) \
                                else (mis_raw or [])
                        except Exception:
                            mis = []
                        for m in mis:
                            if isinstance(m, dict) and m.get("wrong") and m.get("right"):
                                misconceptions.append(
                                    {"kp": kp, "wrong": m["wrong"],
                                     "right": m["right"], "why": m.get("why", "")})
                except Exception:
                    misconceptions = []

            # KB2a：错误率注入（作答 ≥MIN_ANSWERS 且 ≥ERROR_RATE_THRESHOLD 的知识点）
            try:
                error_rates = error_rates_for_kps(conn, all_kps) if all_kps else {}
            except Exception:
                error_rates = {}
            hot = {kp: v for kp, v in error_rates.items()
                   if v.get("error_rate", 0) >= ERROR_RATE_THRESHOLD}

        hint_parts = []
        if prereqs:
            hint_parts.append(
                "先修知识点（可出 1-2 道较易的基础题，考查学生对这些前置的掌握）："
                + "、".join(prereqs))
        if equations:
            hint_parts.append(
                "可使用的方程式素材（入题时确保配平并注明条件）：\n"
                + "\n".join(equations))
        if misconceptions:
            hint_parts.append(
                "常见易错点（陷阱定向：错误说法不要当正确答案，可设计为干扰项）：\n"
                + "\n".join(f"- 误：{m['wrong']} → 正：{m['right']}"
                            + (f"（错因：{m['why']}）" if m["why"] else "")
                            for m in misconceptions))
        for kp, v in hot.items():
            hint_parts.append(
                f"该知识点学生错误率 {v['error_rate']:.0%}（{v['answers']} 次作答，高频易错），"
                "易错方向由老师出题时人工取舍")
        out["prerequisites"] = prereqs
        out["equations"] = equations
        out["misconceptions"] = misconceptions
        out["error_rates"] = error_rates
        out["hint"] = "\n".join(hint_parts)
    except Exception as e:
        logger.warning("出题图谱增强降级: %s", e)
        out = {"prerequisites": [], "equations": [], "misconceptions": [],
               "error_rates": {}, "hint": ""}
    return out
