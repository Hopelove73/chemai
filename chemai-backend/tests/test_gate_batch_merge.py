"""一次批准覆盖这批步骤（P3）：同一 plan_run 内同类同对象的门不再逐次弹卡。

症状：一个计划里连着几步都撞门，老师批准一次任务要连点 N 次确认卡。
判据（全在数据层，模型自述不算数）：
  ① 对象是单个学生/班级（student_id / class_id）；
  ② 动作属确认类（gate_service.BATCH_MERGE_TOOLS）；
  ③ 授权只在**记下它的那个 plan run** 里算数。
一次点一片人（student_ids / class_ids）= 跨对象群发 → 永远逐条批准。
"""
from app.services.gate_service import (
    BATCH_MERGE_TOOLS,
    compute_covered_steps,
    gate_object_key,
    gate_service,
    scope_gate_id,
)

CID = "t-batch-merge"


def _run_with_steps(db, sid, statuses=("completed", "awaiting_confirmation",
                                       "pending", "pending")):
    """起一个 4 步计划：找学生 → 发学习计划 → 发学习计划 → 发家长报告。

    夹具刻意只用**发送**步：生成步（generate_*）不在合并范围里（生成即门是老师
    看过内容的唯一入口，见 gate_service.BATCH_MERGE_TOOLS 的注释），拿生成步搭夹具
    等于测一个不存在的覆盖面。
    """
    from app.services.plan_run_service import plan_run_service
    steps = [
        {"step": 1, "skill": "show_students", "status": statuses[0], "args": {}},
        {"step": 2, "skill": "send_learning_plan", "status": statuses[1],
         "args": {"student_id": sid}},
        {"step": 3, "skill": "send_learning_plan", "status": statuses[2],
         "args": {"student_id": sid}},
        {"step": 4, "skill": "send_report_to_parent", "status": statuses[3],
         "args": {"student_id": sid}},
    ]
    return plan_run_service.start_run(db, CID, "给这个学生准备并同步", steps)


def _awaiting_draft(db, sid, draft_id="d1"):
    """造一行待确认草稿。

    草稿门算不算数由 `pending_gate_criteria` 判定：带 draft_id 的门要求该链
    MAX(version) 那行仍是 awaiting_confirmation，没有草稿行它会被 `open_gate` 收尾时
    当场判成假门。测试里要真的开一扇草稿门，就得先有这行。
    """
    from app.models.database import LearningPlanDraft
    db.add(LearningPlanDraft(draft_id=draft_id, student_id=sid, version=1,
                             status="awaiting_confirmation", content={}))
    db.commit()


def _approve_draft_gate(db, unit, sid, draft_id="d1"):
    """开一扇草稿门（第 2 步）并让老师批准 —— 批准这一步就是 P3 的授权来源。"""
    _awaiting_draft(db, sid, draft_id)
    with unit(db):
        gid = gate_service.open_gate(
            db, CID, kind="draft", tool_name="send_learning_plan",
            kwargs={"student_id": sid, "draft_id": draft_id},
            draft_id=draft_id, version=1, action="send_student",
            message="学习计划等待确认")
    gate_service.bind_step(db, gid, 2)
    with unit(db):
        assert gate_service.claim_gate(db, gid) is True
    return gid


# ── ① 同一 run 内同类同对象：一次批准覆盖整批 ──────────────────────────


def test_one_approval_covers_the_rest_of_the_run(db_session, unit, sample_student):
    from app.services.plan_run_service import compute_tool_transition, plan_run_service
    sid = sample_student.student_id
    _run_with_steps(db_session, sid)
    gid = _approve_draft_gate(db_session, unit, sid)

    # 批准之后：同一学生的同类动作都被这次批准覆盖（换了一份草稿也一样 ——
    # 合并的单位是"这个学生的这类动作"，不是"这一份草稿"）
    scope = gate_service.scope_covers(
        db_session, CID, "send_learning_plan", {"student_id": sid, "draft_id": "d9"})
    assert scope is not None
    assert scope["from_gate"] == gid
    assert scope["steps"] == [2, 3, 4], "本计划里该学生还没执行的确认类步骤都该在覆盖面里"

    # 覆盖到位就意味着后续几步不会再开第二扇门；步进照常推进到终态
    run = plan_run_service.get_active_run(db_session, CID)
    for tool in ("send_learning_plan", "send_learning_plan",
                 "send_report_to_parent"):
        for step_no, status in compute_tool_transition(
                run["steps"], tool, {"message": "已发送"}):
            plan_run_service.mark_step(db_session, CID, step_no, status)
    # 全部步骤到终态 → run 收尾，且从头到尾只有那一扇门
    assert plan_run_service.get_active_run(db_session, CID) is None
    assert gate_service.get_open_gate(db_session, CID) is None


def test_scope_dies_with_the_run(db_session, unit, sample_student):
    """授权绑的是"记下它的那个 run"：计划一收尾，旧授权不许放行新计划的动作。"""
    sid = sample_student.student_id
    from app.services.plan_run_service import plan_run_service
    _run_with_steps(db_session, sid)
    _approve_draft_gate(db_session, unit, sid)
    for step_no in (2, 3, 4):
        plan_run_service.mark_step(db_session, CID, step_no, "completed")
    assert plan_run_service.get_active_run(db_session, CID) is None
    assert gate_service.scope_covers(
        db_session, CID, "send_learning_plan", {"student_id": sid}) is None


# ── ② 跨对象：各自单独批准，合并请求被拒 ────────────────────────────


def test_other_student_is_not_covered(db_session, unit, sample_student):
    sid = sample_student.student_id
    _run_with_steps(db_session, sid)
    _approve_draft_gate(db_session, unit, sid)
    assert gate_service.scope_covers(
        db_session, CID, "send_learning_plan", {"student_id": sid}) is not None
    # 另一个学生：一次批准不是"这个工具全放行"
    assert gate_service.scope_covers(
        db_session, CID, "send_learning_plan", {"student_id": "202699999"}) is None
    # 另一个班级同理
    assert gate_service.scope_covers(
        db_session, CID, "assign_adaptive_practice", {"class_id": "class_other"}) is None


def test_merged_request_for_another_object_is_rejected(db_session, unit, sample_student):
    """拿 A 的批准去执行 B 的门，必须被强绑定校验拒掉（门 A 永不执行门 B）。"""
    sid = sample_student.student_id
    other = "202699999"
    _awaiting_draft(db_session, sid, "dA")
    _awaiting_draft(db_session, other, "dB")
    with unit(db_session):
        gid_a = gate_service.open_gate(
            db_session, CID, kind="draft", tool_name="send_learning_plan",
            kwargs={"student_id": sid, "draft_id": "dA"}, draft_id="dA", version=1,
            action="send_student", message="A 的学习计划")
    with unit(db_session):
        gid_b = gate_service.open_gate(
            db_session, CID, kind="draft", tool_name="send_learning_plan",
            kwargs={"student_id": other, "draft_id": "dB"}, draft_id="dB", version=1,
            action="send_student", message="B 的学习计划")
    # 开门互斥：后开的那扇顶掉前一扇（同一会话只有一扇算数的门）
    open_gate = gate_service.get_open_gate(db_session, CID)
    assert open_gate["gate_id"] == gid_b
    # 用 A 的编号去批：拒（gate_mismatch），并回带当前门让人话对齐
    mismatch = gate_service.check_binding(db_session, CID, gid_a)
    assert mismatch and mismatch["gate_mismatch"] is True
    assert mismatch["open_gate"]["gate_id"] == gid_b
    # 自己的编号才放行
    assert gate_service.check_binding(db_session, CID, gid_b) is None


def test_broadcast_list_never_merges():
    """一次点一片人（群发）= 不属「同一对象」→ 逐条批准。"""
    assert gate_object_key({"student_ids": ["a", "b"]}) == ""
    assert gate_object_key({"class_ids": ["c1", "c2"]}) == ""
    assert gate_object_key({"student_id": "a"}) == "student:a"
    assert gate_object_key({"class_id": "c1"}) == "class:c1"
    assert gate_object_key({"student_name": "张三"}) == ""
    # 覆盖面也不收点一片人的步骤
    steps = [{"step": 2, "skill": "assign_adaptive_practice", "status": "pending",
              "args": {"student_ids": ["a", "b"]}}]
    assert compute_covered_steps(steps, "student:a") == []


# ── ③ 绑定校验与"没计划就不合并" ─────────────────────────────────


def test_binding_still_guards_the_open_gate(db_session, unit, sample_student):
    sid = sample_student.student_id
    gid = _approve_draft_gate(db_session, unit, sid)
    # 门已被批准（executing），此刻没有开着的门 —— 绑定校验不拦
    assert gate_service.check_binding(db_session, CID, gid) is None
    with unit(db_session):
        gid2 = gate_service.open_gate(
            db_session, CID, kind="boolean", tool_name="assign_adaptive_practice",
            kwargs={"student_id": sid}, action="", message="发放自适应练习")
    # 拿草稿门的编号去批布尔门：拒
    assert gate_service.check_binding(db_session, CID, gid)["gate_mismatch"] is True
    assert gate_service.check_binding(db_session, CID, gid2) is None


def test_no_plan_run_means_no_merge(db_session, unit, sample_student):
    """没有活跃计划就没有"这批步骤"，批准照旧只覆盖它自己。"""
    sid = sample_student.student_id
    with unit(db_session):
        gid = gate_service.open_gate(
            db_session, CID, kind="boolean", tool_name="assign_adaptive_practice",
            kwargs={"student_id": sid}, action="", message="发放自适应练习")
    with unit(db_session):
        assert gate_service.claim_gate(db_session, gid) is True
    assert gate_service.scope_covers(
        db_session, CID, "assign_adaptive_practice", {"student_id": sid}) is None


def test_unmergeable_tool_records_nothing(db_session, unit, sample_student):
    """不可逆破坏不参与合并（删题库没有"同一对象"可言）。"""
    sid = sample_student.student_id
    _run_with_steps(db_session, sid)
    with unit(db_session):
        gid = gate_service.open_gate(
            db_session, CID, kind="boolean", tool_name="delete_bank",
            kwargs={"set_id": "set-1"}, action="", message="删除题库")
    with unit(db_session):
        assert gate_service.claim_gate(db_session, gid) is True
    assert "delete_bank" not in BATCH_MERGE_TOOLS
    assert gate_service.scope_covers(
        db_session, CID, "delete_bank", {"set_id": "set-2"}) is None


# ── guard 侧：读入口问的就是实体 ────────────────────────────────


def test_guard_reads_the_scope_entity(db_session, unit, sample_student, patch_get_db):
    """GuardState.gate_scope 必须**问实体**：读不出来时返回空（fail-closed 弹卡）。"""
    from agent.guard import GuardState
    sid = sample_student.student_id
    _run_with_steps(db_session, sid)
    _approve_draft_gate(db_session, unit, sid)

    gs = GuardState()
    gs.conversation_id = CID
    scope = gs.gate_scope("send_learning_plan", {"student_id": sid, "draft_id": "d9"})
    assert scope["covered"] is not None
    assert scope["covers"] == [2, 3, 4]

    # 没会话号（纯内存评测那条路）：什么都不认，照旧弹卡
    assert GuardState().gate_scope(
        "send_learning_plan", {"student_id": sid}) == {"covered": None, "covers": []}


def test_scope_gate_id_is_per_conversation():
    """授权行编号掺会话：两位老师批准同一个学生不能撞同一行。"""
    assert scope_gate_id("t-a", "student:1") != scope_gate_id("t-b", "student:1")
    assert scope_gate_id("t-a", "student:1") == scope_gate_id("t-a", "student:1")


# ── guard 侧的真正放行路径（真跑一次 _make_guarded_tool）────────────────


def _run_async(coro):
    import asyncio
    return asyncio.new_event_loop().run_until_complete(coro)


def test_boolean_gate_tool_runs_when_covered(db_session, unit, sample_student,
                                             patch_get_db):
    """被覆盖时，破坏性工具**直接执行**（不再开第二扇门、不再返回 approval_needed）。"""
    import json

    from agent.guard import GuardState, _make_guarded_tool
    sid = sample_student.student_id
    _run_with_steps(db_session, sid)
    _approve_draft_gate(db_session, unit, sid)

    gs = GuardState()
    gs.conversation_id = CID

    async def assign_adaptive_practice(student_id: str = ""):
        """借真工具名（限额/判据都按名字取），但什么都不做。"""
        return json.dumps({"assigned": student_id}, ensure_ascii=False)
    assign_adaptive_practice.__name__ = "assign_adaptive_practice"

    guarded = _make_guarded_tool(assign_adaptive_practice, gs, requires_approval=True)
    out = json.loads(_run_async(guarded.ainvoke({"student_id": sid})))
    assert out.get("assigned") == sid, out
    assert "approval_needed" not in out, "已经被批准覆盖的工具不该再发卡"
    assert gate_service.get_open_gate(db_session, CID) is None


def test_generated_draft_never_rides_the_batch_authorization(
        db_session, unit, sample_student, patch_get_db, monkeypatch):
    """「发送」被这次批准覆盖时，**新生成**的草稿仍必须自己弹一张卡。

    批量授权覆盖的是"同一份内容再发一次"，不是"还没出生的一份内容"：2026-10 真机
    实测里，刚生成、老师一眼没看过的家长报告被上一张卡的授权顺手发了出去，草稿当场
    消费掉，后面那步「发送家长报告」查不到可发草稿 → 永远停在执行中。
    """
    import json

    from agent.guard import GuardState, _make_guarded_tool
    import agent.langgraph_agent_v2 as _v2

    sid = sample_student.student_id
    _run_with_steps(db_session, sid)
    _approve_draft_gate(db_session, unit, sid)
    _awaiting_draft(db_session, sid, "d9")   # 新草稿同样得是 awaiting 才算数

    sent: list = []

    async def send_learning_plan(student_id: str = "", draft_id: str = ""):
        """假的发送工具：只记录"真被调了一次"。"""
        sent.append((student_id, draft_id))
        return json.dumps({"ok": True, "student_id": student_id,
                           "draft_id": draft_id, "content": "很长的正文"}, ensure_ascii=False)
    monkeypatch.setitem(_v2._TOOL_BY_NAME, "send_learning_plan", send_learning_plan)

    gs = GuardState()
    gs.conversation_id = CID

    async def generate_learning_plan(student_id: str = ""):
        """第二份学习计划的生成（新 draft_id = 另一份草稿）。"""
        return json.dumps({"approval_needed": True, "student_id": student_id,
                           "draft_id": "d9", "version": 1, "action": "send_student",
                           "preview": "预览正文", "message_gate": "计划等待确认"},
                          ensure_ascii=False)
    generate_learning_plan.__name__ = "generate_learning_plan"

    guarded = _make_guarded_tool(generate_learning_plan, gs)
    out = json.loads(_run_async(guarded.ainvoke({"student_id": sid})))

    assert sent == [], "老师没看过的新草稿不许直接发"
    assert out.get("approval_needed") is True, out
    assert out.get("preview") == "预览正文", "生成预览要留着（前端据此内联渲染）"
    og = gate_service.get_open_gate(db_session, CID)
    assert og is not None and og.get("draft_id") == "d9", og


def test_first_card_announces_the_batch(db_session, unit, sample_student,
                                        patch_get_db, monkeypatch):
    """合并省掉的是"点几次卡"，不是"看没看过"：第一张卡上必须写出它连带覆盖哪几步。"""
    import json

    from agent.guard import GuardState, _make_guarded_tool
    import agent.langgraph_agent_v2 as _v2

    sid = sample_student.student_id
    _run_with_steps(db_session, sid)
    _awaiting_draft(db_session, sid, "d2")   # 门要算数，草稿行得在 awaiting

    async def send_learning_plan(student_id: str = "", draft_id: str = ""):
        """假的发送工具（langchain 建工具要有 docstring）。"""
        return json.dumps({"ok": True})
    monkeypatch.setitem(_v2._TOOL_BY_NAME, "send_learning_plan", send_learning_plan)

    gs = GuardState()
    gs.conversation_id = CID

    async def generate_learning_plan(student_id: str = ""):
        """假的生成工具：返回一个待确认草稿（生成即门）。"""
        return json.dumps({"approval_needed": True, "student_id": student_id,
                           "draft_id": "d2", "version": 1, "action": "send_student",
                           "preview": "预览正文",
                           "message_gate": "学习计划等待确认"}, ensure_ascii=False)
    generate_learning_plan.__name__ = "generate_learning_plan"

    guarded = _make_guarded_tool(generate_learning_plan, gs)
    out = json.loads(_run_async(guarded.ainvoke({"student_id": sid})))
    assert out.get("approval_needed") is True, out
    assert "第 2、3、4 步" in out["message_gate"], out["message_gate"]
    # 门行里那句与卡上那句同源（4a 待确认列表读门行）
    og = gate_service.get_open_gate(db_session, CID)
    assert og is not None and "第 2、3、4 步" in og["message"], og
