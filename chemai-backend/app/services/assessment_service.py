"""评估服务（能力升级批次 C）——IRT 标定 + CAT 自适应选题。

三件事（spec 批次 C / CONTEXT.md 口径）：

1. **标定** `calibrate`：从作答数据算每道题的经验难度 b（正确率 logit，按样本量
   向 0 收缩）与作答数 n。区分度 a 固定 1.0（1PL——单题正确率推不出区分度，
   2PL 需要联合拟合，本批不做）。
2. **能力估计** `ability`：学生在**已标定题目**上的作答 → θ（网格 EAP，先验
   N(0,1)）。作答不足 5 条标定题 → None（不猜）。
3. **选题** `select_items`：CAT 内核——已标定（n≥MIN_CALIBRATION_ANSWERS，即
   每题 ≥30 条作答门槛）的候选项按最大 Fisher 信息选题；未达标的候选项退回
   难度 1-5 先验排序。降级规则写死在本服务内部，不出现"标定了一半"的中间态。

与 catsim 的关系：catsim 可装但其 API 面向完整仿真流程，本场景只需要
「EAP 估计 + 最大信息选题」两个纯函数——numpy 自实现（各 ~15 行）更可控、
确定性可测，少一个依赖（spec 偏离已记账）。
"""
import math

from sqlalchemy.orm import Session

from app.models.database import QuestionCalibration

# 数据门槛：每题作答数达到该值才采信标定参数（CONTEXT.md 批次 C 口径）
MIN_CALIBRATION_ANSWERS = 30

# θ 估计的网格（-4..4 步 0.25）与先验
_GRID = [i * 0.25 for i in range(-16, 17)]


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _logit(p: float) -> float:
    p = min(max(p, 0.05), 0.95)  # 钳住极端正确率（对/全错的题 b 无穷大没意义）
    return math.log(p / (1.0 - p))


def calibrate(db: Session) -> int:
    """全量重算题目标定（幂等 upsert），返回标定的题数。冷启动跑一次即可；
    之后由判卷钩子周期性/按需重算（批外事项，本批只提供入口）。"""
    from sqlalchemy import text
    from datetime import datetime

    rows = db.execute(text(
        "SELECT sa.question_id, COUNT(DISTINCT sa.student_id) AS n, "
        "AVG(CASE WHEN sa.is_correct THEN 1.0 ELSE 0.0 END) AS acc "
        "FROM student_answers sa WHERE sa.is_correct IS NOT NULL "
        "AND sa.confirmed = 1 "
        "AND (sa.grade_reason IS NULL OR sa.grade_reason NOT IN "
        "     ('PENDING_SEMANTIC', '缺少标准答案（未判）')) "
        "GROUP BY sa.question_id"
    )).fetchall()

    count = 0
    for qid, n, acc in rows:
        if not qid or n is None or acc is None:
            continue
        # 收缩：n 小的题向 b=0（中等难度先验）收缩，避免小样本极端 b。
        # 符号：IRT 惯例 p=σ(θ-b)，正确率越高 b 越低（题越容易）。
        b = -_logit(float(acc)) * n / (n + 10.0)
        row = db.get(QuestionCalibration, qid)
        if row is None:
            row = QuestionCalibration(question_id=qid)
            db.add(row)
        row.a = 1.0
        row.b = round(b, 4)
        row.n_answers = int(n)
        row.updated_at = datetime.utcnow()
        count += 1
    db.commit()
    return count


def ability(db: Session, student_id: str, min_calibrated: int = 5) -> float | None:
    """学生能力 θ（网格 EAP，先验 N(0,1)）。标定题作答不足 → None。"""
    from sqlalchemy import text
    # 每题取最后一次作答（重做不重复计入），门槛按**不同题数**（P1-6）
    rows = db.execute(text(
        "SELECT qc.b, qc.a, sa.is_correct FROM ("
        "  SELECT question_id, is_correct, ROW_NUMBER() OVER "
        "    (PARTITION BY question_id ORDER BY answered_at DESC) AS rn "
        "  FROM student_answers WHERE student_id = :sid AND is_correct IS NOT NULL "
        "  AND confirmed = 1 "
        "  AND (grade_reason IS NULL OR grade_reason NOT IN "
        "       ('PENDING_SEMANTIC', '缺少标准答案（未判）'))"
        ") sa JOIN question_calibration qc ON qc.question_id = sa.question_id "
        "WHERE sa.rn = 1"
    ), {"sid": student_id}).fetchall()
    if len(rows) < min_calibrated:
        return None

    log_prior = [-0.5 * (t * t) for t in _GRID]  # N(0,1) 对数先验
    log_like = [0.0] * len(_GRID)
    for b, a, correct in rows:
        for i, t in enumerate(_GRID):
            p = _sigmoid(a * (t - float(b)))
            log_like[i] += math.log(max(p, 1e-9)) if correct else math.log(max(1 - p, 1e-9))
    posterior = [math.exp(lp + ll) for lp, ll in zip(log_prior, log_like)]
    norm = sum(posterior)
    if norm <= 0:
        return None
    theta = sum(t * p for t, p in zip(_GRID, posterior)) / norm
    return round(theta, 3)


def fisher_info(a: float, b: float, theta: float) -> float:
    """2PL Fisher 信息：I(θ) = a²·p(θ)·(1-p(θ))，p = σ(a(θ-b))。"""
    p = _sigmoid(a * (theta - b))
    return a * a * p * (1.0 - p)


def _theta_to_difficulty(theta: float) -> int:
    """θ → 难度 1-5：θ 每 ±1 约“一个非常量级”。"""
    if theta < -2:
        return 1
    if theta < -0.75:
        return 2
    if theta <= 0.75:
        return 3
    if theta <= 2:
        return 4
    return 5


theta_difficulty = _theta_to_difficulty  # 公共别名（smart_practice_service 用，不引私有成员）


def theta_to_zpd(theta: float) -> str:
    """θ → zpd 词汇表（easy/medium/hard，与 _calculate_zpd_difficulty 同形）。
    公开函数：assign 服务的定向接线用，不跨模块摸私有成员。"""
    return {1: "easy", 2: "easy", 3: "medium", 4: "hard", 5: "hard"}[
        _theta_to_difficulty(theta)]


def select_items(db: Session, student_id: str, candidate_ids: list[str],
                 count: int) -> dict:
    """CAT 选题内核：候选题目按「标定者优先于 Fisher 信息、未标定退回难度先验」
    排序取前 count。

    返回 {"items": [{question_id, source, info_or_difficulty}], "theta": θ|None,
    "degraded": bool}——degraded=True 表示本轮没有任何可用标定（全部走先验）。
    """
    if count <= 0:
        return {"items": [], "theta": None, "degraded": True}
    theta = ability(db, student_id)
    if candidate_ids:
        rows = (db.query(QuestionCalibration)
                .filter(QuestionCalibration.question_id.in_(candidate_ids)).all())
        calibrated = {r.question_id: r for r in rows
                      if r.n_answers >= MIN_CALIBRATION_ANSWERS}
    else:
        calibrated = {}

    # 未标定候选的先验排序：|难度 1-5 - θ定向难度| 升序（θ 不可估时按中等难度）
    target_diff = _theta_to_difficulty(theta) if theta is not None else 3
    uncal_ids = [q for q in (candidate_ids or []) if q not in calibrated]
    diff_by_id = {}
    if uncal_ids:
        from app.models.database import Question
        for qid, diff in db.query(Question.question_id, Question.difficulty).filter(
                Question.question_id.in_(uncal_ids)).all():
            diff_by_id[qid] = int(diff) if diff is not None else 3

    calibrated_items, prior_items = [], []
    for qid in candidate_ids or []:
        cal = calibrated.get(qid)
        if theta is not None and cal is not None:
            calibrated_items.append({"question_id": qid, "source": "calibrated",
                                     "key": fisher_info(cal.a, cal.b, theta)})
        else:
            prior_items.append({"question_id": qid, "source": "prior",
                                "key": -abs(diff_by_id.get(qid, 3) - target_diff)})

    calibrated_items.sort(key=lambda x: -x["key"])
    prior_items.sort(key=lambda x: -x["key"])
    ordered = calibrated_items + prior_items
    selected = ordered[:count]

    return {"items": selected, "theta": theta,
            "degraded": all(i["source"] == "prior" for i in selected)}
