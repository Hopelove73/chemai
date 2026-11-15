"""学习技能注册表（ADR-0029：agent 统筹学习流程的唯一扩展点）。

与教学技能注册表（ADR-0027）同构：注册表声明 {名称/标题/persona 面/背后
service 调用}，agent 只见 invoke_learning_skill 一个入口工具，分发层不认识
具体技能名——新增学习流程 = 注册一项，agent/TOOLS 数/前端映射零改动。

四技能的参数/返回分支/接棒载荷契约：docs/designs/learning-skills-contract.md
（单一事实源）。persona 面（ADR-0029 审查勘误）：会话类（开作答会话）student
专属——家长端无作答页且 ChatPanel 不接导航；读侧 student+parent。

student_id 一律从登录身份解析（ADR-0014：NEVER 信任参数里的 id）：
student=本人；parent=按绑定解析（复用 parent_* 的 _resolve_student）。
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class LearningSkill:
    name: str
    title: str
    # "session" = 开作答会话（student 专属，返回带 _route 接棒载荷）；
    # "read" = 读侧（student+parent）
    kind: str
    params_doc: str


REGISTRY: dict[str, LearningSkill] = {
    s.name: s
    for s in (
        LearningSkill(
            name="review_today",
            title="今日复习",
            kind="session",   # 清卡引导带接棒；status/debrief 分支为纯读
            params_doc='{"action": ""|"status"|"debrief"}（缺省=引导清卡）',
        ),
        LearningSkill(
            name="smart_practice",
            title="智能练习",
            kind="session",   # 开作答会话带接棒；action=debrief 分支为纯读（C4）
            params_doc='{"kps": ["知识点"...], "action": ""|"debrief"}'
                       '（缺省=薄弱前三，最多 8 题；debrief=读上次真实结果）',
        ),
        LearningSkill(
            name="variant_training",
            title="错题变式",
            kind="session",
            params_doc='{"question_id": "错题id", "quantity": 2}（question_id 必填）',
        ),
        LearningSkill(
            name="mastery_report",
            title="掌握度报告",
            kind="read",
            params_doc='{"top_n": 6}（四级计数 + 薄弱前 N）',
        ),
    )
}

# 会话类技能 persona 面（ADR-0029 审查勘误）：家长端无作答页/不接导航。
_SESSION_ROLES = {"student"}
_READ_ROLES = {"student", "parent"}
# review_today 是混合技能：引导清卡分支 student 专属（带接棒），
# status/debrief 分支 parent 可用（纯读）——分支内按 is_parent 裁定。
_SKILL_ROLES: dict[str, set[str]] = {
    "review_today": {"student", "parent"},
    "smart_practice": _SESSION_ROLES,
    "variant_training": _SESSION_ROLES,
    "mastery_report": _READ_ROLES,
}


def _unknown_payload(skill: str) -> dict:
    return {
        "error": f"未知学习技能: {skill!r}",
        "available": sorted(REGISTRY),
        "hint": "；".join(f"{k}（{v.title}）" for k, v in sorted(REGISTRY.items())),
    }


def _resolve_student(db, identity: dict, params: dict) -> tuple[str, str, dict | None]:
    """登录身份 → (student_id, student_name, error_payload)。

    student=本人；parent=绑定内解析（params.student_name，单孩可缺省）；
    其他角色拒绝（教师走自己的生成/派发工具链，ADR-0029）。"""
    from agent.tools.parent import _resolve_student

    role = (identity.get("role") or "").lower()
    user_id = identity.get("user_id") or ""
    if role == "student":
        return user_id, "", None
    if role == "parent":
        sid, name, err = _resolve_student(db, user_id, params.get("student_name") or "")
        if err:
            return "", "", {"error": err}
        return sid, name, None
    return "", "", {"error": "学习技能只面向学生与家长 persona（教师用生成/派发工具链）"}


def _skill_review_today(db, sid: str, params: dict, is_parent: bool) -> dict:
    from app.services.learning_skills.recent import review_activity
    from app.services.memory_card_service import due_count
    from app.services.streak_service import streak_view

    action = (params.get("action") or "").strip().lower()
    due = due_count(db, sid)
    streak = streak_view(db, sid)
    # 今天的复习提交记录：debrief 的"清完了"不再只靠学生说（C4）——due==0 有两种
    # 真相（真清完了 / 本来就没到期卡），只有这张表能分辨。
    activity = review_activity(db, sid)

    if action == "debrief":
        cleared = due == 0
        streak_tail = (f"连击 {streak['current']} 天（历史最佳 {streak['best']}），"
                       f"冻结卡 {streak['freeze_count']}/{streak['freeze_max']} 张"
                       + ("，今天已计入连击" if activity["streak_credited_today"]
                          else "，今天还没记上连击"))
        if activity["reviewed"]:
            said = f"对 {activity['correct']} / 错 {activity['wrong']}"
            if activity["forgot"] or activity["vague"] or activity["remembered"]:
                said += (f"；自评 忘了 {activity['forgot']} / 模糊 {activity['vague']}"
                         f" / 记得 {activity['remembered']}")
            tail = (f"到期卡已清空，{streak_tail}" if cleared else
                    f"还剩 {due} 张到期卡没清完——去复习中心继续，清完连击就保住了")
            text = f"今天你实际复习了 {activity['reviewed']} 张（{said}）：{tail}"
        elif cleared:
            text = (f"到期卡现在是 0，但今天还没有复习提交记录（{streak_tail}）"
                    f"——没有记录我不替你确认清完")
        else:
            text = (f"今天还没有复习提交记录，还有 {due} 张到期卡没清（{streak_tail}）"
                    f"——去复习中心清卡")
        return {"text": text, "cleared": cleared, "due_count": due,
                "streak": streak, "activity": activity}

    if action == "status" or is_parent:
        text = (f"今日到期复习 {due} 张，连击 {streak['current']} 天"
                f"（冻结 {streak['freeze_count']} 张）" if due else
                f"今日复习已清空，连击 {streak['current']} 天")
        out = {"text": text, "due_count": due, "streak": streak,
               "activity": activity}
        if activity["reviewed"]:
            out["text"] += f"；今天已提交 {activity['reviewed']} 张复习"
        if action == "status" or due == 0:
            return out
        # 家长 + 有到期卡：给状态不给接棒（家长端无导航，ADR-0029）
        out["text"] += "；可提醒孩子去复习中心清卡"
        return out

    # 学生 + 引导清卡（缺省）：有卡带接棒载荷；无卡不接棒、文案转清空态
    out = {"text": (f"今日到期 {due} 张，连击 {streak['current']} 天、冻结 "
                    f"{streak['freeze_count']} 张——去复习中心清卡吧" if due else
                    f"今日复习已清空，连击 {streak['current']} 天"),
           "due_count": due, "streak": streak}
    if due > 0:
        out["_route"] = {"navigate": True, "page": "/m/review",
                         "state": {"fromChat": "review"}}
    return out


def _practice_debrief(db, sid: str, params: dict) -> dict:
    """上次那轮智能练习的真实结果（C4：学生不说"我做完了"也读得到）。

    只读 ExamRecord.question_stats 的会话态，不重放 finish（那个端点会跑一次障碍
    诊断并可能 commit，读侧不该有副作用）。
    """
    from app.services.learning_skills.recent import latest_practice_session

    res = latest_practice_session(db, sid, (params.get("session_id") or "").strip())
    if res is None:
        return {"text": "库里没有你的智能练习记录——想练就说一声，我按薄弱点开一轮",
                "found": False}

    out = dict(res, found=True)
    answered = res["answered"]
    if not answered:
        out["text"] = (f"上次那轮智能练习（{res['practice_id']}）一题都没作答就离开了，"
                       f"可以接着做")
        return out

    rate = round(res["correct"] / answered * 100)
    start = res["theta_start"]
    theta_text = (f"能力 θ {start:.2f}→{res['theta_end']:.2f}（{res['theta_delta']:+.2f}）"
                  if start is not None else f"能力 θ →{res['theta_end']:.2f}")
    tail = (f"会话已经做满一轮（{res['max_items']} 题上限或误差已达标）" if res["done"]
            else f"还没做完，最多还能答 {res['max_items'] - answered} 题")
    out["text"] = (f"上次那轮智能练习：做了 {answered} 题、答对 {res['correct']} 题"
                   f"（{rate}%），{theta_text}，未标定题 {res['uncalibrated']} 道；{tail}。"
                   f"知识点：{'、'.join(res['kps']) or '未记录'}")
    return out


def _skill_smart_practice(db, sid: str, params: dict) -> dict:
    from app.services import smart_practice_service as sps

    # debrief/status 是纯读分支（同 review_today 的混合技能形态，ADR-0029）：
    # 学生从 /m/practice 回来时读库里那轮会话，不等他口述结果。
    if (params.get("action") or "").strip().lower() in ("debrief", "status"):
        return _practice_debrief(db, sid, params)

    kps = params.get("kps") or None
    try:
        res = sps.start_session(db, sid, kps)
    except sps.SmartSessionError as e:
        return {"error": str(e)}
    shown = "、".join(kps) if kps else "你的薄弱点"
    # 接棒载荷带整会话（首题已在）：页面续接 agent 建的会话直接进作答，
    # 不再自调 /smart/start（否则留孤儿会话）——与 variant_training 同款
    return {
        "text": f"已开一轮智能练习（知识点：{shown}，最多 8 题），去练习页开始作答",
        "session_id": res.get("practice_id", ""),
        "first_question": res.get("question"),
        "theta": res.get("theta"),
        "_route": {"navigate": True, "page": "/m/practice",
                   "state": {"smartSession": {
                       "practice_id": res.get("practice_id", ""),
                       "question": res.get("question"),
                       "theta": res.get("theta"),
                   }}},
    }


def _skill_variant_training(db, sid: str, params: dict) -> dict:
    from app.models.database import StudentAnswer
    from app.services.grading_center import effective_conditions
    from app.services.wrong_question_trainer import (
        create_training_session, generate_variant_questions,
    )

    qid = (params.get("question_id") or "").strip()
    if not qid:
        return {"error": "缺少 question_id：请指明要对哪道错题做变式（可先查错题本）"}
    try:
        quantity = max(1, min(5, int(params.get("quantity") or 2)))
    except (TypeError, ValueError):
        quantity = 2

    # 错题归属（与 /variant/generate 同口径：本人错题册 + 生效作答条件）
    owns = db.query(StudentAnswer.question_id).filter(
        effective_conditions(),
        StudentAnswer.student_id == sid,
        StudentAnswer.question_id == qid,
    ).first()
    if not owns:
        return {"error": "只能对自己的错题生成变式（错题册里没找到这道题）"}

    # LLM 同步调用：与 /variant/generate 端点同款（wrong_topic.py 同步直调）
    variants = generate_variant_questions(qid, quantity)
    if not variants:
        return {"error": "生成变式题失败，请稍后再试"}

    created = create_training_session(
        sid, [v["question_id"] for v in variants])
    summary = (variants[0].get("content") or "")[:20]
    return {
        "text": f"已基于「{summary}…」生成 {len(variants)} 道变式，去错题本作答",
        "session_id": created["session_id"],
        "questions": created["questions"],
        "_route": {"navigate": True, "page": "/m/wrong",
                   "state": {"training": created}},
    }


def _skill_mastery_report(db, sid: str, params: dict) -> dict:
    from app.services.mastery_service import (
        course_mastery_percent, mastery_for_student, mastery_level,
    )

    try:
        top_n = max(1, min(10, int(params.get("top_n") or 6)))
    except (TypeError, ValueError):
        top_n = 6
    ms = mastery_for_student(db, sid)
    if not ms:
        return {"text": "还没有掌握度数据：先做几道题，我再帮你分析"}
    levels = [mastery_level(float(m.get("probability") or 0)) for m in ms]
    counts = {"mastered": levels.count("mastered"), "proficient": levels.count("proficient"),
              "familiar": levels.count("familiar"), "attempted": levels.count("attempted")}
    pct = course_mastery_percent(levels)
    weak = ms[:top_n]
    weak_text = "、".join(
        f"{w.get('knowledge_point')}({int(float(w.get('probability') or 0) * 100)}%)"
        for w in weak) or "暂无"
    text = (f"课程掌握 {pct}%：精通{counts['mastered']}/熟练{counts['proficient']}"
            f"/熟悉{counts['familiar']}/摸索{counts['attempted']}；"
            f"薄弱前 {len(weak)}：{weak_text}")
    return {"text": text, "course_percent": pct, "levels": counts,
            "weak": [{"knowledge_point": w.get("knowledge_point"),
                      "probability": w.get("probability")} for w in weak]}


_SESSION_IMPL = {
    "review_today": _skill_review_today,
    "smart_practice": _skill_smart_practice,
    "variant_training": _skill_variant_training,
}
_READ_IMPL = {
    "mastery_report": _skill_mastery_report,
}


def dispatch(db, skill: str, params: dict, identity: dict) -> dict:
    """入口分发：persona 面校验 → 身份解析 → 技能实现。返回契约见契约页。"""
    s = REGISTRY.get(skill or "")
    if s is None:
        return _unknown_payload(skill)

    role = (identity.get("role") or "").lower()
    allowed = _SKILL_ROLES.get(skill, _READ_ROLES)
    if role not in allowed:
        return {"error": f"「{s.title}」不面向当前角色（{role or '未知'}）"}

    sid, _name, err = _resolve_student(db, identity, params)
    if err:
        return err

    impl = _SESSION_IMPL.get(skill) or _READ_IMPL.get(skill)
    if skill == "review_today":
        return impl(db, sid, params, is_parent=(role == "parent"))
    return impl(db, sid, params)


def skill_menu() -> str:
    """「名（中文标题）」清单——工具描述与错误 hint 共用，格式不漂移。"""
    return "；".join(f"{k}（{v.title}，{v.kind}）" for k, v in sorted(REGISTRY.items()))


