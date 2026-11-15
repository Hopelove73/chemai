"""管理端概览与运营聚合（admin-console-p0 / ops-dashboard）—— 推导式只读。

门说明：概览 `student:create`（admin / academic_admin 持有）——**不能**用
`school:read`：治理域全体员工都持 school:read，会把概览漏给全体教师
（菜单测试实测抓到，见 design D2）。运营聚合同此口径（`school:read` 系
ops-dashboard 的门，teacher/subject_lead 无 student:create，同样进不来）。

字段级裁剪：教师待审数字段仅对 `can("teacher","create")` 的角色返回 ——
academic_admin 看不到教师审批，就不下发该字段（而不是下发 0）。
超管（ADR-0011）：未 break-glass 时数据范围为空集，返回 `data_scope_empty=True`
的明确空态标记，前端渲染提示而非空计数冒充数据。
"""
from datetime import datetime, timedelta

from fastapi import APIRouter, Depends
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.middleware.auth import PermissionChecker, can
from app.middleware.break_glass import active_session
from app.models.database import (
    Account, Class, Grade, Student, StudentAnswer, StudentParentBinding,
    Teacher, TeacherApplication, get_db,
)
from app.models.warning_log import WarningLog

router = APIRouter(prefix="/api/admin", tags=["管理端"])

# 八图聚合在演示库（76k 作答）上约 1s；60s 进程内缓存，避免每次切页都重算。
# 缓存键含学校与提权状态 —— 超管提权后必须立刻看到全量，不能吃旧缓存。
_CHARTS_TTL_SECONDS = 60
_charts_cache: dict[tuple, tuple[float, dict]] = {}

SMALL_SAMPLE_N = 10      # 低于此值前端只画点、不显示百分比（与调研结论一致）
HEATMAP_CLASSES_MAX = 8  # 热力最多 8 个班（再多就看不出格子）
HEATMAP_KPS_MAX = 12
WEAK_KPS_MAX = 10


def _school_filtered_students(db: Session, school_id):
    """学校范围内的学生查询；school_id 为空时不加学校过滤（超管提权 / 单校部署）。

    **始终 join Class**：八图里有多处按班级分组/取班级名，若只在有 school_id 时才 join，
    超管提权（school_id=None）会退化成 Class 的笛卡尔积。
    """
    q = db.query(Student).join(Class, Student.class_id == Class.class_id)
    if school_id:
        q = q.join(Grade, Class.grade_id == Grade.grade_id).filter(Grade.school_id == school_id)
    return q


@router.get("/overview")
async def get_admin_overview(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("student", "create")),
):
    """管理概览与待办 —— 全部为真实推导值，无数据源的字段不下发。"""
    # 超管（ADR-0011）：默认空范围；break-glass 生效期间放开**全部学校**
    # （同 RequestScope._all_classes_if_elevated，不受 token school_id 限制）。
    if current_user["role"] == "super_admin":
        if not active_session(db, current_user["user_id"]):
            return {"success": True, "data_scope_empty": True}
        school_id = None
    else:
        school_id = current_user.get("school_id")

    students_q = _school_filtered_students(db, school_id)
    classes_q = db.query(Class)
    teachers_q = db.query(Teacher)
    if school_id:
        classes_q = classes_q.join(Grade, Class.grade_id == Grade.grade_id).filter(
            Grade.school_id == school_id
        )
        teachers_q = teachers_q.filter(Teacher.school_id == school_id)

    overview = {
        "success": True,
        "data_scope_empty": False,
        "counts": {
            "students": students_q.count(),
            "teachers": teachers_q.count(),
            "classes": classes_q.count(),
        },
        "pending": {
            "students": students_q.filter(Student.status == "pending").count(),
            "warnings": students_q.join(
                WarningLog, WarningLog.student_id == Student.student_id
            ).filter(WarningLog.status == "pending").count(),
        },
    }

    # 教师入驻申请是平台级数据（表上无 school 外键），仅对能审批教师的人可见。
    if can(current_user["role"], "teacher", "create"):
        overview["pending"]["teacher_applications"] = (
            db.query(TeacherApplication).filter(TeacherApplication.status == "pending").count()
        )

    return overview


@router.get("/ops-dashboard")
async def get_ops_dashboard(
    days: int = 14,
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("student", "create")),
):
    """学校运营聚合（ops-dashboard）：作答趋势 / 预警处理率 / 集成健康标记。

    全部为真实推导值；时间窗硬上限 30 天（design D-risks）；无数据的日期补零
    成连续序列（诚实空值 → 前端空态标注「数据自日志起点开始」）。
    集成卡仅在 integration:read 持有者（admin）下发；persistence 转正前
    恒为 `{"placeholder": true}`（IntegrationPage 的占位现状如实传导）。
    """
    school_id = current_user.get("school_id")
    if current_user["role"] == "super_admin" and not active_session(db, current_user["user_id"]):
        return {"success": True, "data_scope_empty": True}
    if current_user["role"] == "super_admin":
        school_id = None  # 提权生效：放开全部学校（同 overview）

    days = max(1, min(days, 30))
    since = datetime.utcnow() - timedelta(days=days)

    students_q = _school_filtered_students(db, school_id)
    answers_q = (
        students_q.join(StudentAnswer, StudentAnswer.student_id == Student.student_id)
        .filter(StudentAnswer.answered_at >= since)
    )
    rows = (
        answers_q.with_entities(
            func.date(StudentAnswer.answered_at).label("day"),
            func.count().label("n"),
            func.sum(case((StudentAnswer.is_correct.is_(True), 1), else_=0)).label("ok"),
        )
        .group_by(func.date(StudentAnswer.answered_at))
        .all()
    )
    by_day = {str(r.day): (r.n, int(r.ok or 0)) for r in rows}
    trend = []
    for i in range(days):
        d = (datetime.utcnow() - timedelta(days=days - 1 - i)).strftime("%Y-%m-%d")
        n, ok = by_day.get(d, (0, 0))
        trend.append({"date": d, "answers": n, "accuracy": round(ok / n, 4) if n else None})

    warn_rows = (
        students_q.join(WarningLog, WarningLog.student_id == Student.student_id)
        .with_entities(WarningLog.status, func.count())
        .group_by(WarningLog.status)
        .all()
    )
    warnings = {s: c for s, c in warn_rows}
    total_warnings = sum(warnings.values())

    out = {
        "success": True,
        "data_scope_empty": False,
        "days": days,
        "trend": trend,
        "warnings": {
            "pending": warnings.get("pending", 0),
            "processed": warnings.get("processed", 0),
            "ignored": warnings.get("ignored", 0),
            "total": total_warnings,
        },
    }
    if can(current_user["role"], "integration", "read"):
        # 集成健康（integration-persistence 转正后为真实数据）：平台启用/连接计数
        try:
            from app.models.database import IntegrationConfig
            row = db.get(IntegrationConfig, 1)
            lms = ((row.config if row else None) or {}).get("lms", {})
            enabled = sum(1 for pc in lms.values() if pc.get("enabled"))
            connected = sum(1 for pc in lms.values() if pc.get("connected"))
            out["integration"] = {"placeholder": False, "enabled": enabled, "connected": connected}
        except Exception:
            out["integration"] = {"placeholder": True}
    return out


# ─────────────────────────────────────────────────────────────
# 八图概览（admin-shell-v2）
# ─────────────────────────────────────────────────────────────

def _month_keys(now: datetime, count: int = 12) -> list[str]:
    """最近 count 个月的 'YYYY-MM'（含当月），升序。"""
    keys = []
    y, m = now.year, now.month
    for _ in range(count):
        keys.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(keys))


def _trend(db: Session, students_q, school_id) -> dict:
    now = datetime.utcnow()
    start = (now - timedelta(days=366)).replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    rows = (
        students_q.join(StudentAnswer, StudentAnswer.student_id == Student.student_id)
        .filter(StudentAnswer.answered_at >= start)
        .with_entities(
            func.strftime("%Y-%m", StudentAnswer.answered_at).label("month"),
            func.count().label("n"),
            func.sum(case((StudentAnswer.is_correct.is_(True), 1), else_=0)).label("ok"),
        )
        .group_by("month")
        .all()
    )
    by_month = {r.month: (r.n, int(r.ok or 0)) for r in rows if r.month}
    points = []
    for key in _month_keys(now):
        n, ok = by_month.get(key, (0, 0))
        points.append({"month": key, "answers": n,
                       "accuracy": round(ok / n, 4) if n else None})

    latest = (
        students_q.join(StudentAnswer, StudentAnswer.student_id == Student.student_id)
        .with_entities(func.max(StudentAnswer.answered_at)).scalar()
    )
    return {
        "granularity": "month",
        "points": points,
        "data_until": latest.strftime("%Y-%m-%d") if latest else None,
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    v = sorted(values)
    mid = len(v) // 2
    return v[mid] if len(v) % 2 else (v[mid - 1] + v[mid]) / 2


def _class_compare(db: Session, students_q) -> list[dict]:
    """班级对比。按「学生 × 班级」先聚一层，再在 Python 里算均值与**中位数**。

    中位数是给 10≤n<30 那一档用的（R-ADM-1）：小样本下均值会被极值带跑，
    中位数更稳。一次查询 340 行左右，成本可忽略。
    """
    rows = (
        students_q.join(StudentAnswer, StudentAnswer.student_id == Student.student_id)
        .filter(StudentAnswer.confirmed.is_(True))   # 只算已确认作答（成绩确认门）
        .with_entities(
            Class.class_id, Class.name, Student.student_id,
            func.count().label("n"),
            func.sum(case((StudentAnswer.is_correct.is_(True), 1), else_=0)).label("ok"),
        )
        .group_by(Class.class_id, Class.name, Student.student_id)
        .all()
    )
    per_class: dict[str, dict] = {}
    for r in rows:
        n = int(r.n or 0)
        if not n:
            continue
        c = per_class.setdefault(r.class_id, {
            "class_id": r.class_id, "class_name": r.name,
            "answers": 0, "_correct": 0, "_student_acc": [],
        })
        c["answers"] += n
        c["_correct"] += int(r.ok or 0)
        c["_student_acc"].append(int(r.ok or 0) / n)

    out = []
    for c in per_class.values():
        accs = c.pop("_student_acc")
        correct = c.pop("_correct")
        n = c["answers"]
        out.append({
            **c,
            # accuracy 仍是**作答事件加权**（与 kb_error_rate 同口径，不另立一套）；
            # median 另算，只服务 10≤n<30 那一档的稳健展示。
            "accuracy": round(correct / n, 4) if n else None,
            "median": round(_median(accs), 4) if accs else None,
            "students": len(accs),
            "small_sample": n < SMALL_SAMPLE_N,
        })
    out.sort(key=lambda x: x["answers"], reverse=True)
    return out


def _student_scatter(db: Session, students_q, limit: int = 300) -> list[dict]:
    rows = (
        students_q.join(StudentAnswer, StudentAnswer.student_id == Student.student_id)
        .filter(StudentAnswer.confirmed.is_(True))
        .with_entities(
            Student.student_id, Student.name, Class.name.label("class_name"),
            func.count().label("n"),
            func.sum(case((StudentAnswer.is_correct.is_(True), 1), else_=0)).label("ok"),
        )
        .group_by(Student.student_id, Student.name, Class.name)
        .order_by(func.count().desc())
        .limit(limit)
        .all()
    )
    return [{
        "student_id": r.student_id,
        "name": r.name,
        "class_name": r.class_name,
        "answers": int(r.n),
        "accuracy": round(int(r.ok or 0) / int(r.n), 4) if r.n else None,
    } for r in rows]


def _kp_error_rates(db: Session, class_id: str | None = None) -> dict:
    """复用 kb_error_rate 的作答事件级口径（不另写统计）。失败即空 —— 概览不该因知识库挂掉而 500。

    ⚠️ `error_rates_for_kps` 收的是 **Connection** 不是 Engine：传 `get_bind()` 会在
    第一次 `conn.execute` 时抛 AttributeError，被 except 吞掉后表现为「热力图永远空」。
    这里显式开连接（`db.connection()` 即是当前事务的连接）。
    """
    try:
        from app.services.kb_dictionary import load_all_dictionaries
        from app.services.kb_error_rate import error_rates_for_kps
        from pathlib import Path
        dic_dir = Path(__file__).resolve().parents[2] / "content" / "kb"
        codes = [it["code"] for it in load_all_dictionaries(str(dic_dir))["items"]]
        conn = db.connection()
        return error_rates_for_kps(conn, codes, class_id=class_id)
    except Exception:
        return {}


def _weak_kps(db: Session) -> list[dict]:
    rates = _kp_error_rates(db)
    items = [
        {"kp_id": code, "error_rate": round(v["error_rate"], 4), "answers": int(v["answers"])}
        for code, v in rates.items()
    ]
    items.sort(key=lambda x: (x["error_rate"], x["answers"]), reverse=True)
    return items[:WEAK_KPS_MAX]


def _kp_heatmap(db: Session, students_q) -> dict:
    # 行：学生数最多的前 8 个班；列：全局有样本的错误率最高的前 12 个知识点
    class_rows = (
        students_q.with_entities(Class.class_id, Class.name, func.count().label("n"))
        .group_by(Class.class_id, Class.name)
        .order_by(func.count().desc())
        .limit(HEATMAP_CLASSES_MAX)
        .all()
    )
    weak = _weak_kps(db)[:HEATMAP_KPS_MAX]
    kp_ids = [w["kp_id"] for w in weak]
    cells = []
    for ci, c in enumerate(class_rows):
        rates = _kp_error_rates(db, class_id=c.class_id) if kp_ids else {}
        for ki, code in enumerate(kp_ids):
            r = rates.get(code)
            cells.append({
                "class_index": ci,
                "kp_index": ki,
                "error_rate": round(r["error_rate"], 4) if r else None,
                "answers": int(r["answers"]) if r else 0,
            })
    return {
        "classes": [{"class_id": c.class_id, "class_name": c.name, "students": int(c.n)}
                    for c in class_rows],
        "kps": [{"kp_id": w["kp_id"], "error_rate": w["error_rate"]} for w in weak],
        "cells": cells,
    }


def _score_dist(db: Session, students_q) -> dict:
    """每次「学生 × 考试」的得分率分布（10 分一档）。

    没有单题总分字段，用该生在该场考试的作答正确率作为得分率 —— 口径写在 label 里，
    避免被读成"分数"。样本 0 时 bins 为空。
    """
    rows = (
        students_q.join(StudentAnswer, StudentAnswer.student_id == Student.student_id)
        .filter(StudentAnswer.exam_record_id.isnot(None), StudentAnswer.confirmed.is_(True))
        .with_entities(
            StudentAnswer.exam_record_id, StudentAnswer.student_id,
            func.count().label("n"),
            func.sum(case((StudentAnswer.is_correct.is_(True), 1), else_=0)).label("ok"),
        )
        .group_by(StudentAnswer.exam_record_id, StudentAnswer.student_id)
        .all()
    )
    labels = ["0-59%", "60-69%", "70-79%", "80-89%", "90-100%"]
    counts = [0] * 5
    for r in rows:
        n = int(r.n or 0)
        if not n:
            continue
        rate = int(r.ok or 0) / n
        pct = rate * 100
        idx = 0 if pct < 60 else 1 if pct < 70 else 2 if pct < 80 else 3 if pct < 90 else 4
        counts[idx] += 1
    return {
        "metric": "exam_score_rate",
        "bins": [{"label": lb, "count": c} for lb, c in zip(labels, counts)],
        "n": sum(counts),
    }


def _parent_funnel(db: Session, students_q) -> dict:
    """绑定 → 有账号 → 近 7 天活跃。

    两段都必须**限定在当前数据范围内**：否则跨校场景下"有账号"会数进别校家长，
    出现 activated > bound 的荒谬值（review 指出）。
    """
    stu_ids = students_q.with_entities(Student.student_id).subquery()
    scoped_bindings = db.query(StudentParentBinding).filter(
        StudentParentBinding.status == "active",
        StudentParentBinding.student_id.in_(select(stu_ids.c.student_id)),
    )
    bound = scoped_bindings.count()
    scoped_parent_ids = scoped_bindings.with_entities(StudentParentBinding.parent_id).subquery()
    activated = db.query(Account).filter(
        Account.role == "parent",
        Account.status == "active",
        Account.parent_id.in_(select(scoped_parent_ids.c.parent_id)),
    ).count()
    # 近 7 天活跃：**当前没有可信数据源**（Account 无 last_active_date，登录未记活跃）。
    # 诚实返回 None + available=False，前端显示「未采集」而不是 0。
    return {"bound": bound, "activated": activated,
            "active_7d": None, "active_7d_available": False}


def _charts_payload(db: Session, school_id) -> dict:
    students_q = _school_filtered_students(db, school_id)
    classes_q = db.query(Class)
    teachers_q = db.query(Teacher)
    if school_id:
        classes_q = classes_q.join(Grade, Class.grade_id == Grade.grade_id).filter(
            Grade.school_id == school_id)
        teachers_q = teachers_q.filter(Teacher.school_id == school_id)

    warn_rows = (
        students_q.join(WarningLog, WarningLog.student_id == Student.student_id)
        .with_entities(WarningLog.status, func.count())
        .group_by(WarningLog.status)
        .all()
    )
    warn = {s: c for s, c in warn_rows}
    funnel = _parent_funnel(db, students_q)

    return {
        "kpi": {
            "students": students_q.count(),
            "teachers": teachers_q.count(),
            "classes": classes_q.count(),
            "parent_bindings": funnel["bound"],
            "parent_accounts": funnel["activated"],
        },
        "trend": _trend(db, students_q, school_id),
        "parent_funnel": funnel,
        "class_compare": _class_compare(db, students_q),
        "kp_heatmap": _kp_heatmap(db, students_q),
        "student_scatter": _student_scatter(db, students_q),
        "weak_kps": _weak_kps(db),
        "score_dist": _score_dist(db, students_q),
        "warning": {
            "identified": sum(warn.values()),
            "processed": warn.get("processed", 0),
            # 没有处理记录时明示「闭环未接入」，前端不画处理率
            "closed_loop_available": warn.get("processed", 0) > 0,
        },
    }


@router.get("/overview-charts")
async def get_overview_charts(
    db: Session = Depends(get_db),
    current_user: dict = Depends(PermissionChecker("student", "create")),
):
    """管理概览八图（admin-shell-v2）。

    门与概览同口径（student:create → admin / academic_admin）；超管未提权返回空态。
    60s 进程内缓存，键含学校与提权状态（提权后立刻看到全量）。
    """
    elevated = False
    if current_user["role"] == "super_admin":
        elevated = bool(active_session(db, current_user["user_id"]))
        if not elevated:
            return {"success": True, "data_scope_empty": True}
        school_id = None
    else:
        school_id = current_user.get("school_id")

    cache_key = (school_id or "__all__", elevated)
    now_ts = datetime.utcnow().timestamp()
    hit = _charts_cache.get(cache_key)
    if hit and now_ts - hit[0] < _CHARTS_TTL_SECONDS:
        return {"success": True, "data_scope_empty": False, **hit[1]}

    payload = _charts_payload(db, school_id)
    _charts_cache[cache_key] = (now_ts, payload)
    return {"success": True, "data_scope_empty": False, **payload}
