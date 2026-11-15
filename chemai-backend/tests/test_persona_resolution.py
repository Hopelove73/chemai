"""persona 解析：漏发 persona 时按角色取，不许静默降级。

2026-10-17 端到端实测（gate-lifecycle 2.11 的真因）：老师那句原话跑到第 4 步，
批准第一扇门之后**整条链路降级成家教** —— `/resume` 的请求体没带 persona，
`ChatRequest.persona` 的默认值 `"tutor"` 顶上，而对 teacher 角色 `"tutor"` 又是
合法 persona（`_PERSONAS_BY_ROLE["teacher"] = ("teacher", "tutor")`），
于是 `_resolve_persona` 里 `requested or allowed[0]` 那行的兜底**永远轮不到**。
后果不是文案差异：`teacher.yaml` 才有的 `assign_adaptive_practice` /
`generate_parent_report` 当场从工具清单里消失（46 件 → 21 件），计划第 5 步
永远做不成，模型只能如实汇报「这两个技能在环境里不存在」。

这里把三件事钉住：缺省按角色取、显式指定仍然算数、越权一律降回角色默认。
"""
from agent.channel.gateway import ChatRequest
from agent.channel.langgraph_channel import _resolve_persona


def _req(**kw):
    kw.setdefault("message", "hi")
    return ChatRequest(**kw)


def test_missing_persona_is_none_not_tutor():
    """缺省必须是「没指定」——这是上面那串连锁的第一环。

    若这里回到某个具体 persona，"漏发" 与 "显式指定" 就再也分不开了。
    """
    assert _req().persona is None, "缺省又变回具体 persona 了 —— 静默降级会复发"


def test_teacher_without_persona_gets_teacher():
    """老师漏发 persona → teacher（不是 tutor）。工具白名单挂在这上面。"""
    assert _resolve_persona(_req(), {"role": "teacher"}) == "teacher"


def test_teacher_explicit_tutor_is_still_honored():
    """显式要 tutor 仍然给 tutor —— 修的是「漏发」，不是砍掉这个 persona。"""
    assert _resolve_persona(_req(persona="tutor"), {"role": "teacher"}) == "tutor"


def test_student_cannot_escalate_to_teacher():
    """学生自报 teacher → 降回 student（越权不得顺着 body 走）。"""
    assert _resolve_persona(_req(persona="teacher"), {"role": "student"}) == "student"


def test_admin_family_without_persona_gets_teacher():
    for role in ("subject_lead", "academic_admin", "admin", "super_admin"):
        assert _resolve_persona(_req(), {"role": role}) == "teacher", role
