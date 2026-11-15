"""智能练习（Smart Practice，批 2 S7 / CONTEXT.md「Smart Practice」）。

纯函数内核（单测钉住）：逐题 EAP θ 更新、SE 停止规则（SE≤0.3 或 8 题）；
选题用 catsim 最大信息量（BSD-3），未标定题回退难度先验（design D2 回退硬要求）。

会话实现为一条 ExamRecord（type=PRACTICE，question_stats.smart 记会话态）：
- start：薄弱知识点取候选池 → 复制题进会话记录（题目归属模型：Question.record_id）→
  发第一题；
- answer：grading_center 分层判定落 StudentAnswer（练习生效语义，mastery 读答案
  行自动更新——REQ-SMP-004 不产生第二条掌握度路径）→ EAP 更新 θ → 选题发下一题；
- finish：θ 快照 + 薄弱点 + 未标定占比（build_summary）。掌握度只经作答链路
  自动更新，本服务不直写 mastery/计划（design D2）。
"""

from __future__ import annotations

import math

MAX_ITEMS = 8
SE_STOP = 0.3
_GRID_STEP = 0.1
_GRID = [round(-3.0 + i * _GRID_STEP, 2) for i in range(61)]  # θ ∈ [-3, 3]


def _difficulty_value(raw) -> int:
    """难度归一成 int。库里可能残留枚举名/旧标签（'COMPETITION'），裸 int() 会 500
    （database.py:116-119 记录的 E2E E7 同类问题）；None 按中等（3）处理。"""
    from app.models.database import Difficulty
    return int(Difficulty.coerce(raw))


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + math.exp(-x))


def _fisher_info(a: float, b: float, theta: float) -> float:
    """2PL Fisher 信息——唯一实现在 assessment_service.fisher_info，此处转发。"""
    from app.services.assessment_service import fisher_info
    return fisher_info(a, b, theta)


def estimate_theta(item_params: list[tuple[float, float]], responses: list[bool],
                   prior: float = 0.0) -> tuple[float, float]:
    """网格 EAP：返回 (theta, se)。无作答返回 (prior, 1.0)（先验标准差）。

    item_params 为已 administered 的 (a, b) 序列，与 responses 逐位对齐。
    """
    if not item_params:
        return prior, 1.0
    log_prior = [-(t - prior) ** 2 / 2.0 for t in _GRID]
    log_like = [0.0] * len(_GRID)
    for (a, b), correct in zip(item_params, responses):
        for i, t in enumerate(_GRID):
            p = _sigmoid(a * (t - b))
            log_like[i] += math.log(max(p, 1e-9)) if correct else math.log(max(1 - p, 1e-9))
    post = [math.exp(lp + ll) for lp, ll in zip(log_prior, log_like)]
    norm = sum(post)
    if norm <= 0:
        return prior, 1.0
    mean = sum(t * p for t, p in zip(_GRID, post)) / norm
    var = sum((t - mean) ** 2 * p for t, p in zip(_GRID, post)) / norm
    return round(mean, 3), round(math.sqrt(max(var, 0.0)), 3)


def se_of(item_params: list[tuple[float, float]], theta: float) -> float:
    """信息和的倒数平方根；无信息返回 1.0。"""
    total = sum(_fisher_info(a, b, theta) for a, b in item_params)
    return round(1.0 / math.sqrt(total), 3) if total > 0 else 1.0


def should_stop(answered: int, se: float, max_items: int = MAX_ITEMS,
                se_threshold: float = SE_STOP) -> bool:
    """停止规则（REQ-SMP-003）：SE≤阈值 或 达到题数上限，先到为准。"""
    if answered <= 0:
        return False
    return se <= se_threshold or answered >= max_items


def pick_next_item(candidates: list[dict], administered_ids: set[str],
                   item_params_by_id: dict[str, tuple[float, float]],
                   theta: float, difficulty_by_id: dict[str, int] | None = None) -> dict | None:
    """选下一题：catsim 最大信息量（已标定）优先，未标定回退 |难度-θ定向| 最小。

    catsim 不可用/无标定题时整体降级为先验排序（REQ-SMP-002 回退硬要求）。
    candidates: [{question_id, difficulty}]；返回 candidate 或 None（池空）。
    """
    pool = [c for c in candidates if c["question_id"] not in administered_ids]
    if not pool:
        return None

    calib = [(c, item_params_by_id[c["question_id"]]) for c in pool
             if c["question_id"] in item_params_by_id]
    if calib and theta is not None:
        try:
            import numpy as np
            from catsim.selection import max_information
            items = np.array([[a, b, 0.0, 1.0] for _c, (a, b) in calib])
            ranked = max_information(items=items, administered_items=np.array([]),
                                     theta=float(theta), rank=True)
            return calib[int(ranked[0])][0]
        except Exception:
            # catsim 路径失败 → 同式 Fisher 排序兜底（数学一致）
            calib.sort(key=lambda cc: -_fisher_info(cc[1][0], cc[1][1], theta))
            return calib[0][0]

    # 未标定回退：难度最接近 θ 定向难度（θ 不可估按中等 3）
    from app.services.assessment_service import theta_difficulty
    target = theta_difficulty(theta) if theta is not None else 3
    diffs = difficulty_by_id or {}
    pool.sort(key=lambda c: abs(int(diffs.get(c["question_id"], 3)) - target))
    return pool[0]


def build_summary(theta_start: float | None, theta_end: float,
                  item_params: list[tuple[float, float]],
                  uncalibrated_count: int,
                  weak_points: list[dict],
                  answered: int | None = None) -> dict:
    """finish 快照（REQ-SMP-003/004）：能力变化 + 薄弱点 + 未标定占比 + 建议。

    薄弱点 weak_points 取 mastery 口径（调用方传 mastery_for_student 的薄弱前几），
    本函数只做汇总展示，不产生新口径。answered 为总作答数（未标定题也在内）；
    缺省回退标定题数（兼容旧调用）。
    """
    if answered is None:
        answered = len(item_params)
    se = se_of(item_params, theta_end)
    delta = round(theta_end - (theta_start if theta_start is not None else 0.0), 3)
    ratio = round(uncalibrated_count / answered, 2) if answered else 1.0
    suggestion_kp = weak_points[0]["knowledge_point"] if weak_points else None
    return {
        "theta_start": theta_start,
        "theta_end": theta_end,
        "theta_delta": delta,
        "standard_error": se,
        "answered": answered,
        "uncalibrated_ratio": ratio,
        "weak_points": weak_points,
        "suggestion": (
            f"薄弱点「{suggestion_kp}」建议去复习中心清卡" if suggestion_kp
            else "本轮能力估计已收敛，可再来一轮或去复习中心"),
    }


# ═══ 会话编排（ADR-0029 批② 自 smart_practice.py 端点体迁入，ADR-0012）═══
# 端点变薄壳；行为等价由既有 tests/test_smart_practice.py 护航（断言零修改）。
# 错误用 SmartSessionError(msg, status)——service 不认识 HTTPException，
# 端点捕获后按 status 转 HTTPException，状态码与文案不变。

from datetime import datetime  # noqa: E402

from app.models.database import (  # noqa: E402
    AuditStatus, ExamRecord, Question, QuestionCalibration, QuestionSource,
    RecordType, Student, split_answer_blanks,
)


class SmartSessionError(Exception):
    """会话编排错误；status 对应原端点 HTTP 状态码（400/404）。"""

    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def session_question_payload(q):
    qtype = getattr(q, "question_type", None) or ("single_choice" if q.options else "fill_blank")
    # 历史复制行（每日练习组卷没带 question_type，落到列默认 single_choice）
    # 声称是选择题却没有选项 —— 按选项实况纠偏，否则前端既不渲染选项也不渲染作答器
    if "choice" in str(qtype) and not q.options:
        qtype = "fill_blank"
    return {
        "question_id": q.question_id,
        "content": q.content,
        "question_type": qtype,
        "options": q.options,
        "blank_count": blank_count_of(q),
        "difficulty": _difficulty_value(q.difficulty),
        "knowledge_points": q.knowledge_points or [],
    }


def blank_count_of(q):
    return len(split_answer_blanks(q.answer)) if getattr(q, "answer", None) else 1


def load_session_state(record):
    stats = record.question_stats if isinstance(record.question_stats, dict) else {}
    smart = stats.get("smart") or {}
    return {
        "student_id": smart.get("student_id"),
        "theta_start": smart.get("theta_start"),
        "theta": smart.get("theta"),
        "responses": smart.get("responses", []),      # [{question_id, is_correct}]
        "params": smart.get("params", []),            # [[a, b], ...] 与 responses 对齐
        "uncalibrated": smart.get("uncalibrated", 0),  # 未标定题数
        "kps": smart.get("kps", []),
        "pool": smart.get("pool", []),                # [{question_id, difficulty}] 候选池
    }


def save_session_state(db, record, state):
    stats = dict(record.question_stats) if isinstance(record.question_stats, dict) else {}
    stats["smart"] = state
    record.question_stats = stats
    db.commit()


def calibrated_params(db, ids):
    """{qid: (a, b)}（n≥门槛的标定题）+ 标定池总量（供未标定计数）。"""
    rows = (db.query(QuestionCalibration)
            .filter(QuestionCalibration.question_id.in_(ids)).all()) if ids else []
    params = {r.question_id: (float(r.a), float(r.b))
              for r in rows if r.n_answers >= 30}
    return params, len(rows)


def sid_of(state):
    return state.get("student_id") or ""


def theta_of(db, state):
    """会话 θ：有作答走 EAP，否则取既有 IRT 能力（REQ-SMP-002），再退 0。"""
    from app.services.assessment_service import ability
    if state["responses"]:
        pairs = [(tuple(p) if p else None, bool(r["is_correct"]))
                 for p, r in zip(state["params"], state["responses"])]
        # 必须拆元组再过滤：pairs 的元素是 (参数, 对错) 整对，
        # 判断「p 不是 None」恒真，未标定题的 None 参数会原样喂进 EAP
        # （2026-10 实测：θ 恒等于先验、自适应选题形同失效）
        return estimate_theta([prm for prm, _ok in pairs if prm is not None],
                              [ok for prm, ok in pairs if prm is not None],
                              prior=state["theta_start"] or 0.0)
    theta = ability(db, sid_of(state), min_calibrated=5) if sid_of(state) else None
    return (float(theta), 1.0) if theta is not None else (0.0, 1.0)


def next_after(db, record, student_id, first=False):
    """选题发下一题：源题复制进会话记录（题目归属模型），复制行带题型。"""
    state = load_session_state(record)
    theta, _se = theta_of(db, state)
    answered_ids = {r["question_id"] for r in state["responses"]}

    # 源题池 → 剔除已答、已复制
    created_ids = {row[0] for row in db.query(Question.question_id).filter(
        Question.record_id == record.record_id).all()}
    # sq_{源题id}_{record_id}_{n}：按 record_id 分隔剥尾（源 id/学生 id 可含下划线）
    copied_sources = set()
    for qid in created_ids:
        body = qid[len("sq_"):]
        suffix = "_" + record.record_id + "_"
        if body.rfind(suffix) > 0:
            copied_sources.add(body[:body.rfind(suffix)])
    pool = [c for c in state["pool"] if c["question_id"] not in answered_ids
            and c["question_id"] not in copied_sources]

    calib, _ = calibrated_params(db, [c["question_id"] for c in pool])
    item_params = {qid: calib[qid] for qid in calib}
    picked = pick_next_item(pool, answered_ids | copied_sources,
                            item_params, theta)
    if picked is None:
        if first:
            raise SmartSessionError("候选池不足：没有可出的新题")
        return {"done": True, "theta": theta}

    src = db.query(Question).filter(
        Question.question_id == picked["question_id"]).first()
    if not src:
        # 源题被删（池快照过期）→ 剔除后重挑
        state["pool"] = [c for c in pool if c["question_id"] != picked["question_id"]]
        save_session_state(db, record, state)
        return next_after(db, record, student_id, first=first)

    n = len(created_ids)
    copy_q = Question(
        question_id="sq_" + src.question_id + "_" + record.record_id + "_" + str(n),
        record_id=record.record_id,
        content=src.content,
        question_type=getattr(src, "question_type", None),
        options=src.options,
        answer=src.answer,
        analysis=src.analysis,
        knowledge_points=src.knowledge_points,
        probe_dimension=src.probe_dimension,
        difficulty=src.difficulty,
        source=QuestionSource.AI_GENERATED if src.source != QuestionSource.MANUAL_SELECTED
        else QuestionSource.MANUAL_SELECTED,
        audit_status=AuditStatus.PASSED,
    )
    db.add(copy_q)
    db.commit()
    return {
        "practice_id": record.record_id,
        "question": session_question_payload(copy_q),
        "answered": len(state["responses"]),
        "theta": theta,
        "done": False,
    }


def start_session(db, student_id, kps=None):
    """开一轮智能练习：定候选池 → 建会话记录 → 发第一题（原 smart_start 端点体）。"""
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise SmartSessionError("学生 " + student_id + " 不存在", status=404)

    from app.services.mastery_service import mastery_for_student
    if not kps:
        weak = mastery_for_student(db, student_id)[:3]
        kps = [p["knowledge_point"] for p in weak]
    if not kps:
        raise SmartSessionError("还没有可练习的知识点：先做几道题，或指定 kps")

    # 候选池：薄弱（或指定）知识点下的题（全库跨记录），取前 40
    pool = []
    seen = set()
    # JSON 列在 SQLite 上存的是 ensure_ascii 转义文本，LIKE/contains 都匹配不上
    # 中文知识点（wrong/list 同坑）→ 取最近 500 题在 Python 侧解码过滤
    import json as _json
    rows = (db.query(Question)
            .filter(Question.answer.isnot(None))
            .order_by(Question.created_at.desc()).limit(500).all())
    kp_set = set(kps)
    for q in rows:
        if q.question_id in seen or not (q.answer or "").strip():
            continue
        kps_json = q.knowledge_points or []
        if isinstance(kps_json, str):
            try:
                kps_json = _json.loads(kps_json)
            except ValueError:
                kps_json = []
        if not (kp_set & set(kps_json or [])):
            continue
        seen.add(q.question_id)
        pool.append({"question_id": q.question_id,
                     "difficulty": _difficulty_value(q.difficulty)})
        if len(pool) >= 40:
            break
    if not pool:
        raise SmartSessionError("这些知识点下暂无带答案的题目")

    from app.services.assessment_service import ability
    theta_start = ability(db, student_id, min_calibrated=5)
    record_id = "smart_" + student_id + "_" + str(int(datetime.utcnow().timestamp()))
    record = ExamRecord(
        record_id=record_id,
        class_id=student.class_id,
        name="智能练习-" + (student.name or ""),
        type=RecordType.PRACTICE,
        total_students=1,
        present_students=1,
        exam_date=datetime.utcnow(),
        # 顶层 student_id 是逐生归属键（_belongs_to 口径）——缺失会把
        # 「智能练习-XX」推给全班（捉虫 P2-4）
        question_stats={"student_id": student_id,
            "smart": {
                "student_id": student_id,
                "theta_start": theta_start,
                "theta": None,
                "responses": [], "params": [], "uncalibrated": 0,
                "kps": kps, "pool": pool,
            }},
    )
    db.add(record)
    db.commit()

    return next_after(db, record, student_id, first=True)
