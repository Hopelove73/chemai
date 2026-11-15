"""计划草稿 service —— 学习计划「生成 → 修订 → 确认 → 发送」生命周期的唯一家园
（ADR-0012 业务逻辑住 service；ADR-0013 确认门实体化）。

interface 即测试面：
    create_draft(...)      regenerate 语义：作废该生旧活跃草稿，另开版本链
    revise_draft(...)      同链追加版本（记录老师反馈），审批归零（修订须再确认）
    record_decision(...)   确认门三值决定：approve / reject(=revise) / abandon(不改状态)
    send_draft(...)        发送由数据状态强制：未 teacher_approved 一律 approval_needed
    get_active_draft / get_draft / list_versions
    teacher_teaches_student  Data Scope 关系检查（老师—班级—学生）

agent 工具与 REST 端点皆是本 service 之上的薄 adapter，不承载业务逻辑。
db 会话可选注入（测试用内存库），缺省走 get_db()。
"""
import hashlib
import json
import logging
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session
from app.services.work_unit import require_unit

logger = logging.getLogger(__name__)

STATUS_AWAITING = "awaiting_confirmation"
STATUS_SENT = "sent"
STATUS_SUPERSEDED = "superseded"

# 学生端计划读缓存（TTL 24h）——GET /learning-plan/{sid} 读-through，
# 归 service 所有（ADR-0012：API 不持有业务状态）
_plan_cache = {}


def _draft_payload(row) -> Dict:
    return {
        "draft_id": row.draft_id,
        "student_id": row.student_id,
        "version": row.version,
        "status": row.status,
        "content": row.content,
        "feedback": row.feedback,
        "teacher_approved": bool(row.teacher_approved),
        "created_at": row.created_at.strftime("%Y-%m-%d %H:%M") if row.created_at else "",
    }


def _notification_id(draft_id: str, version: int, student_id: str) -> str:
    """由幂等键（draft_id+version）派生确定性通知 ID——重复投递得到同一 ID。"""
    digest = hashlib.sha1(f"plan_draft:{draft_id}:v{version}:{student_id}".encode()).hexdigest()[:12]
    return f"plan_{digest}"


class PlanDraftService:
    """学习计划草稿生命周期。所有流转住在这一处；门由草稿状态强制。"""

    # ── Data Scope（老师—班级—学生关系）─────────────────────────

    @staticmethod
    def teacher_teaches_student(db: Session, teacher_id: str, student_id: str) -> bool:
        """fail-closed：任一关系缺失即 False。员工管理角色的范围检查在 REST
        adapter 层由 ScopeGuard 承担（沿用 ADR-0008/0009 分层）。"""
        if not teacher_id or not student_id:
            return False
        row = db.execute(
            text("SELECT class_id FROM students WHERE student_id = :s"),
            {"s": student_id},
        ).fetchone()
        if not row or not row[0]:
            return False
        hit = db.execute(
            text("SELECT 1 FROM teacher_class_subject WHERE teacher_id = :t "
                 "AND class_id = :c LIMIT 1"),
            {"t": teacher_id, "c": row[0]},
        ).fetchone()
        return hit is not None

    # ── 查询 ────────────────────────────────────────────────

    @staticmethod
    def get_draft(db: Session, draft_id: str) -> Optional[Dict]:
        """版本链当前版本（最新一行）。"""
        row = (db.query(_Draft)
               .filter(_Draft.draft_id == draft_id)
               .order_by(_Draft.version.desc())
               .first())
        return _draft_payload(row) if row else None

    @staticmethod
    def get_active_draft(db: Session, student_id: str) -> Optional[Dict]:
        """该生当前待确认草稿（最新活跃链的最新版本）。"""
        row = (db.query(_Draft)
               .filter(_Draft.student_id == student_id,
                       _Draft.status == STATUS_AWAITING)
               .order_by(_Draft.id.desc())
               .first())
        return _draft_payload(row) if row else None

    @staticmethod
    def list_versions(db: Session, draft_id: str) -> List[Dict]:
        rows = (db.query(_Draft)
                .filter(_Draft.draft_id == draft_id)
                .order_by(_Draft.version.asc())
                .all())
        return [_draft_payload(r) for r in rows]

    # ── 生成（regenerate 语义内建）：算与写分开（R1 D2=A）──────────

    @staticmethod
    def compute_draft(db: Session, student_id: str, student_name: str = "") -> Dict:
        """算出 v1 的内容：查学生、取学情上下文、调模型。

        这一半**必须开在工作单元之外**：模型一次往返数秒，而 SQLite WAL 同一时刻只有
        一个写事务，单元里等模型 = 全库写者排队到超时。返回值是"要写什么"，不是"已写了"。
        """
        student, err = _resolve_student(db, student_id, student_name)
        if err:
            return err
        student_id, student_name = student

        ctx, err = _collect_student_context(db, student_id)
        if err:
            return err

        from app.services.llm_service import llm_service
        result = llm_service.generate_learning_plan(
            student_name=student_name,
            barrier_type=ctx["barrier_type"],
            weak_knowledge_points=ctx["weak_kps"],
            recent_performance=ctx["recent_performance"],
        )
        plan_data, err = _parse_plan(result)
        if err:
            return err
        _attach_review_today(db, plan_data, student_id)
        return {"student_id": student_id, "student_name": student_name,
                "plan_data": plan_data}

    @staticmethod
    def create_draft(db: Session, computed: Dict, created_by: str = "") -> Dict:
        """把 `compute_draft` 的结果落成 v1。写入口：只 flush，须在最外层单元内。

        该生已有活跃草稿 → 全部标 superseded（推翻重来，每生最多一份活跃草稿）。
        """
        require_unit(db)
        student_id = (computed or {}).get("student_id") or ""
        plan_data = (computed or {}).get("plan_data")
        if not student_id or not plan_data:
            return {"error": "缺少可用的草稿内容，请重新生成"}

        db.query(_Draft).filter(
            _Draft.student_id == student_id, _Draft.status == STATUS_AWAITING,
        ).update({"status": STATUS_SUPERSEDED}, synchronize_session=False)

        row = _Draft(
            draft_id=uuid.uuid4().hex[:16], student_id=student_id, version=1,
            status=STATUS_AWAITING, content=plan_data,
            created_by=created_by or "",
        )
        db.add(row)
        db.flush()
        return {**_draft_payload(row),
                "student_name": (computed or {}).get("student_name") or student_id}

    # ── 修订（revise 同链）───────────────────────────────────

    @staticmethod
    def compute_revision(db: Session, draft_id: str, feedback: str) -> Dict:
        """按老师反馈算修订版内容（单元外；与 `create_draft` 同一套算/写分界）。"""
        if not (feedback or "").strip():
            return {"error": "缺少修订意见：请说明要修改的地方"}
        latest_row = _latest_row(db, draft_id)
        if not latest_row:
            return {"error": "该草稿不存在或已被处理，请刷新后重试；若反复出现请重新生成一份"}
        if latest_row.status != STATUS_AWAITING:
            return {"error": f"草稿当前状态为 {latest_row.status}，不能修订"}

        latest = _draft_payload(latest_row)
        student = db.execute(
            text("SELECT name FROM students WHERE student_id = :s"),
            {"s": latest["student_id"]},
        ).fetchone()
        student_name = student[0] if student else latest["student_id"]
        ctx, _ = _collect_student_context(db, latest["student_id"])

        from app.services.llm_service import llm_service
        result = llm_service.revise_learning_plan(
            student_name=student_name,
            previous_plan=latest["content"],
            feedback=feedback.strip(),
            barrier_type=ctx["barrier_type"] if ctx else "concept",
        )
        plan_data, err = _parse_plan(result)
        if err:
            return err
        return {"draft_id": draft_id, "student_id": latest["student_id"],
                "content": plan_data, "feedback": feedback.strip()}

    @staticmethod
    def revise_draft(db: Session, computed: Dict, revised_by: str = "") -> Dict:
        """把算好的修订版追加进版本链，teacher_approved 归零（修订须再确认）。

        写入口。版本号和"还能不能改"在这里**重查一次**：算模型那几秒里另一个请求
        可能已经把这份草稿发掉了，照 `computed` 里的旧值写就会追加出一条孤儿版本。
        """
        require_unit(db)
        latest_row = _latest_row(db, (computed or {}).get("draft_id") or "")
        if not latest_row:
            return {"error": "该草稿不存在或已被处理，请刷新后重试；若反复出现请重新生成一份"}
        if latest_row.status != STATUS_AWAITING:
            return {"error": f"草稿当前状态为 {latest_row.status}，不能修订"}

        row = _Draft(
            draft_id=latest_row.draft_id, student_id=latest_row.student_id,
            version=latest_row.version + 1, status=STATUS_AWAITING,
            content=computed["content"], feedback=computed["feedback"],
            created_by=revised_by or latest_row.created_by or "",
        )
        db.add(row)
        db.flush()
        return _draft_payload(row)

    # ── 确认门三值决定 ────────────────────────────────────────

    @staticmethod
    def record_decision(db: Session, draft_id: str, action: str,
                        actor_id: str = "", feedback: str = "",
                        computed: Optional[Dict] = None) -> Dict:
        """把老师的决定写进草稿数据（门由数据强制，不靠进程态）。写入口。

        approve → teacher_approved=True（发送放行的唯一钥匙；ADR-0009 fail-closed：
                  缺教师身份一律拒绝——不可逆动作必须有人对身份负责）
        reject  → 必带反馈，且必带 `compute_revision` 在单元外算好的 `computed`
        abandon → 不改草稿（本步可弃，草稿保留待以后再发）

        `computed` 是"算与写分开"的接缝（R1 D2=A）：打回要调模型，而模型往返不能
        发生在单元里，所以调用方先算再进来。缺它 = 这条决定会在单元里调模型，
        宁可报错也不默默 held 写锁。

        Data Scope 由 adapter 层强制（REST=ScopeGuard，agent=channel 的
        teacher_teaches_student 关系检查）；本方法管身份与状态。
        """
        require_unit(db)
        latest_row = _latest_row(db, draft_id)
        if not latest_row:
            return {"error": "该草稿不存在或已被处理，请刷新后重试；若反复出现请重新生成一份"}
        if latest_row.status != STATUS_AWAITING:
            return {"error": ("该学习计划已确认发送给学生，无需重复处理" if latest_row.status == "sent" else f"该草稿当前状态（{latest_row.status}）不支持此操作，请刷新后查看最新状态")}

        action = (action or "").strip().lower()
        if action == "approve" and not (actor_id or "").strip():
            return {"error": "缺少教师身份，无法记录批准（fail-closed）"}
        if action == "approve":
            latest_row.teacher_approved = True
            latest_row.approved_by = actor_id or ""
            db.flush()
            return _draft_payload(latest_row)
        if action == "reject":
            if not (feedback or "").strip():
                return {"error": "打回必须附修改意见"}
            if not computed:
                return {"error": "打回的修订版必须先在单元外算好（compute_revision）再带进来"}
            return PlanDraftService.revise_draft(db, computed, actor_id)
        if action == "abandon":
            return _draft_payload(latest_row)
        return {"error": f"未知的确认门决定: {action}"}

    # ── 发送（确认门在这里强制）───────────────────────────────

    @staticmethod
    def send_draft(db: Session, student_id: str, draft_id: str = "",
                   sender_id: str = "") -> Dict:
        """发送草稿给学生。门由数据状态强制：

        - 未确认（teacher_approved=False）→ approval_needed 载荷（含 draft_id），
          门卡由前端/channel 呈现；这是唯一的门，不再有进程级提权布尔。
        - 已确认 → 执行发送效果并置 sent。

        写入口：只 flush，须在最外层调用方的工作单元内（前置一）。发送效果 = 学生
        收件箱 + 历史 + `current_plan` + 草稿行终态，四样东西必须同生同死。
        """
        require_unit(db)
        # 有身份即早查 Data Scope（不给学生外的人看门卡）；缺身份的强制检查
        # 放在批准之后（那时才真正不可逆）。角色感知（批 3 捉虫）：parent:x
        # 走亲子绑定，其余走教师授课关系——与下方不可逆检查同口径
        if (sender_id or "").strip():
            if sender_id.startswith("parent:"):
                from app.services.parent_task_service import parent_of_student
                parent_ok = parent_of_student(db, sender_id[len("parent:"):], student_id)
                if not parent_ok:
                    return {"error": f"你没有向学生 {student_id} 发送学习计划的权限（未绑定该生）"}
            elif not PlanDraftService.teacher_teaches_student(db, sender_id, student_id):
                return {"error": f"你没有向学生 {student_id} 发送学习计划的权限（Data Scope 不含该生）"}

        payload = (PlanDraftService.get_draft(db, draft_id) if draft_id
                   else PlanDraftService.get_active_draft(db, student_id))
        if not payload:
            latest_any = (db.query(_Draft)
                          .filter(_Draft.student_id == student_id)
                          .order_by(_Draft.id.desc()).first())
            hint = f"（该生最近一份草稿状态为 {latest_any.status}）" if latest_any else "（该生从未生成过草稿）"
            return {
                "approval_needed": True,
                "message": f"即将把学习计划发送给学生 {student_id}，但没有可发送的待确认草稿{hint}",
                "target": student_id,
                "action": "send_student",
                "student_id": student_id,
            }
        if payload["status"] != STATUS_AWAITING or not payload["teacher_approved"]:
            return {
                "approval_needed": True,
                "message": f"学习计划（第 {payload['version']} 版）尚未经老师确认，请先确认再发送",
                "target": student_id,
                "action": "send_student",
                "draft_id": payload["draft_id"],
                "version": payload["version"],
                "student_id": student_id,
            }
        # 即将不可逆发送（ADR-0009 fail-closed）：缺身份 = 无人可对发送负责 = 拒绝。
        # 域校验角色感知（批 3 捉虫修正）：parent:x 发送者走亲子绑定校验
        # （家长派任务门），其余走教师授课关系——家长传授课关系必被拒。
        if not (sender_id or "").strip():
            return {"error": "缺少发送者身份，无法发送（fail-closed）"}
        if sender_id.startswith("parent:"):
            from app.services.parent_task_service import parent_of_student
            if not parent_of_student(db, sender_id[len("parent:"):], student_id):
                return {"error": f"你没有向学生 {student_id} 发送学习计划的权限（未绑定该生）"}
        elif not PlanDraftService.teacher_teaches_student(db, sender_id, student_id):
            return {"error": f"你没有向学生 {student_id} 发送学习计划的权限（Data Scope 不含该生）"}

        student = db.query(_Student).filter(
            _Student.student_id == student_id).first()
        if not student:
            return {"error": f"学生 {student_id} 不存在"}

        content = payload["content"]
        student.current_plan = content
        db.add(_History(
            student_id=student_id,
            plan_title=content.get("plan_title", "学习计划"),
            plan_data=content,
        ))
        # 学生收件箱投递（幂等键 draft_id+version，重复投递不重复插入）
        nid = _notification_id(payload["draft_id"], payload["version"], student_id)
        exists = db.query(_StudentNotification).filter(
            _StudentNotification.notification_id == nid).first()
        if not exists:
            db.add(_StudentNotification(
                notification_id=nid,
                student_id=student_id,
                type="learning_plan",
                title="📋 你有一份新的学习计划",
                content=content.get("plan_title", "学习计划"),
                # 学生端收件卡片上要显示「什么时候收到的」，就是这一列。
                # 以前没赋值 → 全是 NULL，卡片只能写个模糊的「老师发来的」。
                sent_at=datetime.utcnow(),
            ))
        row = db.query(_Draft).filter(
            _Draft.draft_id == payload["draft_id"],
            _Draft.version == payload["version"],
        ).first()
        row.status = STATUS_SENT
        db.flush()
        # 发送落定 = 这扇门按判据不再算数，**当场**收敛（gate-lifecycle 2.2）。
        #
        # 接在这里而不是让 6 个调用点各自记得关：发送成功是「门的事办完了」的
        # 唯一判据，绑在动作本身上，谁调都合规（REST 直发、agent 重放、面板直发）。
        #
        # 不收的后果不是多一行脏数据：`guard` 拿「库里还有 open 的门」当「有一张
        # 确认卡待老师处理」，于是拒掉 agent 的**一切**工具调用 —— 计划卡永远停在
        # 等待态、run 永远 running（2026-10-16 端到端实测的死锁）。
        from app.services.gate_service import gate_service as _gs
        _gs.supersede_stale_gates(db, draft_id=payload["draft_id"])

        # 内存缓存同步（学生端即刻可见；缓存归 service 所有，API 读-through）。
        # 必须排在提交之后：整单回滚时这条不能发生，否则学生端读到一份没落库的计划。
        from app.services.work_unit import after_commit
        after_commit(lambda: _plan_cache.__setitem__(
            student_id,
            {"plan": content, "expires_at": datetime.utcnow().timestamp() + 86400}))

        return {
            "result": f"学习计划已确认发送，{student.name} 可在学生端查看",
            "student_id": student_id,
            "student_name": student.name,
            "draft_id": payload["draft_id"],
            "version": payload["version"],
        }


# ── 内部：模型别名与帮助函数（implementation, not interface）────

from app.models.database import (  # noqa: E402
    LearningPlanDraft as _Draft,
    LearningPlanHistory as _History,
    Student as _Student,
    StudentNotification as _StudentNotification,
)


def _latest_row(db: Session, draft_id: str):
    return (db.query(_Draft)
            .filter(_Draft.draft_id == draft_id)
            .order_by(_Draft.version.desc())
            .first())


def _resolve_student(db: Session, student_id: str, student_name: str):
    """名字/学号二选一解析出唯一学生。返回 ((student_id, name), None) 或 (None, err)。"""
    if student_name and not student_id:
        rows = db.execute(
            text("SELECT student_id, name FROM students WHERE name LIKE :n"),
            {"n": f"%{student_name}%"},
        ).fetchall()
        if not rows:
            return None, {"error": f"未找到名为 '{student_name}' 的学生"}
        if len(rows) > 1:
            return None, {
                "multiple_matches": [{"student_id": r[0], "name": r[1]} for r in rows],
                "hint": "找到多个匹配学生，请指定学号",
            }
        return (rows[0][0], rows[0][1]), None
    if not student_id:
        return None, {"error": "请提供 student_id 或 student_name"}
    row = db.execute(
        text("SELECT student_id, name FROM students WHERE student_id = :s"),
        {"s": student_id},
    ).fetchone()
    if not row:
        return None, {"error": f"学生 {student_id} 不存在"}
    return (row[0], row[1]), None


def _collect_student_context(db: Session, student_id: str) -> tuple:
    """障碍类型（主导）+ 薄弱知识点 + 近期正确率 —— LLM 生成/修订共用上下文。"""
    try:
        student = db.query(_Student).filter(_Student.student_id == student_id).first()
        barrier_raw = student.barrier_type if student else {}
        if isinstance(barrier_raw, str):
            try:
                barrier_raw = json.loads(barrier_raw)
            except (json.JSONDecodeError, TypeError):
                barrier_raw = {}
        dominant = "concept"
        if isinstance(barrier_raw, dict) and barrier_raw:
            dominant = max(barrier_raw, key=barrier_raw.get)
    except Exception:
        dominant = "concept"

    weak_kps: List[str] = []
    try:
        wk_rows = db.execute(text(
            "SELECT q.knowledge_points FROM student_answers sa "
            "JOIN questions q ON sa.question_id = q.question_id "
            "WHERE sa.student_id = :s AND sa.is_correct = 0 "
            "ORDER BY sa.answered_at DESC LIMIT 20"
        ), {"s": student_id}).fetchall()
        kp_count: Dict[str, int] = {}
        for r in wk_rows:
            kps = r[0] or []
            if isinstance(kps, str):
                try:
                    kps = json.loads(kps)
                except (json.JSONDecodeError, TypeError):
                    kps = [kps]
            for kp in kps:
                kp_count[kp] = kp_count.get(kp, 0) + 1
        weak_kps = [kp for kp, _ in sorted(kp_count.items(), key=lambda x: -x[1])[:5]]
    except Exception:
        weak_kps = []

    recent_performance = None
    try:
        agg = db.execute(text(
            "SELECT COUNT(*) total, SUM(CASE WHEN is_correct THEN 1 ELSE 0 END) "
            "FROM student_answers WHERE student_id = :s"
        ), {"s": student_id}).fetchone()
        if agg and agg[0]:
            total, correct = agg[0], agg[1] or 0
            recent_performance = {"total_answers": total, "correct_answers": correct,
                                  "accuracy": round(correct / total, 3)}
    except Exception:
        recent_performance = None

    return {"barrier_type": dominant, "weak_kps": weak_kps,
            "recent_performance": recent_performance}, None


def _attach_review_today(db, plan_data: Dict, student_id: str) -> None:
    """计划内容挂「今日复习」（批次 B）：到期记忆卡的确定性数据，不走 LLM。
    复习数据缺失绝不阻断计划生成——这是增强项不是依赖项。"""
    try:
        from app.services import memory_card_service as mcs
        cards = mcs.due_cards(db, student_id, limit=5)
        plan_data["review_today"] = {
            "due_count": mcs.due_count(db, student_id),
            "items": [{"question_id": c.question_id} for c in cards],
        }
    except Exception:  # noqa: BLE001
        pass


def _parse_plan(result: Dict) -> tuple:
    if not result.get("success") or not result.get("content", "").strip():
        return None, {"error": "学习计划生成失败，请重试"}
    try:
        plan_data = json.loads(result["content"])
    except (json.JSONDecodeError, TypeError):
        return None, {"error": "学习计划解析失败，请重试"}
    if not isinstance(plan_data, dict) or not plan_data.get("plan_title"):
        return None, {"error": "LLM 返回的计划内容不完整，请重试"}
    return plan_data, None


plan_draft_service = PlanDraftService()
