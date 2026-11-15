"""批次 2（openspec/changes/gate-lifecycle）— 门的生命周期：孤儿门必须有人收。

2026-10-16 端到端实测的死锁（`evals/e2e_long_chain.py`）：

    ① 聊天内联卡 REST 批准 → 草稿发出去了，门实体还是 `open`（断点③：
       `app/api/diagnosis.py` 的 REST 路径全程不碰 `gate_service`）
    ② 老师再走 `/resume` → 「已确认发送，无需重复处理」→ 早退，门还是 `open`（断点④）
    ③ 下一个请求进来 → `guard` 拿它当「有一张确认卡待处理」→ 拒掉 agent 的**一切**
       工具调用 → 计划卡永远停在等待态、run 永远 `running`

关键事实：**机制早就有，缺的是有人去跑。**
判据是 `pending_gate_criteria`，收敛器是 `supersede_stale_gates`
（`gate_service` 自称「唯一的假门写库点」）。
`tests/test_pending_gate_predicate.py::test_direct_send_leaves_a_gate_the_convergence_cleans`
甚至早就写着「但库里还是 open —— 这正是需要收敛主人的原因」，只是那条用例**手动**
调了一次收敛器，产品代码里没人调。

所以这批不是发明新机制，是**把收敛器接到「情况变了」的那一刻**。

夹具沿用 `tests/test_gate_decision_loop.py` 的 `env`（内存库 + 学生 + 授课关系 +
假教师身份 + 假模型）。
"""
import json

import pytest

from app.models.database import AgentGate, Student, TeacherClassSubject
from app.services.gate_service import gate_service
from app.services.plan_draft_service import plan_draft_service as draft_svc
from app.services.work_unit import work_unit

PLAN = {"plan_title": "张三计划", "weekly_goals": ["盐类水解"]}


@pytest.fixture
def env(db_session, monkeypatch):
    """内存库 + get_db 重定向 + 假教师身份 + 假模型（同 test_gate_decision_loop）。"""
    db_session.add(Student(student_id="s1", name="张三", class_id="c1", status="approved"))
    db_session.add(TeacherClassSubject(id=1, teacher_id="t1", class_id="c1",
                                       subject="chemistry"))
    db_session.commit()

    import app.models.database as dbmod

    def _fake_get_db():
        yield db_session

    monkeypatch.setattr(dbmod, "get_db", _fake_get_db)
    monkeypatch.setattr("agent.tools.context.current_teacher", lambda: "t1")

    import sys
    mod = sys.modules["app.services.llm_service"]
    monkeypatch.setattr(mod.llm_service, "generate_learning_plan",
                        lambda **kw: {"success": True,
                                      "content": json.dumps(PLAN, ensure_ascii=False)})
    return db_session


def _make_draft(env, student_id="s1"):
    """造一份待确认草稿：单元外算、单元内写（前置一）。"""
    computed = draft_svc.compute_draft(env, student_id)
    with work_unit(env):
        return draft_svc.create_draft(env, computed, created_by="t1")


def _open_gate(env, cid, draft_id):
    """直接造一扇门实体（等价 guard 拦下发送工具时的登记）。"""
    with work_unit(env):
        return gate_service.open_gate(
            env, cid, kind="draft", tool_name="send_learning_plan",
            kwargs={"student_id": "s1", "draft_id": draft_id}, draft_id=draft_id,
            version=1, action="send_student", message="学习计划（第 1 版）等待确认")


def _gate_row(env, gate_id):
    return env.query(AgentGate).filter(AgentGate.gate_id == gate_id).first()


# ── 2.2 执法点下沉：发送落定 = 门当场收敛 ────────────────────────────

def test_send_draft_converges_the_gate_it_orphaned(env):
    """`send_draft` 真的把草稿发出去了，那扇门就不能再留着 `open`。

    ⚠️ 断言必须看**门行本身**，不能看 `get_open_gate`：判据（`pending_gate_criteria`）
    对「草稿已 sent」的门本来就不露出，所以 `get_open_gate` 在修复前就是 None ——
    拿它当断言等于什么都没测。`tests/test_pending_gate_predicate.py` 里那句
    「页面不露出，但库里还是 open」说的就是这个。
    """
    draft = _make_draft(env)
    did = draft["draft_id"]
    with work_unit(env):
        draft_svc.record_decision(env, did, "approve", "t1")
    gid = _open_gate(env, "conv-orphan", did)

    assert _gate_row(env, gid).status == "open", "前置：门开着"

    with work_unit(env):
        out = draft_svc.send_draft(env, "s1", did, "t1")
    assert "error" not in out, out

    assert _gate_row(env, gid).status != "open", (
        "草稿已发送，门行却还留在 open —— 收敛器没人跑。guard 会拿这一行当"
        "「有一张确认卡待处理」，拒掉 agent 的一切工具调用（端到端实测的死锁）"
    )


# ── 2.7 门三态：占住 ≠ 执行完了 ────────────────────────────────────────

def test_claim_moves_gate_to_executing_not_executed(env):
    """占到门只表示「这活儿归我了」，不表示「做完了」——两者原来是一个状态。"""
    draft = _make_draft(env)
    gid = _open_gate(env, "conv-3state", draft["draft_id"])

    with work_unit(env):
        assert gate_service.claim_gate(env, gid) is True

    assert _gate_row(env, gid).status == "executing", \
        "占住应当是「执行中/结果未知」，不是「已执行」"


def test_failed_replay_returns_the_gate_so_the_teacher_can_retry(env):
    """执行失败 ⇒ 门退回 `open`，老师能重试。

    `claim_gate` 在执行**之前**占门（防同一扇门批两次，是对的），代价是动作失败时
    门已被消费。没有"退回"这一步，「执行失败」就等于「这件事永远做不成了」——
    老师拿着那张卡点什么都只会得到「已经处理过了」，而动作其实一次都没跑成。
    """
    draft = _make_draft(env)
    gid = _open_gate(env, "conv-retry", draft["draft_id"])

    with work_unit(env):
        gate_service.claim_gate(env, gid)
        gate_service.finish_gate(env, gid, ok=False)

    assert _gate_row(env, gid).status == "open", "失败必须退回可重试的 open"


def test_successful_replay_marks_the_gate_executed(env):
    """执行成功 ⇒ 终态 `executed`，这扇门的事办完了。"""
    draft = _make_draft(env)
    gid = _open_gate(env, "conv-done", draft["draft_id"])

    with work_unit(env):
        gate_service.claim_gate(env, gid)
        gate_service.finish_gate(env, gid, ok=True)

    assert _gate_row(env, gid).status == "executed"


def test_second_claim_is_refused_while_executing(env):
    """`executing` 期间第二个人来点——**不许再执行一遍**（这正是三态要挡的）。

    进程挂在 `executing` 上时，门既不是"还能批"也不是"做完了"。盲目重试就是把
    不可逆动作跑第二遍；2.7 的答案是不许自动重试，由老师对着"结果未知"卡定。
    """
    draft = _make_draft(env)
    gid = _open_gate(env, "conv-indoubt", draft["draft_id"])

    with work_unit(env):
        assert gate_service.claim_gate(env, gid) is True
    with work_unit(env):
        assert gate_service.claim_gate(env, gid) is False, \
            "已在 executing 的门不得被再占一次"


# ── 2.7b 启动扫描：熬过进程重启的 executing = 结果未知 ──────────────────

def test_startup_sweep_marks_stuck_executing_as_in_doubt(env):
    """启动时仍停在 `executing` 的门，判成 `in_doubt`（结果未知，等老师裁定）。

    判据是**进程边界**，不是超时：`claim_gate` 与 `finish_gate` 之间永远发生在同一次
    请求、同一个进程里（`_direct_replay_events` 一个 async 函数走完）。所以任何熬过
    一次进程重启的 `executing`，必然是上次崩溃留下的——执行它的那个进程已经不在了，
    结果不可能再有回音。

    绝不能退回 `open`：那等于允许盲目重试，而重试跑的正是那个**不知道跑没跑过**的
    不可逆动作。
    """
    draft = _make_draft(env)
    gid = _open_gate(env, "conv-sweep", draft["draft_id"])
    with work_unit(env):
        gate_service.claim_gate(env, gid)
    assert _gate_row(env, gid).status == "executing", "前置：门停在 executing"

    with work_unit(env):
        swept = gate_service.sweep_stuck_executing(env)

    assert swept == 1, f"该扫到 1 扇，实际 {swept}"
    assert _gate_row(env, gid).status == "in_doubt"
    assert _gate_row(env, gid).status != "open", "退回 open 等于允许盲目重试"


def test_startup_sweep_leaves_finished_gates_alone(env):
    """反向钉子：正常收口的门（executed / 退回的 open）一律不碰。"""
    # 两个草稿：草稿门的 gate_id 由 draft_id 生成，共用一个草稿就是同一行
    done = _open_gate(env, "conv-done-ok", _make_draft(env)["draft_id"])
    retryable = _open_gate(env, "conv-done-fail", _make_draft(env)["draft_id"])
    with work_unit(env):
        gate_service.claim_gate(env, done)
        gate_service.finish_gate(env, done, ok=True)
        gate_service.claim_gate(env, retryable)
        gate_service.finish_gate(env, retryable, ok=False)

    with work_unit(env):
        swept = gate_service.sweep_stuck_executing(env)

    assert swept == 0, f"没有残留的 executing，不该扫到东西，实际 {swept}"
    assert _gate_row(env, done).status == "executed"
    assert _gate_row(env, retryable).status == "open"


# ── 2.7b 裁定：老师对「结果未知」卡的两种选择 ───────────────────────────

def _make_in_doubt(env, cid: str) -> str:
    """造一扇 in-doubt 的门：开门 → 占住 → 进程"重启"（扫描）。"""
    gid = _open_gate(env, cid, _make_draft(env)["draft_id"])
    with work_unit(env):
        gate_service.claim_gate(env, gid)
        gate_service.sweep_stuck_executing(env)
    assert _gate_row(env, gid).status == "in_doubt", "前置：门在 in_doubt"
    return gid


def test_in_doubt_confirmed_as_executed(env):
    """老师对着「结果未知」卡选「确认已执行」→ 门落 `executed`。

    这一支是给"动作其实跑成功了、进程死在收口之前"那种情况用的：老师核对过
    真实世界（学生确实收到了计划），把账目对齐。
    """
    gid = _make_in_doubt(env, "conv-confirm")

    with work_unit(env):
        assert gate_service.resolve_in_doubt(env, gid, retry=False) is True

    assert _gate_row(env, gid).status == "executed"


def test_in_doubt_sent_back_for_retry(env):
    """老师选「重试」→ 门退回 `open`，可以重新批准、重新重放。

    这一支是给"动作其实没跑成"用的。退回 `open` 而不是别的：那条通路（`claim_gate`
    → 重放 → `finish_gate`）已经有了，不需要为 in-doubt 另造一条执行路径。
    """
    gid = _make_in_doubt(env, "conv-retry-from-doubt")

    with work_unit(env):
        assert gate_service.resolve_in_doubt(env, gid, retry=True) is True

    assert _gate_row(env, gid).status == "open"


def test_resolve_refuses_gates_that_are_not_in_doubt(env):
    """反向钉子：只对 `in_doubt` 有效 —— 别把正常开着的门给「确认」掉了。

    这扇门还在等老师决定，一次「确认已执行」会把一个**从没执行过**的动作记成做过。
    """
    draft = _make_draft(env)
    gid = _open_gate(env, "conv-not-doubt", draft["draft_id"])

    with work_unit(env):
        assert gate_service.resolve_in_doubt(env, gid, retry=False) is False

    assert _gate_row(env, gid).status == "open", "没动它才对"


# ── 2.5 重放目标构造不出来的门，开门即失败 ──────────────────────────────

def test_gate_refuses_to_open_when_the_replay_target_is_not_constructible(env):
    """开不出一张**永远重放不了**的门。

    重放要的是「工具名 → 函数」+「kwargs」。工具名不在 `_TOOL_BY_NAME` 里时，
    门照样能开（`open_gate` 只把它当字符串存），老师点了批准才发现没东西可执行
    —— 那时门已经开过、整个会话被 guard 全拦，只能报错收场。

    校验必须在**开门之前**：命中得了工具、kwargs 序列化得了，才算一扇真门。
    """
    from agent.guard import GuardState

    gs = GuardState()
    gs.conversation_id = "conv-fake-tool"

    ok = gs.register_gate_entity(
        kind="boolean", tool_name="no_such_tool_at_all", kwargs={},
        action="", message="此操作需要老师确认后执行")

    assert ok is False, "工具不在 _TOOL_BY_NAME 里，门不该开出来"
    assert gate_service.get_open_gate(env, "conv-fake-tool") is None, \
        "拒绝之后库里不该留下任何门"


def test_gate_still_opens_for_a_real_tool(env):
    """反向：真工具照常开门 —— 别把校验写成"谁都不给开"（回归钉子）。"""
    from agent.guard import GuardState

    gs = GuardState()
    gs.conversation_id = "conv-real-tool"

    ok = gs.register_gate_entity(
        kind="boolean", tool_name="delete_bank", kwargs={"bank_id": "b1"},
        action="", message="此操作需要老师确认后执行")

    assert ok is True, "真工具应当照常开门"
    assert gate_service.get_open_gate(env, "conv-real-tool") is not None


# ── 被门拦下的调用，门关后必须能重试（否模型被迫改走别的路）────────────

def _open_boolean_gate(db, cid: str, tool: str = "delete_bank") -> str:
    """开一扇布尔门（与上面那个草稿门版本的 `_open_gate` 区分开）。"""
    from app.services.gate_service import gate_service
    from app.services.work_unit import work_unit
    with work_unit(db):
        return gate_service.open_gate(db, cid, kind="boolean", tool_name=tool, kwargs={})


def test_gate_blocked_call_can_be_retried_after_the_gate_closes(env):
    """被已开的门拦下的调用，老师处理完之后**必须能原样重试**。

    `guard.py` 的 `check_dedup` 对**每一次**调用都登记进 `seen_calls`，而「门开着 →
    本次暂缓」那条早退（`_waiting_out`）必须撤销它，否则门关掉之后模型重试同一个
    调用会被「检测到重复调用，已跳过」**静默吃掉**。

    实测表现（2026-10-17 端到端）：`assign_adaptive_practice` 在门开着时被拦一次，
    重放回来后模型改调 `generate_questions` / `save_to_bank` —— 计划第 5 步
    「布置一天的练习题」永远做不成，家长通知也跟着落空。

    **2.4 收尾改写**：「门开着」从一个内存 pending 变成**真的开一扇门**（判据是实体）。
    """
    import asyncio

    from agent.guard import GuardState, _make_guarded_tool
    from app.services.gate_service import gate_service

    cid = "conv-retry"
    executed = []

    async def assign_adaptive_practice(student_id: str = "s1"):
        """给某个学生布置自适应练习。"""
        executed.append(student_id)
        return json.dumps({"ok": True, "assigned": student_id})

    gs = GuardState()
    gs.conversation_id = cid
    gid = _open_boolean_gate(env, cid)   # 另一扇门开着：agent 多调的工具会被全拦
    guarded = _make_guarded_tool(assign_adaptive_practice, gs)

    first = json.loads(asyncio.run(guarded.ainvoke({"student_id": "s1"})))
    assert first.get("gate_open_blocked") is True, first
    assert executed == [], "门开着时不得执行"

    from app.services.work_unit import work_unit
    with work_unit(env):
        gate_service.close_gate(env, gid, "executed")   # 老师处理完确认卡

    second = json.loads(asyncio.run(guarded.ainvoke({"student_id": "s1"})))
    assert second.get("dedup_skipped") is not True, (
        f"门关掉之后同一次调用必须能重试，实际被判重复跳过：{second}"
    )
    assert executed == ["s1"], "门关了就该真的执行"


def test_replay_without_a_resolvable_gate_refuses_to_execute(env):
    """拿不到门 = 拿不到许可 ⇒ **不许执行**，而且要给人话。

    `_direct_replay_events` 自己的注释写着「批准后跑的是不可逆动作（删题库、执行
    命令、发送），所以"许可"必须是"占到这扇门"这件事本身」。

    **2.4 收尾改写**：原先用内存 pending + 空 gate_id 驱动。那个字段删了；
    现在钉的是**真实那条路** —— 客户端提交的 gate_id 那一行还在、但已经不是 open
    （已被另一次批准占掉 / 已判死）⇒ 拒执行 + "这次确认已经处理过了"。

    另一条（**压根没有门、也没提交 gate_id**）返回**空**，由调用方说
    「没有待处理的确认请求」—— 这是既有契约（`/resume` 那条路 `if _direct:` 非空
    就往另一个分支走），所以单独钉一条反向钉子。
    """
    import asyncio

    from agent.channel.langgraph_channel import _direct_replay_events
    from agent.guard import GuardState
    from app.services.gate_service import gate_service
    from app.services.work_unit import work_unit

    cid = "conv-no-gate"
    gid = _open_boolean_gate(env, cid)
    with work_unit(env):
        gate_service.close_gate(env, gid, "executed")   # 那扇门已经翻篇

    guard = GuardState()
    guard.conversation_id = cid

    called = []

    async def _dangerous(**kw):
        called.append(kw)
        return json.dumps({"ok": True})

    events, replay = asyncio.run(
        _direct_replay_events(guard, True, cid, gid))

    assert called == [], "没有占到门，破坏性动作绝不能执行"
    assert replay is None, "拒绝执行时不得回传重放信息"
    assert events, "至少要给一条人话，不能静默什么都不做"

    # 反向钉子：没有门、也没提交 gate_id ⇒ 返回空，由调用方开口
    events2, replay2 = asyncio.run(
        _direct_replay_events(GuardState(), True, "conv-empty", ""))
    assert events2 == [] and replay2 is None


def test_send_draft_leaves_no_open_gate_behind_for_that_draft(env):
    """同一条的更宽口径：发送之后，这个草稿名下不该再有任何 open 的门行。

    存量库里这种行有 74 条（2026-10-16 只读盘点），形态不止「草稿已 sent」一种：
    门指向的草稿已 superseded、甚至压根不存在。收敛器按判据统一收，这里钉住
    新产生的那一条不会混进去。
    """
    draft = _make_draft(env)
    did = draft["draft_id"]
    with work_unit(env):
        draft_svc.record_decision(env, did, "approve", "t1")
    _open_gate(env, "conv-orphan2", did)

    with work_unit(env):
        draft_svc.send_draft(env, "s1", did, "t1")

    leftover = (env.query(AgentGate)
                .filter(AgentGate.draft_id == did, AgentGate.status == "open").count())
    assert leftover == 0, f"发送后仍留下 {leftover} 行 open 门"
