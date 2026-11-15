"""班级通知工具 — agent 侧唯一的通知出口。

此前通知只有 REST 端点（/api/notification/send-to-class），agent 没有对应工具：
老师对话里说「给全班发通知」时 agent 只能嘴上答应、实际没发（2026-10-13 e2e
矩阵实测发现）。本工具把同一套 notification_service.dispatch 包给 agent，
学生与家长两条分发链都走这里，行为与 REST 端点保持一致。"""

import json

from agent.tools.diagnosis import _caller_scope


async def send_class_notification(
    class_id: str = "",
    title: str = "",
    content: str = "",
    notify_parents: bool = True,
) -> str:
    """发送班级通知 — 给全班学生（可选同时发给各生绑定的家长）

    何时用：老师说「发通知」「告知全班」「提醒学生和家长」这类班级级消息，
        或考试/练习发布后需要同步全班时
    会发生什么：按班级名册给每个学生发一条通知（notify_parents 时同时发给
        各生绑定的活跃家长，同一家长只收一份）；写入对应收件箱，家长端
        「消息」Tab 和学生通知列表可见
    下一步：发送后向老师回报成功条数；老师要补发或修改内容时重新调本工具
    NOT for 点对点消息 — 单个学生/家长用 share_artifact 或 learning_plan 的
        发送链路；NOT for 公告到校级 — 走管理端"""
    from app.models.database import get_db, Student, StudentParentBinding
    from app.services.notification_service import notification_service
    from sqlalchemy import text

    if not (class_id or "").strip() or not title.strip() or not content.strip():
        return json.dumps({"error": "class_id / title / content 都是必填",
                           "_route": {"navigate": False}}, ensure_ascii=False)

    db = next(get_db())
    try:
        scope = _caller_scope(db)
        if not scope.user_id:
            return json.dumps({"error": "缺少教师身份，无法发送班级通知",
                               "_route": {"navigate": False}}, ensure_ascii=False)

        # LLM 常传显示名（「2419班」）而非真实 id（class_2419）——按 diagnosis 同款
        # 三级解析兜底：id 精确 → 名称精确 → 名称 LIKE → 「前缀+数字+班」模式
        cid = class_id.strip()
        if not db.execute(text("SELECT 1 FROM classes WHERE class_id = :c"), {"c": cid}).fetchone():
            cn = str.maketrans("一二三四五六七八九十", "1234567890")
            row = db.execute(text("SELECT class_id FROM classes WHERE name = :n"), {"n": cid}).fetchone()
            if not row:
                row = db.execute(text("SELECT class_id FROM classes WHERE name LIKE :n"), {"n": f"%{cid}%"}).fetchone()
            if not row:
                clean = cid.replace("(", "").replace(")", "").replace("（", "").replace("）", "")
                import re
                m = re.match(r"(.+?)([一二三四五六七八九十\d]+)\s*班$", clean)
                if m:
                    row = db.execute(
                        text("SELECT class_id FROM classes WHERE REPLACE(REPLACE(name, '(', ''), ')', '') LIKE :p"),
                        {"p": f"%{m.group(1)}%{m.group(2).translate(cn)}%班%"},
                    ).fetchone()
            if not row:
                names = [r[0] for r in db.execute(text("SELECT name FROM classes ORDER BY name")).fetchall()]
                return json.dumps({"error": f"未找到班级 '{cid}'，可用班级：{'、'.join(names[:9])}",
                                   "_route": {"navigate": False}}, ensure_ascii=False)
            cid = row[0]

        if not scope.allows_class(cid):
            return json.dumps({"error": f"无权给班级 {cid} 发通知（不在你执教的班级内）",
                               "_route": {"navigate": False}}, ensure_ascii=False)

        students = db.query(Student).filter(Student.class_id == cid).all()
        if not students:
            return json.dumps({"error": f"班级 {class_id} 无学生",
                               "_route": {"navigate": False}}, ensure_ascii=False)

        sent_students = 0
        for st in students:
            out = notification_service.dispatch(
                recipient={"role": "student", "id": st.student_id},
                event={"type": "class_notice", "title": title, "content": content},
            )
            sent_students += 0 if out.get("duplicate") else 1

        sent_parents = 0
        if notify_parents:
            seen: set[str] = set()
            for st in students:
                for b in db.query(StudentParentBinding).filter(
                    StudentParentBinding.student_id == st.student_id,
                    StudentParentBinding.status == "active",
                ).all():
                    if b.parent_id in seen:
                        continue
                    seen.add(b.parent_id)
                    out = notification_service.dispatch(
                        recipient={"role": "parent", "id": b.parent_id, "student_id": st.student_id},
                        event={"type": "class_notice", "title": title, "content": content},
                    )
                    sent_parents += 0 if out.get("duplicate") else 1

        return json.dumps({
            "success": True,
            "class_id": cid,
            "sent_students": sent_students,
            "sent_parents": sent_parents,
            "message": f"已通知 {sent_students} 名学生" + (f"、{sent_parents} 位家长" if notify_parents else ""),
        }, ensure_ascii=False)
    finally:
        db.close()
