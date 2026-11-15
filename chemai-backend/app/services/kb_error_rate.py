"""错误率聚合（KB2a）：student_answers → kp_tag_map → 按词典码聚合。

数据链：题目带标签（questions.knowledge_points JSON）→ kp_tag_map 把标签
映射到词典码 → student_answers.is_correct 聚合出每个码的错误率与作答数。

纪律：作答次数 < MIN_ANSWERS 的码不出现在结果里（样本太小，提示是噪音）；
聚合任何失败由调用方静默降级（出题 hint 不注入）——错误率是增强不是门槛。
"""
import json
import logging
from collections import defaultdict

from sqlalchemy import text

logger = logging.getLogger(__name__)

MIN_ANSWERS = 5
ERROR_RATE_THRESHOLD = 0.3   # 与出题 hint 注入阈值共用（设计 §1）


def error_rates_for_kps(conn, kp_codes: list, class_id: str | None = None) -> dict:
    """按词典码聚合错误率——**作答事件级**口径（2026-10-27 与老师对齐）。

    口径辨析（老师两次抓计数坑的结论）：
      · questions 表的行是"布置实例"，同内容多实例（32625 行 → 307 独立题）
        ——**题目数量**的口径必须去重（191 题映射词典码）；
      · **错误率**按作答事件算：每次作答都是一次真实测量，实例重复不构成
        重复测量——一道题被布置 100 次、作答 500 次，500 次都是有效样本；
      · 一题多标签映射到同一 KP 时只计一次（按首个映射标签去重）。
    class_id（教师星图）：只把作答样本过滤到该班学生，口径/去重/MIN_ANSWERS 全不变。
    返回 {code: {"error_rate": float, "answers": int}}，只含作答 ≥MIN_ANSWERS 的码。
    """
    if not kp_codes:
        return {}
    # 去重子查询：每道独立题（按 content）只取一个规范实例参与统计
    rows = conn.execute(text(
        "SELECT q.content, MIN(q.question_id) AS qid "
        "FROM questions q WHERE q.knowledge_points != '' GROUP BY q.content"
    )).fetchall()
    canonical = []   # (qid, kp)
    seen_qids = set()
    for content, qid in rows:
        qrow = conn.execute(text(
            "SELECT knowledge_points FROM questions WHERE question_id = :q"),
            {"q": qid}).fetchone()
        kps_raw = qrow[0] if qrow else "[]"
        try:
            tags = json.loads(kps_raw) if isinstance(kps_raw, str) else []
        except Exception:
            tags = []
        # 逐标签找首个映射到目标码集合的（首次映射语义，与 kp_tag_map 审定一致）
        kp = None
        for t in tags:
            mapped = conn.execute(text(
                "SELECT kp_id FROM kp_tag_map WHERE tag_text = :t"),
                {"t": t}).fetchone()
            if mapped and mapped[0] in set(kp_codes):
                kp = mapped[0]
                break
        if kp is None:
            continue
        if qid in seen_qids:  # 同内容只留一次（GROUP BY 已保证，双保险）
            continue
        seen_qids.add(qid)
        canonical.append((content, qid, kp))

    if not canonical:
        return {}

    # 作答按**内容**汇总（不是按规范实例）：同内容多实例的作答都是这道
    # 独立题的真实测量（原题行往往没有作答——学生答的是练习副本）。
    _CS = __import__("sqlalchemy").bindparam("cs", expanding=True)
    if class_id:
        answers = conn.execute(text(
            "SELECT q.content, sa.is_correct FROM student_answers sa "
            "JOIN questions q ON q.question_id = sa.question_id "
            "WHERE q.content IN :cs AND sa.student_id IN "
            "(SELECT student_id FROM students WHERE class_id = :cid)"
        ).bindparams().bindparams(_CS),
            {"cs": [c for c, _ in rows], "cid": class_id}).fetchall()
    else:
        answers = conn.execute(text(
            "SELECT q.content, sa.is_correct FROM student_answers sa "
            "JOIN questions q ON q.question_id = sa.question_id "
            "WHERE q.content IN :cs"
        ).bindparams().bindparams(_CS),
            {"cs": [c for c, _ in rows]}).fetchall()

    kp_by_content = {content: kp for content, _, kp in canonical}

    agg = defaultdict(lambda: [0, 0])  # kp → [wrong, answers]
    for content, ok in answers:
        kp = kp_by_content.get(content)
        if kp is None:
            continue
        agg[kp][0] += 0 if ok else 1
        agg[kp][1] += 1

    return {kp: {"error_rate": w / n, "answers": n}
            for kp, (w, n) in agg.items() if n >= MIN_ANSWERS}


def kp_contents(conn, kp_id: str) -> list[str]:
    """映射到指定词典码的独立题内容集合（与 error_rates_for_kps 同一套
    content 去重 + 首次映射语义），供星图下钻按学生聚合错误作答用。"""
    rows = conn.execute(text(
        "SELECT q.content, MIN(q.question_id) AS qid "
        "FROM questions q WHERE q.knowledge_points != '' GROUP BY q.content"
    )).fetchall()
    contents = []
    for content, qid in rows:
        qrow = conn.execute(text(
            "SELECT knowledge_points FROM questions WHERE question_id = :q"),
            {"q": qid}).fetchone()
        kps_raw = qrow[0] if qrow else "[]"
        try:
            tags = json.loads(kps_raw) if isinstance(kps_raw, str) else []
        except Exception:
            tags = []
        for t in tags:
            mapped = conn.execute(text(
                "SELECT kp_id FROM kp_tag_map WHERE tag_text = :t"),
                {"t": t}).fetchone()
            if mapped and mapped[0] == kp_id:
                contents.append(content)
                break
    return contents


def tag2kp(conn, tag: str) -> "str | None":
    """单标签 → 词典码（kp_tag_map 查询）。"""
    row = conn.execute(text("SELECT kp_id FROM kp_tag_map WHERE tag_text = :t"),
                       {"t": tag}).fetchone()
    return row[0] if row else None
