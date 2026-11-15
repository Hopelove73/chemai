"""学习技能入口工具（ADR-0029：agent 唯一能看到的学习流程接口）。"""
import json


async def invoke_learning_skill(skill: str = "", params_json: str = "",
                                student_name: str = "") -> str:
    """开启/查询学生的学习流程（复习、智能练习、错题变式、掌握度报告）

    何时用：学生说「我该复习啥」「来一轮智能练习」「出变式」「我掌握得怎么样」，
    或家长问学习状态/掌握度；讲解后要给学生变式练习时（会话接力，ADR-0029）
    会发生什么：按技能开启会话或读取数据——开作答会话会返回接棒跳页（学生去
    专用组件作答，回来时你用 debrief 读真实结果点评）
    下一步：作答点评一律用 action="debrief"（复习 review_today、练习 smart_practice
    都行）——它直接反查库里的作答/复习记录（做了几题、对几题、会话做满没、连击今天
    记上没），**学生不说"我做完了"也读得到**；他下次回来只要提到练习或复习，先 debrief
    再说话，别让他复述结果；
    skill 参数必须传以下精确名（不要自造缩写），params_json 按对应 schema：
    NOT for 教师布置练习（用 assign_adaptive_practice）、内容生产（用
    invoke_teaching_skill）、裸出题（无真实判卷与记忆调度，禁止）
    """
    from app.models.database import get_db

    from app.services.learning_skills import REGISTRY, dispatch, skill_menu

    if (skill or "") not in REGISTRY:
        return json.dumps({
            "error": f"未知学习技能: {skill!r}",
            "available": sorted(REGISTRY),
            "hint": f"可用技能：{skill_menu()}",
        }, ensure_ascii=False)

    try:
        params = json.loads(params_json) if (params_json or "").strip() else {}
    except ValueError:
        return json.dumps({"error": "params_json 不是合法 JSON（示例："
                          + skill_menu() + "）"}, ensure_ascii=False)
    if not isinstance(params, dict):
        return json.dumps({"error": "params_json 必须是 JSON 对象"}, ensure_ascii=False)
    if (student_name or "").strip():
        params.setdefault("student_name", student_name.strip())

    from agent.tools.context import current_identity
    identity = current_identity()
    if not identity:
        return json.dumps({"error": "没有取得用户身份，请从学习页发起对话"},
                          ensure_ascii=False)

    db = next(get_db())
    try:
        out = dispatch(db, skill, params, identity)
    finally:
        db.close()
    return json.dumps(out, ensure_ascii=False)


def _append_skill_names_to_doc() -> None:
    """注册表技能名拼进 docstring（= 模型看到的工具描述），注册即生效
    （ADR-0027 同款机制，teaching.py 先例）。"""
    from app.services.learning_skills import REGISTRY, skill_menu

    lines = "；".join(
        f"{k}（{v.title}，参数 {v.params_doc}）" for k, v in sorted(REGISTRY.items()))
    invoke_learning_skill.__doc__ = (
        (invoke_learning_skill.__doc__ or "").rstrip()
        + f"\n\nskill 参数必须传以下精确名：{lines}。params_json 为对应 JSON；"
          f"家长代查时把 student_name 传顶层参数。\n当前注册：{len(REGISTRY)} 项。"
    )


_append_skill_names_to_doc()
