"""批次 1（openspec/changes/gate-lifecycle）— 门与计划步骤的同源绑定。

**夹具按 2026-10-16 端到端实测的形状**：LLM 生成的 7 步计划里**没有**
`send_learning_plan` 步。既有 `tests/test_phase1_step_ownership.py` 的 STEPS 夹具
恰好**有**那一步（step 3）——即 `_GATE_STEP_SKILL` 映射表假设的步——所以那组测试是绿的，
却照不到真实计划形状下的洞。

实测证据链（回放 compute_tool_transition，终态与线上一字不差）见
`openspec/changes/gate-lifecycle/tasks.md` 的 0.2。
"""
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.models.database as db_mod
from app.models.database import Base
from app.services.plan_run_service import (compute_replay_transition,
                                               compute_tool_transition)

# ── 夹具：实测形状（7 步，无 send_learning_plan）────────────────────

REAL_PLAN = [
    {"step": 1, "skill": "show_students", "description": "找学生", "status": "pending"},
    {"step": 2, "skill": "memory_student_get", "description": "读学情", "status": "pending"},
    {"step": 3, "skill": "diagnose_barrier", "description": "诊断", "status": "pending"},
    {"step": 4, "skill": "generate_learning_plan", "description": "出方案", "status": "pending"},
    {"step": 5, "skill": "assign_adaptive_practice", "description": "布置练习", "status": "pending"},
    {"step": 6, "skill": "generate_parent_report", "description": "生成家长报告", "status": "pending"},
    {"step": 7, "skill": "send_report_to_parent", "description": "同步家长", "status": "pending"},
]

# generate_learning_plan 的结果：草稿已落库、门在此刻开
GATE_OPENED = {"message": "已生成", "approval_needed": True, "draft_id": "d1",
               "action": "send_student"}
# assign_adaptive_practice 被**已开的门**拦下的结果
GATE_OPEN_BLOCKED = {"gate_open_blocked": True, "error": "waiting_for_gate"}


def _before_gate_opened():
    """回放到门开**之前**那一刻：step1-3 走完，step4（生成步）running。"""
    steps = [dict(s) for s in REAL_PLAN]
    compute_tool_transition(steps, "show_students", {"students": ["陈湘旻"]})
    compute_tool_transition(steps, "memory_student_get", {"profile": {}})
    compute_tool_transition(steps, "diagnose_barrier", {"barriers": []})
    return steps


def _up_to_gate_opened():
    """回放到「门刚开」那一刻：step1-3 走完，step4 生成完，门开。"""
    steps = _before_gate_opened()
    compute_tool_transition(steps, "generate_learning_plan", GATE_OPENED)
    return steps


def _statuses(steps):
    return {s["skill"]: s["status"] for s in steps}


# ── 1.1 门不得挂到无关的步上 ────────────────────────────────────────

def test_1_1_gate_never_binds_to_an_unrelated_step():
    """开着的门绝不允许把**无关**步骤写成 awaiting_confirmation。

    实测故障：门是 `send_learning_plan` 的，终态里却是 step5
    `assign_adaptive_practice` 挂着「等待确认」。
    """
    steps = _up_to_gate_opened()
    bogus = [s["skill"] for s in steps
             if s["status"] == "awaiting_confirmation"
             and s["skill"] not in ("generate_learning_plan", "send_learning_plan")]
    assert bogus == [], f"无关步骤被写成了等待确认: {bogus}"


# ── 1.5 门必须挂在生成步自己身上（否则 run 永远到不了 completed）─────

def test_1_5_gate_binds_to_the_generator_step_itself():
    """生成即门：门开的瞬间，必须**有**某一步处于 awaiting_confirmation，
    且就是那个生成步——否则门与计划跑了两个真源，run 收不了尾。
    """
    steps = _up_to_gate_opened()
    awaiting = [s["step"] for s in steps if s["status"] == "awaiting_confirmation"]
    assert awaiting == [4], (
        f"门应挂在生成步（step4 generate_learning_plan）自己身上，实际挂在 {awaiting}"
    )


# ── 1.3 被门拦下的工具，不得凭空造出「等待确认」步 ───────────────────

def test_1_3_blocked_tool_must_not_invent_an_awaiting_step():
    """`gate_open_blocked` 的语义是「暂停、等**别的**门」，不是「我在等确认」。

    实测故障：`assign_adaptive_practice` 被已开的门拦下后，它的步骤被写成
    awaiting_confirmation —— 一个**没有被任何门挂住**的等待，永远解不开。
    """
    steps = _up_to_gate_opened()
    transitions = compute_tool_transition(steps, "assign_adaptive_practice",
                                          GATE_OPEN_BLOCKED)
    assert (5, "awaiting_confirmation") not in transitions, (
        "被已开的门拦下的工具，其步骤不得被写成 awaiting_confirmation —— "
        "它没有在等确认，它是被别的门挡住的"
    )
    assert _statuses(steps)["assign_adaptive_practice"] != "awaiting_confirmation"


# ── 1.2 计划里任何时刻只该有一个 running 步 ──────────────────────────

def test_1_2_no_second_running_step():
    """已有 running 步时，另一个工具完成不得再启动新步。

    并发场景：LLM 一轮里并行调了两个工具 —— step1 还在跑，另一个工具的结果先回来，
    它把**自己那一步**打勾（对），又顺手把下一个 pending 启成 running（错）。
    计划里于是有两个 running 步，「当前步」不再有唯一答案 —— 门该挂哪一步也跟着
    变得不确定（断点②「并发推进产生两个 running 步」，是①的助力）。
    """
    steps = [dict(s) for s in REAL_PLAN]
    steps[0]["status"] = "running"           # show_students 还在跑
    transitions = compute_tool_transition(steps, "memory_student_get", {"profile": {}})

    assert not any(s == "running" for _, s in transitions), (
        f"已有 running 步时不得再启动一个：{transitions}"
    )
    running = [s["skill"] for s in steps if s["status"] == "running"]
    assert running == ["show_students"], f"计划里必须恰好一个 running 步：{running}"


# ── 1.8 ⑥ 同源：history 不得下发「没有门撑着的等待确认」步 ──────────────

def test_1_8_history_does_not_report_waiting_step_without_a_gate(monkeypatch):
    """库里没有门，`/chat/history` 就不得还下发一个「等待确认」步。

    断点⑥的用户可见面：计划卡读 `plan_runs.steps`、待确认红点读 `agent_gates`
    —— 两个真源。门被判据过滤掉（或从未真正开成）之后，步骤仍写着「等待确认」，
    于是计划卡永远停在等待态、红点却早已清零，老师看到的是「没有任何东西在等我
    确认，但计划卡不动」。
    """
    from agent.channel.conversation import _active_run_payload
    from app.services.plan_run_service import plan_run_service

    Session = _patch_db(monkeypatch)
    cid = "conv-orphan"
    db = Session()
    try:
        plan_run_service.start_run(db, cid, "老师原话", REAL_PLAN)
        plan_run_service.mark_step(db, cid, 4, "awaiting_confirmation")
        payload = _active_run_payload(db, cid)
    finally:
        db.close()

    assert payload["gate"] is None, "本场景刻意不建任何门"
    waiting = [s["skill"] for s in (payload["run"] or {}).get("steps", [])
               if s.get("status") == "awaiting_confirmation"]
    assert waiting == [], f"没有门撑着的等待确认步不得下发：{waiting}"


# ── 2.8 重放的落终态必须进库（不能只发 SSE 事件）─────────────────────

def test_replay_completion_is_persisted(monkeypatch):
    """重放成功后，门绑的那一步必须**落库**成 completed。

    2026-10-17 从影子库挖出来的：`_plan_completion_events` 曾经只 append SSE 事件、
    **从不写库**。后果是内存态 `completed`、前端也收到了进度，但 `plan_runs.steps`
    里那一步还是 `running` —— 刷新页面和端到端断言读的都是库，于是判它没做完。

    它违反的是本模块文件头自己写的原则：「收权：所有调用面只算转移（纯函数），
    投影与持久化走**这一个入口**（`project_transitions`）」—— 2026-09-21 那次架构
    审查专门收敛过，而 `_plan_completion_events` 是唯一没走那个入口的调用面。
    """
    from types import SimpleNamespace

    from agent.channel.langgraph_channel import _plan_completion_events
    from app.services.plan_run_service import plan_run_service

    Session = _patch_db(monkeypatch)
    cid = "conv-replay"
    db = Session()
    try:
        plan_run_service.start_run(db, cid, "老师原话", REAL_PLAN)
        plan_run_service.mark_step(db, cid, 4, "running")   # 批准后、重放前的状态
    finally:
        db.close()

    mem = [dict(s) for s in REAL_PLAN]
    for s in mem[:3]:
        s["status"] = "completed"
    mem[3]["status"] = "running"

    events = _plan_completion_events(
        SimpleNamespace(plan_steps=mem), "send_learning_plan", True, 4, cid)
    assert events, "重放成功应当产出转移事件"

    db = Session()
    try:
        run = plan_run_service.get_active_run(db, cid)
        step4 = next(s for s in run["steps"] if s["step"] == 4)
        assert step4["status"] == "completed", (
            f"重放的落终态没写进库，库里还是 {step4['status']} —— 只发事件不算完成"
        )
    finally:
        db.close()


# ── 门记下它绑的是哪一步（门与计划步同源）────────────────────────────

def _patch_db(monkeypatch):
    """把 `get_db` 换成内存库（照 tests/test_plan_step_advance_guard.py 的写法）。

    `new_work_unit` 是**延迟**取 `app.models.database.get_db` 的
    （work_unit._session 的注释写了这条契约），所以替换属性就能换库。
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    def _fake_get_db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    monkeypatch.setattr(db_mod, "get_db", _fake_get_db)
    return Session


def test_gate_records_the_step_it_is_bound_to(monkeypatch):
    """门开的瞬间，门实体上落下**它绑的步号** —— 决定与重放从此按它走，不再猜工具名。

    这是「重放目标从门实体构造」的落点（gate-lifecycle：把批次 2 的 2.4 提前）。
    原先两个函数各有一套说法：`compute_decision_transition` 认状态、
    `compute_replay_transition` 认 `skill == tool_name` —— 而门的 tool_name
    （`send_learning_plan`）与它实际该绑住的步（`generate_learning_plan`）不同名，
    那一步于是永远停在 running，run 收不了尾。
    """
    from app.services.gate_service import gate_service
    from app.services.plan_run_service import plan_run_service, project_transitions
    from app.services.work_unit import new_work_unit

    Session = _patch_db(monkeypatch)
    cid = "conv-bind"
    db = Session()
    try:
        plan_run_service.start_run(db, cid, "老师原话", REAL_PLAN)
    finally:
        db.close()

    # 门开 —— 真实路径里由 guard 在破坏性工具第一次被拦时登记门实体。
    # 不带 draft_id：带的话 `pending_gate_criteria` 还会要求那个草稿行仍处于
    # awaiting_confirmation（草稿链的 MAX(version) 那一行），本用例只关心门与步的
    # 绑定，不想为此再铺一条草稿链。空 draft_id 是判据显式允许的形状（R23）。
    with new_work_unit() as udb:
        gate_service.open_gate(udb, cid, "draft", "send_learning_plan", {},
                               action="send_student")

    steps = _before_gate_opened()
    transitions = compute_tool_transition(steps, "generate_learning_plan", GATE_OPENED)
    assert transitions == [(4, "awaiting_confirmation")], transitions

    project_transitions(cid, transitions)

    db = Session()
    try:
        og = gate_service.get_open_gate(db, cid)
        assert og is not None, "门应当还开着"
        assert og["plan_step"] == 4, f"门应记下它绑的步号，实际 {og!r}"
    finally:
        db.close()


# ── 重放只推进门绑的那一步（2026-10-17 删掉双写后钉住）────────────────────

def test_replay_completes_both_the_gate_step_and_the_tool_step():
    """一次批准办完两件事：**生成步**（门绑的那一步）与**发送步**（重放真正跑的工具）。

    老师批准的是生成步的产物（生成即门，1.5），但重放跑的是 `send_report_to_parent`
    —— 报告生成了、也发出去了，计划把这两件事写成两步（`PLAN_PROMPT` 明确要求
    「提到同步家长必须包含 `generate_parent_report` **与** `send_report_to_parent` 步骤」），
    两步就该都收。

    **2026-10-17 的经过值得记**：这个"第二步"原来长在 channel 里
    （`_persist_send_completion`），按 `skill == tool_name` 找同名步，**只认
    `running`/`awaiting_confirmation`**。于是：
      · 7 步计划里那一步**碰巧**刚被 `_start_next_step` 启动成 `running` → 匹配上，全绿。
        看着像正常，其实是**巧合**。
      · 8 步计划（同时有 `generate_learning_plan` 与 `send_learning_plan`）里它还是
        `pending` → 匹配不上，那一格永远悬着、run 收不了官。
    当时把它当"多余的双写"删掉，端到端当场红在 `send_report_to_parent` 停在 running
    —— 才看清**它是承重的，错的是那个过滤器**。现在收进转移规则里：
    按 skill 认、`pending` 也算数、并排除已经定位的那一步。
    """
    steps = [dict(s) for s in REAL_PLAN]
    for s in steps:
        s["status"] = ("completed" if s["step"] < 6
                       else "running" if s["step"] == 6 else "pending")

    # 门是 send_report_to_parent，绑在**生成步** 6 上（生成即门，1.5）
    transitions = compute_replay_transition(steps, "send_report_to_parent", True, 6)

    assert transitions == [(6, "completed"), (7, "completed")], transitions
    assert _statuses(steps)["send_report_to_parent"] == "completed", \
        "重放确实跑了这个工具，计划里为它留的那一步也该收"


def test_replay_completes_a_send_step_even_while_it_is_pending():
    """**反向钉子**：发送步还是 `pending` 时也要收 —— 那正是旧规则匹配不上的那一格。

    8 步计划实测的形状：学习方案门绑在生成步 4，而 `send_learning_plan` 是 step6、
    此刻还是 `pending`（计划还没走到它）。旧规则只认 running/awaiting，
    于是这一格**永远悬着**，run 收不了官。
    """
    skills = ["show_students", "memory_student_get", "diagnose_barrier",
              "generate_learning_plan", "assign_adaptive_practice",
              "send_learning_plan", "generate_parent_report", "send_report_to_parent"]
    steps = [{"step": i + 1, "skill": k, "status": "pending"}
             for i, k in enumerate(skills)]
    for s in steps[:4]:
        s["status"] = "completed"
    steps[3]["status"] = "running"          # 生成步 4：门绑的就是它

    transitions = compute_replay_transition(steps, "send_learning_plan", True, 4)

    assert (6, "completed") in transitions, \
        f"发送步还是 pending 时没收掉（transitions={transitions}）—— 那格会永远悬着"
    assert _statuses(steps)["send_learning_plan"] == "completed"


def test_replay_does_not_complete_an_unrelated_same_skill_step():
    """同名 skill 的另一个步不是"配对步"：门绑哪一步只收哪一步。

    计划里可以有两处 `assign_adaptive_practice`（两个学生 / 两批练习）。
    门绑在第 2 步时，第 3 步还没轮到自己，不能跟着被打勾。
    """
    skills = ["show_students", "assign_adaptive_practice", "assign_adaptive_practice"]
    steps = [{"step": i + 1, "skill": k, "status": "pending"}
             for i, k in enumerate(skills)]
    steps[0]["status"] = "completed"
    steps[1]["status"] = "running"          # 门绑的就是这一步

    transitions = compute_replay_transition(steps, "assign_adaptive_practice", True, 2)

    assert transitions == [(2, "completed"), (3, "running")], transitions
    assert steps[2]["status"] == "running", \
        "没跑过的同名步被替它打勾了（应只是轮到它开始跑）"


# ── 门状态投影按门绑的步号走（F5 / R2-F1）────────────────────────────

def test_gate_step_events_uses_bound_step_not_tool_name():
    """真实计划形状：只有生成步（generate_learning_plan），没有同名发送步。

    门绑在第 4 步；放弃/打回必须落到这一步，不能因为 tool_name 是
    `send_learning_plan` 找不到同名步就什么都不做。
    """
    from agent.channel.langgraph_channel import _gate_step_events
    from agent.guard import GuardState

    guard = GuardState()
    guard.plan_steps = [
        {"step": 1, "skill": "show_students", "status": "completed"},
        {"step": 4, "skill": "generate_learning_plan", "status": "awaiting_confirmation"},
        {"step": 5, "skill": "assign_adaptive_practice", "status": "pending"},
    ]

    events = _gate_step_events("", guard, "cancelled", "send_learning_plan", 4)

    assert events and '"current_step": 4' in events[0]
    assert guard.plan_steps[1]["status"] == "cancelled"


def test_gate_step_events_legacy_fallback_skips_terminal_same_skill_steps():
    """plan_step 未记录的存量门：回退按 skill 选，但必须跳过已终态的同名步。

    计划里有两处 `assign_adaptive_practice`，第一处已完成、第二处正在跑；
    回退若先选第一个再判状态，就会返回空、第二处永远收不了尾（R2-F1）。
    """
    from agent.channel.langgraph_channel import _gate_step_events
    from agent.guard import GuardState

    guard = GuardState()
    guard.plan_steps = [
        {"step": 2, "skill": "assign_adaptive_practice", "status": "completed"},
        {"step": 5, "skill": "assign_adaptive_practice", "status": "running"},
    ]

    events = _gate_step_events("", guard, "cancelled", "assign_adaptive_practice", 0)

    assert events and '"current_step": 5' in events[0]
    assert guard.plan_steps[1]["status"] == "cancelled"
    assert guard.plan_steps[0]["status"] == "completed", "已完成的同名步不得被改写"
