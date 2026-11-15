"""智能练习 API（批 2 S7 / smart-practice REQ-SMP-001..004）。

三端点闭环：start → answer（逐题）→ finish。全部 allow_self + ScopeGuard；
作答经 grading_center 落 StudentAnswer（练习生效语义），掌握度只由既有
作答→诊断链路自动更新，本 router 不直写 mastery/学习计划。
"""

from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker
from app.middleware.scope import ScopeGuard
from app.models.database import (
    ExamRecord, Question, RecordType, Student, get_db,
)
from app.services import grading_center
from app.services import smart_practice_service as sps
from app.services.smart_practice_service import (
    SmartSessionError, calibrated_params as _calibrated_params,
    load_session_state as _load_session_state,
    next_after as _next_after,
    save_session_state as _save_session_state,
    session_question_payload as _session_question_payload,  # noqa: F401  测试从此模块再导入
    sid_of as _sid_of,  # noqa: F401  同上
    start_session, theta_of as _theta_of,
)
from app.services.mastery_service import mastery_for_student

router = APIRouter()


class SmartStartRequest(BaseModel):
    student_id: str
    kps: Optional[List[str]] = None  # 知识点范围；缺省取薄弱 Top3
    # 停止规则固定 SE≤0.3 或 8 题（REQ-SMP-003 定案），无 count 配置面


class SmartAnswerRequest(BaseModel):
    student_id: str
    practice_id: str
    question_id: str
    answer: str
    attachments: Optional[List[dict]] = None  # 装置图 [{question_id, data_url}]（X2 对称）


class SmartFinishRequest(BaseModel):
    student_id: str
    practice_id: str


def _require_student(db: Session, student_id: str) -> Student:
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail=f"学生 {student_id} 不存在")
    return student


@router.post("/smart/start")
async def smart_start(request: SmartStartRequest, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("practice", "create", allow_self=True)),
    _scope: None = Depends(ScopeGuard("student_id", kind="student")),
):
    """开一轮智能练习：定候选池 → 建会话记录 → 发第一题。"""
    # 编排在 service（ADR-0029 批② 迁入，供 invoke_learning_skill 复用）；
    # SmartSessionError 的 status/code 与原端点体逐字一致（等价性由既有测试护航）
    try:
        return start_session(db, request.student_id, request.kps)
    except SmartSessionError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


@router.post("/smart/answer")
async def smart_answer(request: SmartAnswerRequest, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("practice", "update", allow_self=True)),
    _scope: None = Depends(ScopeGuard("student_id", kind="student")),
):
    """作答一题：分层判定落库 → EAP 更新 θ → 停止规则 → 发下一题/结束。"""
    _require_student(db, request.student_id)
    record = db.query(ExamRecord).filter(
        ExamRecord.record_id == request.practice_id,
        ExamRecord.type == RecordType.PRACTICE).first()
    if not record:
        raise HTTPException(status_code=404, detail="练习记录不存在")
    state = _load_session_state(record)
    if state.get("student_id") != request.student_id:
        raise HTTPException(status_code=403, detail="无权访问该会话")
    answered_ids = {r["question_id"] for r in state["responses"]}
    if request.question_id in answered_ids:
        raise HTTPException(status_code=409, detail="该题已作答")

    q = db.query(Question).filter(
        Question.question_id == request.question_id,
        Question.record_id == request.practice_id).first()
    if not q:
        raise HTTPException(status_code=404, detail="题目不存在")

    # 装置图附件（X2 对称 submit）：落盘后把路径挂进判定条目
    from app.api.practice import save_answer_attachments
    att_paths = save_answer_attachments(
        request.attachments, request.student_id, request.practice_id)

    # 分层判定（练习生效语义：即时生效进诊断面）；语义判定在途照旧落 PENDING 行
    graded = grading_center.grade(
        [{"question_id": request.question_id, "answer": request.answer}],
        [{"question_id": request.question_id, "answer": q.answer or "",
          "choice": grading_center.answer_key_choice(q.answer, q.options)}],
        semantic=False)
    g = graded["questions"][0]
    if request.question_id in att_paths:
        g["attachments"] = att_paths[request.question_id]
    pending_semantic = bool(g.get("needs_semantic"))
    resolved = [] if pending_semantic else [g]
    grading_center.save(db, resolved, request.student_id, request.practice_id,
                        "online", trigger_diagnosis=False, upsert=True)
    if pending_semantic:
        grading_center.save(db, [{
            "question_id": g["question_id"], "student_answer": g["student_answer"],
            "is_correct": False, "reason": grading_center.PENDING_REASON,
            # 装置图（X2）附件必须随 PENDING 行落库 —— 丢了教师在判卷中心
            # 「装置图」tab 就看不到图（E2E E10 实测：队列全显示「附件 0 张」）
            "attachments": g.get("attachments"),
        }], request.student_id, request.practice_id, "online",
            trigger_diagnosis=False, upsert=True)

    # 会话态推进：θ 逐题 EAP（REQ-SMP-003）
    state["responses"].append({"question_id": request.question_id,
                               "is_correct": bool(g["is_correct"])})
    params, _total = _calibrated_params(db, [request.question_id])
    if request.question_id in params:
        state["params"].append(list(params[request.question_id]))
    else:
        # 未标定题占位 None：不进 EAP/SE——塞伪参数 (a=1,b=0) 会虚增信息量、
        # 压低 SE 造成提前误停（spec 审查 c-1）；estimate_theta 按位跳过 None
        state["uncalibrated"] += 1
        state["params"].append(None)
    pairs = [(tuple(p) if p else None, bool(r["is_correct"]))
             for p, r in zip(state["params"], state["responses"])]
    # 同上：先拆元组再按参数是否标定过滤，否则 None 参数会进 EAP
    theta, se = sps.estimate_theta(
        [prm for prm, _ok in pairs if prm is not None],
        [ok for prm, ok in pairs if prm is not None],
        prior=state["theta_start"] or 0.0)
    state["theta"] = theta
    _save_session_state(db, record, state)

    done = sps.should_stop(len(state["responses"]), se)
    result = {
        "is_correct": bool(g["is_correct"]),
        "reason": str(g.get("reason") or ""),
        "pending_semantic": pending_semantic,
        "answered": len(state["responses"]),
        "theta": theta,
        "standard_error": se,
        "done": done,
    }
    if not done:
        result["next"] = _next_after(db, record, request.student_id)
    return result


@router.post("/smart/finish")
async def smart_finish(request: SmartFinishRequest, db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("practice", "update", allow_self=True)),
    _scope: None = Depends(ScopeGuard("student_id", kind="student")),
):
    """结束快照：θ 变化 + 薄弱点 + 未标定占比 + 建议（REQ-SMP-003/004）。

    掌握度已由作答链路自动更新（mastery 读 StudentAnswer 行），此处只汇总。
    """
    record = db.query(ExamRecord).filter(
        ExamRecord.record_id == request.practice_id,
        ExamRecord.type == RecordType.PRACTICE).first()
    if not record:
        raise HTTPException(status_code=404, detail="练习记录不存在")
    state = _load_session_state(record)
    if state.get("student_id") != request.student_id:
        raise HTTPException(status_code=403, detail="无权访问该会话")

    # REQ-SMP-001：生效作答已即时进诊断数据面（mastery 直读答案行），
    # 聚合诊断在 finish 收尾跑一次
    theta, _se = _theta_of(db, state)
    weak = mastery_for_student(db, request.student_id)[:3]
    summary = sps.build_summary(
        state["theta_start"], theta,
        [tuple(p) for p in state["params"] if p],
        state["uncalibrated"], weak,
        answered=len(state["responses"]))

    # 收尾：一次障碍诊断（练习生效语义的一部分，幂等）
    from app.jobs.handlers import diagnose_exam
    try:
        diagnose_exam(db, request.practice_id)
    except Exception:
        pass
    return summary
