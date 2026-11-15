"""家长报告草稿 service —— 第二个确认门工件的生命周期唯一家园
（ADR-0013；issue #12）。与 PlanDraftService 同构：
    create_draft(...)      聚合学生周数据生成报告草稿 v1（regenerate 作废旧活跃草稿）
    revise_draft(...)      同链追加版本（LLM 只改写叙述性字段，事实字段不动）
    record_decision(...)   三值决定：approve（须教师身份）/ reject / abandon
    send_draft(...)        门由数据状态强制；写 ParentNotification（幂等键
                           draft_id+version+parent_id）并置 sent
Data Scope 关系检查复用 plan_draft_service.teacher_teaches_student。
"""
import hashlib
import json
import uuid
from collections import Counter
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import text
from sqlalchemy.orm import Session
from app.services.work_unit import require_unit

STATUS_AWAITING = "awaiting_confirmation"
STATUS_SENT = "sent"
STATUS_SUPERSEDED = "superseded"


def _payload(row) -> Dict:
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


def _notification_id(student_id: str, content: Dict, parent_id: str) -> str:
    """幂等键 = 学生 × 报告内容 × 家长（不含 draft_id）。

    `_aggregate` 是纯查库聚合：数据没变，重新生成的报告逐字节相同。旧键把
    draft_id 算进去，于是「重新生成 → 再确认 → 再发送」会给家长再投一条一模一样的
    通知（2026-10 实测 parent_241914 收到两条内容相同的「📋 陈湘旻 的学习报告」，B1）。
    内容真变了（练习量/薄弱点/建议不同）照旧产生一条新通知。
    """
    payload = json.dumps({"student_id": student_id, "parent_id": parent_id,
                          "content": content or {}},
                         ensure_ascii=False, sort_keys=True)
    return f"rep_{hashlib.sha1(payload.encode('utf-8')).hexdigest()[:12]}"


def _dominant(barrier: dict) -> tuple:
    """主导障碍类型与占比。空/坏数据返回 ('unknown', 0)。"""
    if not barrier:
        return ("unknown", 0)
    try:
        return max(barrier.items(), key=lambda x: x[1])
    except (ValueError, AttributeError):
        return ("unknown", 0)


_MISSING_DRAFT = "该草稿不存在或已被处理，请刷新后重试；若反复出现请重新生成一份"


def _revision_blocked(status: str) -> Dict:
    """不能修订时的两种人话：已经发出去了，或者卡在别的状态上。"""
    if status == "sent":
        return {"error": "该学习报告已确认发送给家长，无法再修订"}
    return {"error": f"该报告当前状态（{status}）不支持修订，请刷新后查看最新状态"}


class ParentReportDraftService:
    def _row(self, db: Session, draft_id: str):
        return (db.query(_Draft)
                .filter(_Draft.draft_id == draft_id)
                .order_by(_Draft.version.desc())
                .first())

    # ── 查询 ────────────────────────────────────────────────

    def get_draft(self, db: Session, draft_id: str) -> Optional[Dict]:
        row = self._row(db, draft_id)
        return _payload(row) if row else None

    def get_active_draft(self, db: Session, student_id: str) -> Optional[Dict]:
        row = (db.query(_Draft)
               .filter(_Draft.student_id == student_id, _Draft.status == STATUS_AWAITING)
               .order_by(_Draft.id.desc())
               .first())
        return _payload(row) if row else None

    def list_versions(self, db: Session, draft_id: str) -> List[Dict]:
        rows = (db.query(_Draft).filter(_Draft.draft_id == draft_id)
                .order_by(_Draft.version.asc()).all())
        return [_payload(r) for r in rows]

    # ── 生成（聚合逻辑自 agent 工具文件迁入，ADR-0012）──────────

    def create_draft(self, db: Session, student_id: str, created_by: str = "") -> Dict:
        """聚合本周学情生成报告 v1。写入口：只 flush，须在最外层单元内。

        与学习计划不同，这条不调模型（`_aggregate` 全是查库聚合），所以没有"算与写
        分开"那一半；`supersede 旧活跃版本 + 追加 v1` 两步写入必须同属一个单元。
        """
        require_unit(db)
        report, err = self._aggregate(db, student_id)
        if err:
            return err
        db.query(_Draft).filter(
            _Draft.student_id == student_id, _Draft.status == STATUS_AWAITING,
        ).update({"status": STATUS_SUPERSEDED}, synchronize_session=False)
        row = _Draft(
            draft_id=uuid.uuid4().hex[:16], student_id=student_id, version=1,
            status=STATUS_AWAITING, content=report, created_by=created_by or "",
        )
        db.add(row)
        db.flush()
        return _payload(row)

    def _aggregate(self, db: Session, student_id: str) -> tuple:
        row = db.execute(
            text("SELECT s.name, s.barrier_type, s.exercises_completed, s.class_id, c.name "
                 "FROM students s LEFT JOIN classes c ON s.class_id=c.class_id WHERE s.student_id=:sid"),
            {"sid": student_id},
        ).fetchone()
        if not row:
            return None, {"error": f"学生 {student_id} 不存在"}
        sname = row[0]
        barrier = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
        ex_count = row[2] or 0
        class_name = row[4] or ""

        week_start = datetime.utcnow() - timedelta(days=7)
        week_answers = db.execute(
            text("SELECT is_correct FROM student_answers WHERE student_id=:sid AND answered_at >= :ws"),
            {"sid": student_id, "ws": week_start},
        ).fetchall()
        total_w = len(week_answers)
        correct_w = sum(1 for a in week_answers if a[0])

        kp_counter: Counter = Counter()
        wrong_answers = db.execute(
            text("SELECT question_id FROM student_answers WHERE student_id=:sid "
                 "AND is_correct=0 ORDER BY answered_at DESC LIMIT 30"),
            {"sid": student_id},
        ).fetchall()
        for (qid,) in wrong_answers:
            q = db.execute(
                text("SELECT knowledge_points FROM questions WHERE question_id=:q"), {"q": qid}
            ).fetchone()
            if q and q[0]:
                kps = json.loads(q[0]) if isinstance(q[0], str) else q[0]
                for kp in kps:
                    kp_counter[kp] += 1
        weak_kps = [{"name": kp, "errors": c} for kp, c in kp_counter.most_common(5)]

        barrier_labels = {"concept": "概念理解", "reading": "审题仔细度", "expression": "答题表述"}
        dominant = _dominant(barrier)
        dom_label = barrier_labels.get(dominant[0], dominant[0])
        suggestions = {
            "concept": "建议帮孩子建立知识框架图，用思维导图梳理化学概念之间的联系。",
            "reading": "建议每天陪孩子读一道题，让他把「题目给了什么条件」说出来。",
            "expression": "鼓励孩子写完整的解题过程，化学符号和方程式要写规范。",
        }
        accuracy = round(correct_w / total_w * 100) if total_w > 0 else 0
        report = {
            "student_name": sname,
            "class_name": class_name,
            "exercises_completed": ex_count,
            "week_practice_count": total_w,
            "week_accuracy": accuracy,
            "dominant_barrier": dom_label,
            "barrier_distribution": barrier,
            "weak_knowledge_points": weak_kps,
            "suggestion": suggestions.get(dominant[0], "多鼓励孩子，关注学习过程而非分数。"),
        }
        return report, None

    # ── 修订（revise 同链）───────────────────────────────────

    def compute_revision(self, db: Session, draft_id: str, feedback: str) -> Dict:
        """按老师反馈算报告修订版（单元外；模型往返不进单元，见学习计划侧同一分界）。"""
        if not (feedback or "").strip():
            return {"error": "缺少修订意见：请说明要修改的地方"}
        latest = self._row(db, draft_id)
        if not latest:
            return {"error": _MISSING_DRAFT}
        if latest.status != STATUS_AWAITING:
            return _revision_blocked(latest.status)

        from app.services.llm_service import llm_service
        result = llm_service.revise_parent_report(
            student_name=latest.content.get("student_name", ""),
            previous_report=latest.content,
            feedback=feedback.strip(),
        )
        if not result.get("success"):
            return {"error": result.get("error", "报告修订失败，请重试")}
        return {"draft_id": draft_id, "content": result["report"],
                "feedback": feedback.strip()}

    def revise_draft(self, db: Session, computed: Dict, revised_by: str = "") -> Dict:
        """把算好的修订版追加进版本链。写入口，状态在这里重查一次。"""
        require_unit(db)
        latest = self._row(db, (computed or {}).get("draft_id") or "")
        if not latest:
            return {"error": _MISSING_DRAFT}
        if latest.status != STATUS_AWAITING:
            return _revision_blocked(latest.status)

        row = _Draft(
            draft_id=latest.draft_id, student_id=latest.student_id,
            version=latest.version + 1, status=STATUS_AWAITING,
            content=computed["content"], feedback=computed["feedback"],
            created_by=revised_by or latest.created_by or "",
        )
        db.add(row)
        db.flush()
        return _payload(row)

    # ── 三值决定 ──────────────────────────────────────────────

    def record_decision(self, db: Session, draft_id: str, action: str,
                        actor_id: str = "", feedback: str = "",
                        computed: Optional[Dict] = None) -> Dict:
        """三值决定。写入口；打回必带 `compute_revision` 算好的 `computed`。"""
        require_unit(db)
        if action and action.strip().lower() == "approve" and not (actor_id or "").strip():
            return {"error": "缺少教师身份，无法记录批准（fail-closed）"}
        latest = self._row(db, draft_id)
        if not latest:
            return {"error": "该草稿不存在或已被处理，请刷新后重试；若反复出现请重新生成一份"}
        if latest.status != STATUS_AWAITING:
            return {"error": ("该学习报告已确认发送给家长，无需重复处理" if latest.status == "sent" else f"该报告当前状态（{latest.status}）不支持此操作，请刷新后查看最新状态")}
        action = (action or "").strip().lower()
        if action == "approve":
            latest.teacher_approved = True
            latest.approved_by = actor_id or ""
            db.flush()
            return _payload(latest)
        if action == "reject":
            if not (feedback or "").strip():
                return {"error": "打回必须附修改意见"}
            if not computed:
                return {"error": "打回的修订版必须先在单元外算好（compute_revision）再带进来"}
            return self.revise_draft(db, computed, actor_id)
        if action == "abandon":
            return _payload(latest)
        return {"error": f"未知的确认门决定: {action}"}

    # ── 发送（门由数据强制；elevation 布尔在该路径退役）──────────

    def send_draft(self, db: Session, student_id: str, draft_id: str = "",
                   sender_id: str = "") -> Dict:
        """写入口：只 flush，须在最外层调用方的工作单元内（前置一）。"""
        require_unit(db)
        from app.services.plan_draft_service import PlanDraftService
        # 有身份即早查 Data Scope；缺身份的强制检查放在批准之后
        if (sender_id or "").strip() and not PlanDraftService.teacher_teaches_student(
                db, sender_id, student_id):
            return {"error": f"你没有向学生 {student_id} 的家长发送报告的权限（Data Scope 不含该生）"}

        payload = self.get_draft(db, draft_id) if draft_id else self.get_active_draft(db, student_id)
        if not payload:
            return {
                "approval_needed": True,
                "message": f"即将把学习报告同步给 {student_id} 的家长，但没有待确认的报告草稿（请先生成）",
                "target": student_id,
                "action": "send_parent",
                "student_id": student_id,
            }
        if payload["status"] != STATUS_AWAITING or not payload["teacher_approved"]:
            return {
                "approval_needed": True,
                "message": f"学习报告（第 {payload['version']} 版）尚未经老师确认，请先确认再发送",
                "target": student_id,
                "action": "send_parent",
                "draft_id": payload["draft_id"],
                "version": payload["version"],
                "student_id": student_id,
            }
        # 即将不可逆发送（ADR-0009 fail-closed）：缺身份 = 拒绝
        if not (sender_id or "").strip():
            return {"error": "缺少教师身份，无法发送（fail-closed）"}
        if not PlanDraftService.teacher_teaches_student(db, sender_id, student_id):
            return {"error": f"你没有向学生 {student_id} 的家长发送报告的权限（Data Scope 不含该生）"}

        from app.models.database import Parent, StudentParentBinding
        student = db.query(_Student).filter(_Student.student_id == student_id).first()
        if not student:
            return {"error": f"学生 {student_id} 不存在"}
        bindings = db.query(StudentParentBinding).filter(
            StudentParentBinding.student_id == student_id,
            StudentParentBinding.status == "active",
        ).all()
        if not bindings:
            return {"error": "该学生未绑定家长，无法发送"}

        report = payload["content"]
        report_text = json.dumps(report, ensure_ascii=False)
        sent, already = [], []
        for b in bindings:
            parent = db.query(Parent).filter(Parent.parent_id == b.parent_id).first()
            if not parent:
                continue
            nid = _notification_id(student_id, report, b.parent_id)
            exists = db.query(_ParentNotification).filter(
                _ParentNotification.notification_id == nid).first()
            if exists:
                already.append(parent.name)
                continue
            db.add(_ParentNotification(
                notification_id=nid,
                parent_id=b.parent_id, student_id=student_id,
                type="weekly_report",
                title=f"📋 {student.name} 的学习报告",
                content=report_text,
                is_read=False, sent_at=datetime.utcnow(),
            ))
            sent.append(parent.name)

        row = db.query(_Draft).filter(
            _Draft.draft_id == payload["draft_id"],
            _Draft.version == payload["version"],
        ).first()
        row.status = STATUS_SENT
        db.flush()
        parts = []
        if sent:
            parts.append(f"学习报告已确认发送给 {len(sent)} 位家长（{', '.join(sent)}）")
        if already:
            parts.append(f"{len(already)} 位家长已有相同报告，未重复投递（{', '.join(already)}）")
        return {
            "result": "；".join(parts),
            "student_id": student_id,
            "student_name": student.name,
            "draft_id": payload["draft_id"],
            "version": payload["version"],
            "sent_parents": sent,
            "already_delivered": already,
        }


from app.models.database import (  # noqa: E402
    ParentNotification as _ParentNotification,
    ParentReportDraft as _Draft,
    Student as _Student,
)

parent_report_draft_service = ParentReportDraftService()
