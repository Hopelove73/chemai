"""家长端 Agent 工具（批 3 P2 / parent-agent-tools REQ-PAT-001..002）。

5 个读工具 = 既有 service 的薄包装（数据面经绑定收窄）；派任务走生成即门配对：
parent_assign_task（频控 + compute_draft + create_draft，返回 approval_needed）
→ 门批准后确定性重放 parent_send_task（send_draft 落学生计划）。

门隔离（design D4 落地口径）：门实体挂在**家长的会话**上，教师队列
（pending_gates 按会话归属分段）天然看不到、也决定不了；学生两值确认走
Plan Draft REST 的既有 seam，互不混用。
"""
import json


def _bound_student_ids(db, parent_id: str) -> set[str]:
    from app.models.database import StudentParentBinding
    rows = (db.query(StudentParentBinding.student_id)
            .filter(StudentParentBinding.parent_id == parent_id,
                    StudentParentBinding.status == "active").all())
    return {r[0] for r in rows}


def _current_parent_id() -> str:
    from agent.tools.context import current_identity
    return (current_identity().get("user_id") or "")


def _resolve_student(db, parent_id: str, student_ref: str):
    """家长视角解析孩子：绑定内按学号精确 → 按名包含。返回 (sid, name, err)。"""
    from app.models.database import Student
    bound = _bound_student_ids(db, parent_id)
    if not bound:
        return "", "", "没有绑定的孩子，请先在家长端完成绑定"
    rows = db.query(Student).filter(Student.student_id.in_(bound)).all()
    ref = str(student_ref or "").strip()
    if not ref:
        if len(rows) == 1:
            return rows[0].student_id, rows[0].name, ""
        return "", "", ("请说明是哪个孩子：" +
                        "、".join(f"{r.name}({r.student_id})" for r in rows))
    for r in rows:
        if r.student_id == ref:
            return r.student_id, r.name, ""
    for r in rows:
        if ref in (r.name or ""):
            return r.student_id, r.name, ""
    return "", "", f"「{ref}」不是你绑定的孩子"


async def parent_learning_plan(student_name: str = "") -> str:
    """读孩子的当前学习计划（概要 + 今日任务）

    何时用：家长问「孩子的学习计划是什么」「今天该做什么」
    会发生什么：返回绑定孩子的计划概要（标题/周期/今日任务），不含其他学生数据
    下一步：可结合 parent_mastery_levels 谈薄弱点
    NOT for 派新任务 — 用 parent_assign_task"""
    from app.models.database import get_db, Student

    parent_id = _current_parent_id()
    db = next(get_db())
    try:
        sid, name, err = _resolve_student(db, parent_id, student_name)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)
        student = db.get(Student, sid)
        plan = (student.current_plan if student else None) or {}
        tasks = plan.get("daily_tasks") or []
        today_idx = min(len(tasks), 1)
        return json.dumps({
            "student": name,
            "plan_title": plan.get("plan_title", "未设置"),
            "plan_period": plan.get("plan_period", ""),
            "today_task": tasks[today_idx - 1] if tasks else None,
            "total_tasks": len(tasks),
        }, ensure_ascii=False)
    finally:
        db.close()


async def parent_mastery_levels(student_name: str = "") -> str:
    """读孩子的知识点掌握度四级状态（摸索/熟悉/熟练/精通）

    何时用：家长问「孩子学得怎么样」「哪些知识点薄弱」
    会发生什么：返回薄弱在前四级列表（BKT 掌握概率 + 级别），数据与教师端同源
    下一步：薄弱点可用 parent_assign_task 派强化任务
    NOT for 预测考试成绩 — 掌握概率不是分数"""
    from app.models.database import get_db
    from app.services.mastery_service import mastery_for_student, mastery_level

    parent_id = _current_parent_id()
    db = next(get_db())
    try:
        sid, name, err = _resolve_student(db, parent_id, student_name)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)
        points = mastery_for_student(db, sid)
        return json.dumps({
            "student": name,
            "points": [{"knowledge_point": p["knowledge_point"],
                        "mastery": p["mastery"],
                        "level": mastery_level(p["mastery"]),
                        "samples": p["samples"]} for p in points],
        }, ensure_ascii=False)
    finally:
        db.close()


async def parent_barrier_distribution(student_name: str = "") -> str:
    """读孩子的错因分布（概念/审题/表述三类的占比与解读）

    何时用：家长问「孩子为什么错得多」「错在哪类问题上」
    会发生什么：返回三类错因分布 + 每类一句家长话解读
    下一步：表述占比高可建议表述特训；概念占比高可派薄弱点任务
    NOT for 指责式转述 — 数据给方向，不给评价"""
    from app.models.database import Student, get_db

    parent_id = _current_parent_id()
    db = next(get_db())
    try:
        sid, name, err = _resolve_student(db, parent_id, student_name)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)
        student = db.get(Student, sid)
        dist = dict(student.barrier_type) if student and student.barrier_type else {}
        reading = {
            "concept": "概念没吃透——需要回到定义重新理解",
            "reading": "审题漏条件——读题习惯问题，可练圈画关键词",
            "expression": "会做但说不清——表述规范需要特训",
        }
        return json.dumps({
            "student": name,
            "distribution": dist,
            "reading": {k: reading.get(k, "") for k in dist},
        }, ensure_ascii=False)
    finally:
        db.close()


async def parent_class_comparison(student_name: str = "") -> str:
    """读孩子在班级里的相对位置（班级平均正确率对比，不含排名）

    何时用：家长问「孩子在班里什么水平」
    会发生什么：返回孩子正确率与班级平均正确率的对比（同班口径），刻意不排名字
    下一步：落后时优先看 parent_barrier_distribution 找方向
    NOT for 横向比较别的孩子 — 只出平均值，不泄露他人数据"""
    from app.models.database import Student, StudentAnswer, get_db
    from sqlalchemy import func

    parent_id = _current_parent_id()
    db = next(get_db())
    try:
        sid, name, err = _resolve_student(db, parent_id, student_name)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)
        student = db.get(Student, sid)

        def _acc(q):
            total, correct = q
            return round(correct / total * 100) if total else 0

        own = db.query(func.count(StudentAnswer.answer_id),
                       func.sum(func.coalesce(StudentAnswer.is_correct, 0))
                       ).filter(StudentAnswer.student_id == sid).first()
        class_avg = (0, 0)
        if student and student.class_id:
            class_avg = (db.query(func.count(StudentAnswer.answer_id),
                                  func.sum(func.coalesce(StudentAnswer.is_correct, 0)))
                         .join(Student, Student.student_id == StudentAnswer.student_id)
                         .filter(Student.class_id == student.class_id).first())
        return json.dumps({
            "student": name,
            "student_accuracy": _acc(own),
            "class_average_accuracy": _acc(class_avg),
            "note": "班级平均为匿名聚合，不含排名与他人数据",
        }, ensure_ascii=False)
    finally:
        db.close()


async def parent_artifact_log(student_name: str = "") -> str:
    """读孩子收到的学习工件记录（老师分享的动画/课件等）

    何时用：家长问「孩子最近学了什么内容」「看过哪些讲解」
    会发生什么：返回孩子收件箱里的工件分享记录（标题+时间），家长只见事件不见内容
    下一步：可结合学习计划谈学习内容安排
    NOT for 查看工件内容 — 家长端不支持打开工件本体"""
    from app.models.database import StudentNotification, get_db

    parent_id = _current_parent_id()
    db = next(get_db())
    try:
        sid, name, err = _resolve_student(db, parent_id, student_name)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)
        rows = (db.query(StudentNotification)
                .filter(StudentNotification.student_id == sid,
                        StudentNotification.type == "artifact_share")
                .order_by(StudentNotification.sent_at.desc()).limit(10).all())
        return json.dumps({
            "student": name,
            "items": [{"title": r.title,
                       "sent_at": r.sent_at.isoformat() if r.sent_at else None}
                      for r in rows],
        }, ensure_ascii=False)
    finally:
        db.close()


async def parent_assign_task(student_name: str = "", focus: str = "") -> str:
    """给孩子派一个薄弱点强化任务（生成学习计划草稿，弹确认卡等家长决定）

    何时用：家长明确说「给孩子派个任务」「让他练练XX知识点」（明确了孩子与方向）
    会发生什么：频控校验（每周 ≤2 次）→ 按孩子薄弱点生成计划草稿 → 弹确认卡
               （批准/打回/放弃）；批准后任务进入孩子的学习计划
    下一步：告知家长确认卡已弹出，等待其决定；打回可附修改意见
    NOT for 没说孩子名字（先问是哪个孩子）；NOT for 一周第三次（频控会拒绝）"""
    from app.models.database import get_db
    from app.services import parent_task_service as pts

    parent_id = _current_parent_id()
    db = next(get_db())
    try:
        sid, name, err = _resolve_student(db, parent_id, student_name)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)
        ok, used, msg = pts.can_assign(db, sid)
        if not ok:
            return json.dumps({"error": msg, "used": used}, ensure_ascii=False)

        from app.services.plan_draft_service import plan_draft_service
        computed = plan_draft_service.compute_draft(db, student_id=sid)
        if computed.get("error"):
            return json.dumps({"error": computed["error"]}, ensure_ascii=False)
        plan_data = computed.get("plan_data") or {}
        if focus:
            plan_data["parent_focus"] = focus[:200]
        # 写入口与教师 generate_learning_plan 同款：同一条会话上开工作单元。
        # 此前的 _unit()（new_work_unit）开的是另一条会话，require_unit(db)
        # 身份校验必炸 —— 家长派任务门卡从未成功弹出过（E2E E14 实测）
        from app.services.work_unit import work_unit
        with work_unit(db):
            draft = plan_draft_service.create_draft(
                db, computed={**computed, "plan_data": plan_data},
                created_by=f"parent:{parent_id}")
        if draft.get("error"):
            return json.dumps({"error": draft["error"]}, ensure_ascii=False)

        # 生成即门（同 generate_learning_plan 语义）：guard 登记配对的发送调用
        return json.dumps({
            "approval_needed": True,
            "draft_id": draft["draft_id"],
            "student_id": sid,
            "message": f"即将为 {name} 派发强化任务（本周第 {used + 1} 次），等待你的确认",
        }, ensure_ascii=False)
    finally:
        db.close()


async def parent_send_task(student_id: str = "", draft_id: str = "") -> str:
    """发送家长确认过的任务计划给孩子（门批准后的确定性重放调用，不要主动调用）

    何时用：确认卡批准后由系统自动重放，家长无需也无法直接调用
    会发生什么：草稿落入孩子的学习计划，孩子端可见
    NOT for 主动调用 — 未确认的草稿会被门拒绝"""
    from app.services import parent_task_service as pts
    from app.services.plan_draft_service import plan_draft_service

    parent_id = _current_parent_id()
    if not student_id or not draft_id:
        return json.dumps({"error": "缺少 draft_id 或 student_id"}, ensure_ascii=False)
    # 门批准的确定性重放：自带会话开单元（send_draft 里有 require_unit 闸）
    from app.services.work_unit import new_work_unit
    with new_work_unit() as db:
        if not pts.parent_of_student(db, parent_id, student_id):
            return json.dumps({"error": "未绑定该学生"}, ensure_ascii=False)
        # sender_id=parent:x：send_draft 角色感知按前缀走亲子绑定校验 ——
        # 传裸 parent_id 会误入教师分支被 Data Scope 拒（E2E E14 实测）
        out = plan_draft_service.send_draft(
            db, student_id=student_id, draft_id=draft_id,
            sender_id=f"parent:{parent_id}")
    if out.get("approval_needed"):
        return json.dumps({**out,
                           "message": "任务计划尚未确认"}, ensure_ascii=False)
    return json.dumps({"message": "任务已发给孩子，可在其学习计划中查看", **out},
                      ensure_ascii=False)




async def parent_weekly_summary(student_name: str = "") -> str:
    """看孩子本周学情三卡：未完成 / 即将到期 / 本周动态，附消灭率与一句话解读

    何时用：家长问「孩子这周怎么样」「本周学习情况」「有什么要注意的」
    会发生什么：汇总未完成任务数、到期项、本周正确率与连击，附薄弱消灭率
    和一句话人话解读 + 「现在能帮上的一件事」建议
    下一步：未完成多可用 parent_assign_task 派强化任务；想看掌握度明细用
    parent_mastery_levels；想让孩子自己练用学习技能（smart_practice）
    NOT for 查分数明细 — 三卡刻意不放分数（Google Classroom 三段式）"""
    from app.models.database import get_db
    from app.services.parent_summary_service import (
        build_cards, compose_summary_text,
    )
    from app.services.mastery_service import mastery_for_student

    parent_id = _current_parent_id()
    db = next(get_db())
    try:
        sid, name, err = _resolve_student(db, parent_id, student_name)
        if err:
            return json.dumps({"error": err}, ensure_ascii=False)
        cards = build_cards(db, sid) or {}
        # 消灭率口径与 analytics.py elimination 端点同源（BKT≥0.9 计达标）
        ELIMINATED_AT = 0.9
        points = mastery_for_student(db, sid)
        elim = {"total": len(points),
                "eliminated": sum(1 for p in points
                                  if float(p["mastery"]) >= ELIMINATED_AT)}
        _title, lines = compose_summary_text(db, sid)
        ua = cards.get("week_activity") or {}
        undone = cards.get("unfinished") or {}
        delta = int(ua.get("accuracy_delta") or 0)
        text = (f"{name}本周：未完成 {undone.get('count', 0)} 项、"
                f"正确率 {int(ua.get('accuracy') or 0)}%（环比 {delta:+d}%）、"
                f"连击 {int(ua.get('streak_days') or 0)} 天；"
                f"薄弱消灭 {elim['eliminated']}/{elim['total']}。"
                + ("现在能帮上的一件事：提醒孩子先清掉未完成的练习任务。"
                   if undone.get("count") else "节奏不错，保持就好。"))
        return json.dumps({
            "student": name, "text": text, "cards": cards,
            "elimination": elim, "summary_lines": lines,
        }, ensure_ascii=False)
    finally:
        db.close()
