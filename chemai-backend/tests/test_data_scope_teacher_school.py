"""data_scope 教师维度：校级管理员必须限本校（grilling 2026-09-22 · 决策 D）。

根因：`check_tool_scope` 的 teacher 分支自算 `_SCHOOL_WIDE`（绕开了它的声明
「范围的计算只有 RequestScope 一个家」），**没有校过滤**——
任何 admin/academic_admin 都能拿任意 teacher_id 访问全校所有教师的数据。

修复：改为委托 `scope.allows_teacher(raw)`。
`allows_teacher` 的契约：本人 → True；校级管理员且查得同校 → True；
其余一律 False（fail-closed）。

本文件的跨校测试在旧代码上是**红的**（旧代码直接 continue，拒绝不到）。
"""
import asyncio
import json

from agent.guard import GuardState, _make_guarded_tool
from agent.tools.context import set_current_identity


def _run(coro):
    async def go():
        return await coro
    return asyncio.run(go())




def _register_tool(fn, scope: dict, monkeypatch):
    """临时登记 TOOL_META 范围声明（check_tool_scope 只认这里）。"""
    from agent.tools import TOOL_META
    monkeypatch.setitem(TOOL_META, fn,
                        {"personas": [], "call_limit": 99, "scope": scope})


def test_school_admin_denied_for_other_school_teacher(db_session, monkeypatch, patch_get_db):
    """academic_admin 访问**外校**教师 → 必须拒绝。

    旧代码（仅查 _SCHOOL_WIDE 不查校）会直接 continue，工具被调用 → 红。
    新代码委托 allows_teacher → 查得外校 → 拒绝 → 绿。
    """
    from app.models.database import School, Teacher
    db_session.add_all([
        School(school_id="sch_A", name="海中"),
        School(school_id="sch_B", name="稳中"),
        Teacher(teacher_id="tch_B", school_id="sch_B", name="他校老师"),
    ])
    db_session.commit()

    captured = {}

    async def fake_tool(teacher_id: str = ""):
        """Fake teacher-scoped tool (外校拒绝用例)."""
        captured["teacher_id"] = teacher_id
        return json.dumps({"ok": True})

    _register_tool(fake_tool, {"teacher_id": "teacher"}, monkeypatch)
    set_current_identity("admin_A", "academic_admin", "sch_A")
    try:
        gs = GuardState()
        guarded = _make_guarded_tool(fake_tool, gs)
        out = _run(guarded.ainvoke({"teacher_id": "tch_B"}))
    finally:
        set_current_identity("")

    assert captured == {}, "工具不得被调用——外校教师必须在接缝处拦住"
    assert json.loads(out).get("scope_denied") is True, \
        f"应返回 scope_denied，实际：{out[:200]}"


def test_school_admin_allowed_for_same_school_teacher(db_session, monkeypatch, patch_get_db):
    """academic_admin 访问**本校**教师 → 放行（不能误封同校范围）。"""
    from app.models.database import School, Teacher
    db_session.add_all([
        School(school_id="sch_A", name="海中"),
        Teacher(teacher_id="tch_A", school_id="sch_A", name="同校老师"),
    ])
    db_session.commit()

    captured = {}

    async def fake_tool(teacher_id: str = ""):
        """Fake teacher-scoped tool (同校放行用例)."""
        captured["teacher_id"] = teacher_id
        return json.dumps({"ok": True})

    _register_tool(fake_tool, {"teacher_id": "teacher"}, monkeypatch)
    set_current_identity("admin_A", "academic_admin", "sch_A")
    try:
        gs = GuardState()
        guarded = _make_guarded_tool(fake_tool, gs)
        out = _run(guarded.ainvoke({"teacher_id": "tch_A"}))
    finally:
        set_current_identity("")

    assert captured.get("teacher_id") == "tch_A", \
        f"同校教师应放行，实际 captured={captured}"
    assert json.loads(out).get("ok") is True


def test_regular_teacher_denied_for_other_teacher(db_session, monkeypatch, patch_get_db):
    """普通教师访问其他教师 → 拒绝（原逻辑已覆盖，这里钉住回归）。"""
    from app.models.database import School, Teacher
    db_session.add_all([
        School(school_id="sch_A", name="海中"),
        Teacher(teacher_id="tch_other", school_id="sch_A", name="同校但不同人"),
    ])
    db_session.commit()

    captured = {}

    async def fake_tool(teacher_id: str = ""):
        """Fake teacher-scoped tool (普通教师拒绝用例)."""
        captured["teacher_id"] = teacher_id
        return json.dumps({"ok": True})

    _register_tool(fake_tool, {"teacher_id": "teacher"}, monkeypatch)
    set_current_identity("tch_self", "teacher", "sch_A")
    try:
        gs = GuardState()
        guarded = _make_guarded_tool(fake_tool, gs)
        out = _run(guarded.ainvoke({"teacher_id": "tch_other"}))
    finally:
        set_current_identity("")

    assert captured == {}, "工具不得被调用——普通教师不能冒充他人"
    assert json.loads(out).get("scope_denied") is True
