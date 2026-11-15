"""知识地图 API（S6 / knowledge-map-views REQ-KMV-001..005）。

学生视图（allow_self + ScopeGuard）：三态节点 + 拓扑分层 + 工件绑定
（只呈现已分享给该学生的版本——工件分发契约读侧过滤）；教师管理：边启停、
绑定（只允许本人名下工件）。
"""

import functools
import json
import logging

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker
from app.middleware.scope import ScopeGuard
from app.models.database import (
    KPArtifactBinding, KnowledgeEdge, Student, get_db,
)
from app.services import knowledge_map_service as kms
from app.services.mastery_service import mastery_for_student

logger = logging.getLogger(__name__)

router = APIRouter()


def _active_edges(db) -> list[tuple[str, str]]:
    rows = db.query(KnowledgeEdge.from_kp, KnowledgeEdge.to_kp).filter(
        KnowledgeEdge.status == "active").all()
    return [(a, b) for a, b in rows]


def _prereqs_of(edges: list[tuple[str, str]]) -> dict[str, list[str]]:
    prereqs: dict[str, list[str]] = {}
    for a, b in edges:
        prereqs.setdefault(b, []).append(a)
    return prereqs


def _mastery_by_kp(db, student_id: str) -> dict[str, float]:
    return {p["knowledge_point"]: float(p["mastery"])
            for p in mastery_for_student(db, student_id)}


def _bindings_visible(db, artifact_ids: set[str], student_id: str) -> set[str]:
    """学生可见绑定 = 该工件确有分享给此学生的记录（复用 artifacts 的
    收件通知口径，fail-closed：无分享记录的绑定对学生不可见）。"""
    if not artifact_ids:
        return set()
    from app.api.artifacts import _shared_versions
    return {aid for aid in artifact_ids if _shared_versions(db, aid, student_id)}


@router.get("/student/{student_id}")
async def student_map(
    student_id: str,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("analysis", "read", allow_self=True)),
    _scope: None = Depends(ScopeGuard("student_id", kind="student")),
):
    """学生知识地图：三态节点（拓扑分层）+ 工件绑定 + 褪色（FSRS 到期 KP）。"""
    student = db.query(Student).filter(Student.student_id == student_id).first()
    if not student:
        raise HTTPException(status_code=404, detail=f"学生 {student_id} 不存在")

    mastery = _mastery_by_kp(db, student_id)
    edges = _active_edges(db)
    # 节点集 = 掌握度知识点 ∪ 图边端点 ∪ 学生作答过/班级任务题目的知识点
    from app.models.database import ExamRecord, Question as _Question, RecordType as _RT
    from app.models.database import StudentAnswer as _SA
    kps_rows = (db.query(_Question.knowledge_points)
                .join(_SA, _SA.question_id == _Question.question_id)
                .filter(_SA.student_id == student_id).all())
    if student.class_id:
        kps_rows += (db.query(_Question.knowledge_points)
                     .join(ExamRecord, ExamRecord.record_id == _Question.record_id)
                     .filter(ExamRecord.class_id == student.class_id,
                             ExamRecord.type == _RT.PRACTICE).all())
    answered_kps: set[str] = set()
    for (kps_json,) in kps_rows:
        if not kps_json:
            continue
        arr = json.loads(kps_json) if isinstance(kps_json, str) else kps_json
        answered_kps.update(arr or [])
    nodes = set(mastery) | answered_kps
    states = kms.resolve_states(mastery, edges)
    for kp in nodes:
        states.setdefault(kp, kms.kp_state(kp, mastery,
                                           _prereqs_of(edges)))
    layers = kms.topo_layers(edges, set(states))

    # 工件绑定：只呈现已分享给该学生的
    bindings = db.query(KPArtifactBinding).all()
    visible_binds = []
    for b in bindings:
        if _bindings_visible(db, {b.artifact_id}, student_id):
            visible_binds.append({"kp": b.kp, "artifact_id": b.artifact_id})

    # FSRS 褪色：到期卡所属知识点（与掌握环同一数据面）
    from app.models.database import Question as _Question
    from app.services.memory_card_service import due_cards
    faded = set()
    for card in due_cards(db, student_id, limit=100):
        q = db.query(_Question).filter(
            _Question.question_id == card.question_id).first()
        if q and q.knowledge_points:
            faded.update(q.knowledge_points)

    return {
        "success": True,
        "student_id": student_id,
        "states": states,
        "mastery": mastery,
        "layers": layers,
        "edges": [{"from": a, "to": b} for a, b in edges],
        "bindings": visible_binds,
        "faded": sorted(faded),
        "cold_start": len(edges) == 0,
    }


@router.get("/teacher/edges")
async def teacher_edges(
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("question", "read")),
):
    """教师边管理读侧：全部边（含 disabled）+ 全图状态分布（班级无关的图谱态）。"""
    rows = db.query(KnowledgeEdge).order_by(KnowledgeEdge.id.desc()).all()
    return {
        "success": True,
        "edges": [{"id": r.id, "from_kp": r.from_kp, "to_kp": r.to_kp,
                   "status": r.status, "reason": r.reason} for r in rows],
        "states_note": "节点三态按学生维度在学生视图计算；本端点为图谱管理面",
    }


_NOTES_PATH = Path(__file__).resolve().parents[2] / "content" / "kb" / \
    "kp_reference_notes.json"


@functools.lru_cache(maxsize=1)
def _read_reference_notes(mtime: float) -> dict:
    """按文件 mtime 失效的进程级缓存：细则缺失/损坏只影响弹窗一个区块，
    返回空表告警，不得拖垮图谱接口。"""
    try:
        data = json.loads(_NOTES_PATH.read_text(encoding="utf-8"))
        return data.get("notes") or {}
    except Exception as e:
        logger.warning("kp_reference_notes 加载失败（细则区块降级为空）: %s", e)
        return {}


def _load_reference_notes() -> dict:
    try:
        mtime = _NOTES_PATH.stat().st_mtime
    except OSError:
        mtime = 0
    return _read_reference_notes(mtime)


def _graph_base_nodes(db: Session) -> list[dict]:
    """全量节点骨架（词典+文档+方程式+必背考点细则），错误率由调用方按口径填。"""
    import json as _json

    from app.models.database import KBFrontmatter, KnowledgePoint

    kps = db.query(KnowledgePoint).order_by(
        KnowledgePoint.module, KnowledgePoint.order_no).all()
    fm = {f.kp_id: f for f in db.query(KBFrontmatter).all()}
    ref_notes = _load_reference_notes()

    nodes = []
    for k in kps:
        f = fm.get(k.kp_id)
        equations: list = []
        if f is not None and f.equations_json:
            try:
                equations = _json.loads(f.equations_json)
            except Exception:
                equations = []
        nodes.append({
            "kp_id": k.kp_id, "name": k.name, "module": k.module or "",
            "theme": k.theme or "", "order_no": k.order_no,
            "description": k.description or "",
            "error_rate": None, "answers": 0,
            "doc": (f.source_doc if f is not None else "") or "",
            "equations": equations,
            "reference_notes": ref_notes.get(k.kp_id, []),
        })
    return nodes


def _graph_edges(db: Session) -> list[dict]:
    return [{"id": r.id, "from_kp": r.from_kp, "to_kp": r.to_kp,
             "status": r.status, "edge_type": r.edge_type or "",
             "reason": r.reason} for r in db.query(KnowledgeEdge).all()]


@router.get("/teacher/graph")
async def teacher_graph(
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("question", "read")),
):
    """教师图谱可视化读侧：全量节点（词典+错误率+文档+方程式+必背考点细则）与全量边。

    160 节点量级一次全量返回——前端点击详情在本地完成，无需二次请求。
    错误率复用 KB2a 聚合（挂在注入会话的连接上，测试内存库无统计表时静默空）。
    """
    from app.services.kb_error_rate import error_rates_for_kps

    nodes = _graph_base_nodes(db)
    codes = [n["kp_id"] for n in nodes]
    try:
        rates = error_rates_for_kps(db.connection(), codes) if codes else {}
    except Exception:
        rates = {}
    for n in nodes:
        rate = rates.get(n["kp_id"], {})
        n["error_rate"] = rate.get("error_rate")
        n["answers"] = rate.get("answers", 0)
    return {"success": True, "nodes": nodes, "edges": _graph_edges(db)}


@router.get("/class/{class_id}/graph")
async def class_graph(
    class_id: str,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("question", "read")),
    _scope: None = Depends(ScopeGuard("class_id")),
):
    """班级星图读侧：节点带全班错误率/作答量（作答事件级口径，仅加班级过滤）。

    与 teacher_graph 同一节点骨架；class_error_rate 阈值口径与全局一致
    （作答 < MIN_ANSWERS 不出，样本太小是噪音）；聚合异常静默降级。
    """
    from app.services.kb_error_rate import error_rates_for_kps

    nodes = _graph_base_nodes(db)
    codes = [n["kp_id"] for n in nodes]
    try:
        rates = (error_rates_for_kps(db.connection(), codes, class_id=class_id)
                 if codes else {})
    except Exception:
        rates = {}
    for n in nodes:
        rate = rates.get(n["kp_id"], {})
        n["class_error_rate"] = rate.get("error_rate")
        n["class_answers"] = rate.get("answers", 0)
    from app.models.database import Class as ClassModel
    cls = db.query(ClassModel).filter(ClassModel.class_id == class_id).first()
    return {"success": True, "class_id": class_id,
            "class_name": (cls.name if cls else ""),
            "nodes": nodes, "edges": _graph_edges(db)}


_WEAK_STUDENT_LIMIT = 20


class KPLayoutPoint(BaseModel):
    kp_id: str
    x: float
    y: float
    z: float


@router.get("/layout")
async def get_layout(
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("question", "read")),
):
    """全校共享的星图坐标（REQ-SMI-006）：布局只取决于知识点与先修边。

    只插入缺失、从不覆盖——写入语义见 KPLayout 模型注释。"""
    from app.models.database import KPLayout
    rows = db.query(KPLayout).all()
    return {"success": True,
            "layout": {r.kp_id: [r.x, r.y, r.z] for r in rows}}


@router.post("/layout")
async def save_layout(
    points: list[KPLayoutPoint],
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("question", "update")),
):
    """补齐缺失坐标：只插入库中没有的 kp_id，从不覆盖已有值（并发首访幂等）。"""
    from app.models.database import KPLayout
    existing = {r.kp_id for r in db.query(KPLayout.kp_id).all()}
    added = 0
    for p in points:
        if p.kp_id in existing:
            continue
        db.add(KPLayout(kp_id=p.kp_id, x=p.x, y=p.y, z=p.z))
        existing.add(p.kp_id)
        added += 1
    if added:
        db.commit()
    return {"success": True, "added": added, "skipped": len(points) - added}


@router.get("/class/{class_id}/kp/{kp_id}/weak-students")
async def class_kp_weak_students(
    class_id: str,
    kp_id: str,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("question", "read")),
    _scope: None = Depends(ScopeGuard("class_id")),
):
    """星图下钻懒加载：该知识点错误最多的学生（每人附最近一条错误作答样本）。

    题目范围走 kp_contents（content 去重 + 首次映射语义，与错误率聚合同尺）；
    is_correct=0 才算错（PENDING_SEMANTIC 的 NULL 不进错误名单）。
    """
    from sqlalchemy import bindparam, text

    from app.services.kb_error_rate import kp_contents

    try:
        contents = kp_contents(db.connection(), kp_id)
    except Exception:
        contents = []
    if not contents:
        return {"success": True, "kp_id": kp_id, "students": []}

    sql = text(
        "SELECT s.student_id, s.name, COUNT(*) AS wrongs, "
        "MAX(sa.answered_at) AS last_wrong_at "
        "FROM student_answers sa "
        "JOIN questions q ON q.question_id = sa.question_id "
        "JOIN students s ON s.student_id = sa.student_id "
        "WHERE s.class_id = :cid AND q.content IN :cs AND sa.is_correct = 0 "
        "GROUP BY s.student_id, s.name "
        "ORDER BY wrongs DESC, last_wrong_at DESC LIMIT :lim"
    ).bindparams(bindparam("cs", expanding=True))
    rows = db.execute(sql, {"cid": class_id, "cs": contents,
                            "lim": _WEAK_STUDENT_LIMIT}).fetchall()
    if not rows:
        return {"success": True, "kp_id": kp_id, "students": []}

    sample_sql = text(
        "SELECT s.student_id, sa.student_answer, sa.answered_at "
        "FROM student_answers sa "
        "JOIN questions q ON q.question_id = sa.question_id "
        "JOIN students s ON s.student_id = sa.student_id "
        "WHERE s.class_id = :cid AND q.content IN :cs AND sa.is_correct = 0 "
        "AND s.student_id IN :sids ORDER BY sa.answered_at DESC"
    ).bindparams(bindparam("cs", expanding=True), bindparam("sids", expanding=True))
    samples: dict[str, dict] = {}
    for sid, ans, at in db.execute(
            sample_sql, {"cid": class_id, "cs": contents,
                         "sids": [r[0] for r in rows]}).fetchall():
        samples.setdefault(sid, {"answer": ans, "answered_at": str(at) if at else None})

    return {"success": True, "kp_id": kp_id, "students": [
        {"student_id": sid, "name": name, "wrong_count": wrongs,
         "last_wrong_at": str(last) if last else None,
         "last_wrong_answer": samples.get(sid, {}).get("answer")}
        for sid, name, wrongs, last in rows]}



class EdgeStatusRequest(BaseModel):
    status: str  # active / disabled


@router.put("/edges/{edge_id}/accept")
async def accept_proposed_edge(
    edge_id: int,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("question", "update")),
):
    """采纳一条 proposed 边（KB-T12 的 UI 通道，2026-10-27）。

    环校验与 accept_edges 同语义（含与已 active 边组合成环）；成环返回 409
    带环路径，边保持 proposed——老师要在页面上看到"这条被拒了"，不是静默消失。
    非 proposed 边（active/disabled）不接受——采纳只对提议有意义。
    """
    row = db.get(KnowledgeEdge, edge_id)
    if not row:
        raise HTTPException(status_code=404, detail="边不存在")
    if row.status != "proposed":
        raise HTTPException(status_code=400,
                            detail=f"该边状态为 {row.status}，只有提议（proposed）可采纳")
    from app.services.knowledge_map_service import find_cycle
    active = _active_edges(db)
    cycle = find_cycle(active, (row.from_kp, row.to_kp))
    if cycle:
        raise HTTPException(status_code=409,
                            detail=f"先修边成环：{' → '.join(cycle)}")
    row.status = "active"  # 存量词汇：生效态叫 active（学生就绪过滤认它）
    db.commit()
    return {"success": True, "edge_id": edge_id, "status": "active"}


@router.put("/edges/{edge_id}/status")
async def set_edge_status(
    edge_id: int,
    request: EdgeStatusRequest,
    db: Session = Depends(get_db),
    _perm: None = Depends(PermissionChecker("question", "update")),
):
    """边启停（REQ-KED-003）：disabled 即时退出就绪过滤。"""
    if request.status not in ("active", "disabled"):
        raise HTTPException(status_code=400, detail="status 必须是 active/disabled")
    row = db.get(KnowledgeEdge, edge_id)
    if not row:
        raise HTTPException(status_code=404, detail="边不存在")
    row.status = request.status
    db.commit()
    return {"success": True, "edge_id": edge_id, "status": row.status}


class BindArtifactRequest(BaseModel):
    kp: str
    artifact_id: str


@router.post("/bind-artifact")
async def bind_artifact(
    request: BindArtifactRequest,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("workspace", "update")),
):
    """知识点绑定工件（REQ-KMV-003）：仅本人名下工件；重复绑定幂等覆盖。"""
    from app.models.database import Artifact
    user_id = current_user.get("user_id") or ""
    art = db.query(Artifact).filter(Artifact.id == request.artifact_id,
                                    Artifact.owner_teacher_id == user_id).first()
    if not art:
        raise HTTPException(status_code=404, detail="工件不存在或不属于你")
    row = db.query(KPArtifactBinding).filter(
        KPArtifactBinding.kp == request.kp,
        KPArtifactBinding.artifact_id == request.artifact_id).first()
    if not row:
        db.add(KPArtifactBinding(kp=request.kp[:128], artifact_id=request.artifact_id,
                                 created_by=user_id))
        db.commit()
    return {"success": True, "kp": request.kp, "artifact_id": request.artifact_id}
