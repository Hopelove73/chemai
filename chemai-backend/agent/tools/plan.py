"""
plan_task — 复杂任务拆解（Spec 06：planner 接入 v2 agent）。

agent/planner.py 的 PlanGenerator 原为零引用死代码；本工具把它接入 ReAct 循环：
LLM 判断任务复杂时调用本工具 → 生成 ≤6 步结构化计划（步骤/技能/依赖/描述）→
计划 JSON 经 SSE 适配器转成 plan_summary / plan_progress 事件，驱动前端步骤卡
（前端已有 addPlanCard/updatePlanCard 消费这些事件）。
"""
import json


async def plan_task(goal: str = "") -> str:
    """任务拆解 — 把复杂目标拆成结构化执行步骤计划。

    何时用：用户提出多步骤复杂任务（如「先诊断班级薄弱点，再按薄弱点出题，最后生成复习计划」）
    会发生什么：生成 ≤8 步执行计划（每步含技能/依赖/描述）——生成后你必须立即执行第 1 步，
              并逐步骤调用对应工具执行，直到全部步骤完成后再汇报。
    铁律（2026-08 长任务断链修复 + ADR-0013 确认门）：生成后禁止停下来问参数——
    参数不全时用合理默认值继续；但执行途中弹出的确认门（确认卡）必须停下等老师决定，
    门的结果会自动推进任务。出题类步骤调用 generate_questions 或 show_exam_workbench。
    跨步引用：需要前序步骤的产物时，在 args 里写 ${step_N.field}
              （如 {"keyword": "${step_1.question_ids}"}）——运行时按 depends_on 从已落库
              的步骤产物注入，长对话里那一步的输出早被 trim_context 挤掉也照样取得到；
              不要把早期产物的内容抄进 args 自己搬运（抄的那份会随上下文一起消失）。
    NOT for 简单单步任务 — 闲聊、单次问答、单次搜索不需要计划"""
    if not goal or not goal.strip():
        return json.dumps({"error": "请提供要拆解的任务目标"}, ensure_ascii=False)

    from agent.planner import PlanGenerator
    from agent.provider.deepseek import DeepSeekProvider
    from agent.tools import TOOL_META

    available_skills = sorted(t.__name__ for t in TOOL_META)

    # 目标里的纯数字学号 → 查姓名，给规划器上下文（描述用人名，不再「学生241914」）
    student_context = ""
    try:
        import re
        from app.models.database import get_db
        from sqlalchemy import text as _text
        ids = sorted(set(re.findall(r"(?<!\d)\d{6,8}(?!\d)", goal)))[:5]
        if ids:
            # ids 来自 \d 正则，纯数字，可安全内插（text() 的 IN 不支持普通绑定）
            conds = " OR ".join(f"student_id = '{i}'" for i in ids)
            session = next(get_db())
            rows = session.execute(
                _text(f"SELECT student_id, name FROM students WHERE {conds}"),
            ).fetchall()
            session.close()
            if rows:
                student_context = "；".join(f"{r[0]} = {r[1]}" for r in rows)
    except Exception:
        student_context = ""

    generator = PlanGenerator(DeepSeekProvider())
    plan = await generator.generate(goal.strip(), available_skills, student_context)

    steps = [
        {
            "step": s.step,
            "skill": s.skill,
            "args": s.args,
            "depends_on": s.depends_on,
            "description": s.description,
            "status": "pending",
        }
        for s in plan.steps
    ]
    return json.dumps({
        "goal": plan.goal or goal.strip(),
        "total_steps": len(steps),
        "steps": steps,
    }, ensure_ascii=False)


async def decide_plan_draft(draft_id: str = "", action: str = "", comment: str = "") -> str:
    """确认或打回老师发来的学习计划（学生本人，ADR-0029 批④）

    何时用：学生说「这个计划可以/确认」「太难了打回/想调整」且上下文有具体计划草稿
    会发生什么：approve=确认并生效到你的学习计划；revise=打回并附意见（老师会
    收到修改建议重新出稿）；直接执行不过门卡（本人表态，ADR-0029 Decision 5）
    下一步：打回后等老师修订再确认；确认后去「学习计划」面板查看生效内容
    NOT for 给自己凭空造计划（那是老师侧生成）、操作别人的草稿（归属校验会拒绝）"""
    import json

    from app.models.database import get_db
    from app.services.plan_draft_service import plan_draft_service
    from agent.tools.context import current_identity

    identity = current_identity()
    if not identity:
        return json.dumps({"error": "没有取得用户身份，请从学习页发起对话"},
                          ensure_ascii=False)
    if (identity.get("role") or "").lower() != "student":
        return json.dumps({"error": "计划确认只面向学生本人"}, ensure_ascii=False)
    sid = identity.get("user_id") or ""
    act = (action or "").strip().lower()
    if act not in ("approve", "revise"):
        return json.dumps({"error": "action 只支持 approve（确认）或 revise（打回）"},
                          ensure_ascii=False)
    if not (draft_id or "").strip():
        return json.dumps({"error": "缺少 draft_id：请说明要确认/打回哪份计划"},
                          ensure_ascii=False)

    from app.services.work_unit import work_unit

    db = next(get_db())
    try:
        draft = plan_draft_service.get_draft(db, draft_id)
        # 归属即边界：非本人草稿与不存在同文案（不泄漏存在性）
        if not draft or draft.get("student_id") != sid:
            return json.dumps({"error": f"找不到你的计划草稿 {draft_id}"},
                              ensure_ascii=False)

        if act == "approve":
            # 批准与生效是一个单元：生效失败 ⇒ 批准一并回滚（与 REST 端点同款编排）
            with work_unit(db):
                out = plan_draft_service.record_decision(
                    db, draft_id=draft_id, action="approve", actor_id=sid)
                if "error" in out:
                    return json.dumps({"error": out["error"]}, ensure_ascii=False)
                sent = plan_draft_service.send_draft(
                    db, student_id=sid, draft_id=draft_id, sender_id=sid)
                if sent.get("approval_needed"):
                    return json.dumps({"error": sent.get("message", "发送被门拒绝")},
                                      ensure_ascii=False)
                if "error" in sent:
                    return json.dumps({"error": sent["error"]}, ensure_ascii=False)
            return json.dumps({
                "text": "已确认，学习计划已生效——去「学习计划」面板查看",
                "action": "approve",
            }, ensure_ascii=False)

        # revise（打回）：意见必填；修订要调模型，先算后写（WAL 单写事务）
        if not (comment or "").strip():
            return json.dumps({"error": "打回请附一句修改意见（比如太难了/想先学氧化还原）"},
                              ensure_ascii=False)
        computed = plan_draft_service.compute_revision(db, draft_id, comment.strip())
        if "error" in computed:
            return json.dumps({"error": computed["error"]}, ensure_ascii=False)
        with work_unit(db):
            out = plan_draft_service.record_decision(
                db, draft_id=draft_id, action="reject", actor_id=sid,
                feedback=comment.strip(), computed=computed)
            if "error" in out:
                return json.dumps({"error": out["error"]}, ensure_ascii=False)
        return json.dumps({
            "text": "已打回并附上你的意见，老师修订后会重新发给你确认",
            "action": "revise",
        }, ensure_ascii=False)
    finally:
        db.close()
