"""掌握概率服务（能力升级批次 C）——知识点掌握概率（CONTEXT.md 口径：给老师
概率条 + 薄弱 Top N；家长口径维持文字化，概率不进家长报告 prompt）。

模型：**顺序贝叶斯知识追踪（BKT）**，本仓库自有实现（约 40 行，零新依赖）。
评估期曾选 pyBKT，实装发现其 fit/predict 与本环境 sklearn 版本不兼容
（log_loss 内部 numpy API 断裂），且本场景只需要「按学生序列推掌握概率」，
不需要跨学生 EM 拟合——固定先验的标准 BKT 足够，且确定性可测（spec 偏离已记账）。

参数（经典 BKT 文献常用值，写死不配置）：
- P_KNOWN_INIT=0.25 初始掌握概率
- P_TRANSIT=0.15 每次练习后学会的概率
- P_SLIP=0.15 已会做错（失误）
- P_GUESS=0.20 不会猜对

样本门槛：某知识点作答数 < MIN_SAMPLES 时 mastery=None（样本不足不下结论，
调用方回退正确率口径）。
"""
import json

from sqlalchemy.orm import Session

# BKT 固定参数
P_KNOWN_INIT = 0.25
P_TRANSIT = 0.15
P_SLIP = 0.15
P_GUESS = 0.20

MIN_SAMPLES = 3


def bkt_sequence(correct_flags: list[bool]) -> float:
    """按时间序喂作答序列，返回最终掌握概率（0-1）。"""
    p_known = P_KNOWN_INIT
    for correct in correct_flags:
        # 学习迁移：练习让未掌握者有概率学会
        p_known = p_known + (1.0 - p_known) * P_TRANSIT
        # 观测更新（贝叶斯）
        if correct:
            p_obs = p_known * (1.0 - P_SLIP) + (1.0 - p_known) * P_GUESS
            p_known_given_obs = p_known * (1.0 - P_SLIP) / p_obs if p_obs > 0 else p_known
        else:
            p_obs = p_known * P_SLIP + (1.0 - p_known) * (1.0 - P_GUESS)
            p_known_given_obs = p_known * P_SLIP / p_obs if p_obs > 0 else p_known
        p_known = min(max(p_known_given_obs, 0.0), 1.0)
    return round(p_known, 4)


def mastery_for_student(db: Session, student_id: str,
                        min_samples: int = MIN_SAMPLES) -> list[dict]:
    """学生的知识点掌握概率列表，薄弱在前。

    数据面：生效作答（is_correct 非空）× 题目知识点，按时间序展开成每知识点
    的 0/1 序列。样本 < min_samples 的知识点不输出（不下结论）。
    """
    from sqlalchemy import text
    rows = db.execute(
        text(
            "SELECT q.knowledge_points, sa.is_correct FROM student_answers sa "
            "JOIN questions q ON sa.question_id = q.question_id "
            "WHERE sa.student_id = :sid AND sa.is_correct IS NOT NULL "
            "AND sa.confirmed = 1 "
            "AND (sa.grade_reason IS NULL OR sa.grade_reason NOT IN "
            "     ('PENDING_SEMANTIC', '缺少标准答案（未判）')) "
            "ORDER BY sa.answered_at ASC"
        ),
        {"sid": student_id},
    ).fetchall()

    sequences: dict[str, list[bool]] = {}
    for kps_json, is_correct in rows:
        if not kps_json:
            continue
        kps = json.loads(kps_json) if isinstance(kps_json, str) else kps_json
        for kp in kps or []:
            sequences.setdefault(kp, []).append(bool(is_correct))

    result = []
    for kp, seq in sequences.items():
        if len(seq) < min_samples:
            continue
        result.append({
            "knowledge_point": kp,
            "mastery": bkt_sequence(seq),
            "samples": len(seq),
        })
    result.sort(key=lambda x: x["mastery"])  # 薄弱在前
    return result


def weak_top(db: Session, student_id: str, n: int = 3) -> list[dict]:
    """薄弱知识点 Top N（掌握概率最低的 n 个，样本门槛同上）。"""
    return mastery_for_student(db, student_id)[:max(1, n)]


# ═══ 四级状态映射（S2 / mastery-visualization REQ-MSV-001）═══
# 阈值与 knowledge-map 就绪过滤同源（design D1）：<0.4 摸索 / 0.4–0.7 熟悉 /
# 0.7–0.9 熟练 / ≥0.9 精通（Khan 语义：只有 Proficient+ 计入课程掌握）。
MASTERY_PROFICIENT_AT = 0.7
MASTERY_MASTERED_AT = 0.9


def mastery_level(probability: float) -> str:
    """BKT 掌握概率 → 四级状态（canonical，纯函数）。"""
    p = float(probability)
    if p >= MASTERY_MASTERED_AT:
        return "mastered"
    if p >= MASTERY_PROFICIENT_AT:
        return "proficient"
    if p >= 0.4:
        return "familiar"
    return "attempted"


def course_mastery_percent(levels: list) -> int:
    """课程掌握 %：精通+熟练知识点占比（整数百分比）。空列表返回 0。"""
    if not levels:
        return 0
    counted = sum(1 for lv in levels if lv in ("proficient", "mastered"))
    return round(counted / len(levels) * 100)
