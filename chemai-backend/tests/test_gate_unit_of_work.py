"""前置一的运行时契约：一次决定 = 一次提交，单元内不调模型。

对应计划 T3 (amend) 的 Verify 三条与 R7/R1/R8 三条决议的「必要测试」：

- 写入口名单的参数化"单元外调用必抛"（R7 D8=A，一条不漏）
- 五条通路各一条端到端"确实落库 + commit 次数 = 1"
- 活动单元内 `llm_service` 调用次数 = 0（断言抛出，不靠点名调用方）
- 关不掉的决定不算生效：`close_gate` 未命中 ⇒ 整单回滚；空编号不误伤
"""
import pytest
from sqlalchemy.orm import Session

from app.services import work_unit as wu


def _commits(monkeypatch, session):
    """数这条会话上 `commit` 的真实调用次数（`monkeypatch` 负责还原）。"""
    seen = []
    real = Session.commit

    def counted(self):
        if self is session:
            seen.append(1)
        return real(self)

    monkeypatch.setattr(Session, "commit", counted)
    return seen


def test_unit_commits_exactly_once_at_exit(db_session, monkeypatch):
    """单元内多次 flush ⇒ 出口只有一次 commit（这就是"一次决定一次提交"）。"""
    counts = _commits(monkeypatch, db_session)
    with wu.work_unit(db_session) as db:
        db.flush()
        db.flush()
        assert len(counts) == 0, "块内不许有提交：那半截状态就落库了"
    assert len(counts) == 1


def test_stray_commit_inside_the_unit_is_caught(db_session):
    """单元跑到一半被别处 commit 掉 ⇒ 下一次写入口当场抛，而不是悄悄分两笔。

    漏包最常见的形状就是新 service 沿用仓里"自己写自己提交"的习惯。`commit 次数 = 1`
    那类断言只覆盖被测到的通路，这条绊线要管的是没被测到的那一条（前置一 / ADR-0021 规则 1）。"""
    with pytest.raises(wu.WorkUnitCommittedEarly):
        with wu.work_unit(db_session) as db:
            wu.require_unit(db)
            db.commit()          # 有人抢跑了一次提交
            wu.require_unit(db)


def test_flush_alone_does_not_trip_the_transaction_tripwire(db_session):
    """只 flush 不换事务 ⇒ 绊线不许误报（写入口靠的就是反复进 `require_unit`）。"""
    from app.models.database import Student
    with wu.work_unit(db_session) as db:
        assert wu.require_unit(db) is db
        db.add(Student(student_id="S-tripwire", name="李四",
                       class_id="class_2025_1", status="approved"))
        db.flush()
        assert wu.require_unit(db) is db
    assert db_session.query(Student).filter_by(student_id="S-tripwire").first() is not None


def test_unit_rolls_back_the_whole_thing_on_exception(db_session, monkeypatch):
    from app.models.database import Student
    counts = _commits(monkeypatch, db_session)
    with pytest.raises(ValueError):
        with wu.work_unit(db_session) as db:
            db.add(Student(student_id="S-rollback", name="张三",
                           class_id="class_2025_1", status="approved"))
            db.flush()
            raise ValueError("写一半炸了")
    assert len(counts) == 0
    assert db_session.query(Student).filter_by(student_id="S-rollback").first() is None


def test_nested_unit_is_refused(db_session):
    """嵌套 = 内层把外层的半成品提交掉，回滚也救不回来，所以直接拒。"""
    with wu.work_unit(db_session):
        with pytest.raises(wu.WorkUnitAlreadyOpen):
            with wu.work_unit(db_session):
                pass


def test_require_unit_outside_a_unit_raises(db_session):
    with pytest.raises(wu.NoWorkUnit):
        wu.require_unit(db_session)


def test_require_unit_rejects_a_different_session(db_session):
    """另一条会话来写 = 写进一个没人提交的会话：静默丢写，必须硬失败。"""
    other = Session(bind=db_session.get_bind())
    try:
        with wu.work_unit(db_session):
            with pytest.raises(wu.NoWorkUnit):
                wu.require_unit(other)
    finally:
        other.close()


def test_model_call_inside_a_unit_raises(db_session, monkeypatch):
    """R1 D2=A：单元圈进模型往返 ⇒ WAL 写锁 held 到超时，运行时当场拒掉。

    判据是"有没有活动单元"，不是"谁调了模型"——所以传递调用（record_decision 经
    revise 摸到 LLM）挡得住，以后新增的调用点也挡得住。
    """
    from app.services.llm_service import llm_service
    monkeypatch.setattr(llm_service, "enabled", True)
    monkeypatch.setattr(llm_service, "dashscope_key", "test-key")
    monkeypatch.setattr(llm_service, "_call_llm_api",
                        lambda *a, **kw: {"success": True, "content": "ok"})
    assert wu.in_work_unit() is False
    assert llm_service.generate_text("hi").get("success") is True, "单元外不得误伤"
    with wu.work_unit(db_session):
        with pytest.raises(wu.ModelCallInsideWorkUnit):
            llm_service.generate_text("hi")


def test_the_guard_sits_on_the_network_throat(db_session):
    """守卫挂在 `_call_with_retry` 上：OCR、视觉批改、出题都汇到这一个点。

    挂在咽喉而不是逐个方法挂，才有"新方法也自动被罩住"的效果 —— 哪天有人绕过它
    自己发 httpx，这一条就红。
    """
    from app.services.llm_service import llm_service
    with wu.work_unit(db_session):
        with pytest.raises(wu.ModelCallInsideWorkUnit):
            llm_service._call_with_retry("http://example.invalid", "k", {})


@pytest.mark.parametrize("entry", ("chat", "chat_stream"))
def test_agent_provider_entries_obey_the_same_guard(db_session, entry):
    """ADR-0021 规则 2 那句"所有真实网络往返"要把 agent 自己的对话出口也算进去（eng 三轮 F3/D5）。

    `chat_stream` 是异步生成器，函数体到第一次取块才跑 —— 断言必须写在取块处，
    写成"调用即抛"会一直假绿。单元外不许误伤由下一条单独验（不碰网络）。
    """
    import asyncio

    from agent.provider.deepseek import DeepSeekProvider
    provider = DeepSeekProvider()
    msgs = [{"role": "user", "content": "hi"}]

    async def _first(gen):
        async for _ in gen:
            return "yielded"
        return "empty"

    async def _call():
        if entry == "chat":
            await provider.chat(msgs)
        else:
            await _first(provider.chat_stream(msgs))

    with wu.work_unit(db_session):
        with pytest.raises(wu.ModelCallInsideWorkUnit):
            asyncio.run(_call())
    asyncio.run(provider.close())


def test_the_provider_guard_does_not_fire_outside_a_unit():
    """单元外它就是句空话：不抛、不改任何行为（否则 agent 对话全废）。

    调的是缝上那个函数本体 —— provider 现在直接用它（`deepseek.py:75,117`），中间不再套
    一层只转发的包装。"provider 真的挂了它"由上面那条在单元内必抛的用例证。
    """
    from app.services.llm_transport import refuse_model_call_in_work_unit
    assert wu.in_work_unit() is False
    refuse_model_call_in_work_unit("DeepSeekProvider.chat")


def test_new_work_unit_uses_the_test_session(monkeypatch, db_session):
    """`new_work_unit` 延迟绑定 `get_db`：换库靠替换这个属性，不靠 import 的名字。"""
    from app.models import database as db_mod
    monkeypatch.setattr(db_mod, "get_db", lambda: iter([db_session]))
    with wu.new_work_unit() as db:
        assert db is db_session
        assert wu.active_unit() is db_session
    assert wu.active_unit() is None, "出口必须把活动单元摘掉，否则下一段代码以为还在单元里"


# ── 写入口名单：单元外调用必抛（R7 D8=A，一条不漏）──────────────────────

def _write_entries(db):
    """D8 批准的写入口清单（确认门这条链上的全部）。名字带 `#` 的是可读标签，不参与调用。

    这张表的边界要说准：`plan_run_service`、通知投递、题库等 service 仍各自
    `commit()`，它们不在本契约的约束面内（范围见 ADR-0021 的"适用范围"）。
    往这张表里加一行，就等于宣布"这个写入必须有人负责提交"。
    """
    from app.services.gate_service import gate_service
    from app.services.parent_report_draft_service import parent_report_draft_service as rsvc
    from app.services.plan_draft_service import plan_draft_service as psvc
    return {
        "gate.open_gate": lambda: gate_service.open_gate(
            db, "c1", kind="boolean", tool_name="bash", kwargs={}),
        "gate.close_gate": lambda: gate_service.close_gate(db, "g-none", "executed"),
        "gate.claim_gate": lambda: gate_service.claim_gate(db, "g-none"),
        "gate.close_conversation": lambda: gate_service.close_conversation(db, "c1"),
        "gate.supersede_stale_gates": lambda: gate_service.supersede_stale_gates(db),
        "plan.create_draft": lambda: psvc.create_draft(db, {"student_id": "s1",
                                                            "plan_data": {}}),
        "plan.revise_draft": lambda: psvc.revise_draft(
            db, {"draft_id": "d1", "content": {}, "feedback": "改"}),
        "plan.send_draft": lambda: psvc.send_draft(db, "s1"),
        "plan.record_decision": lambda: psvc.record_decision(db, "d1", "approve", "t1"),
        "report.create_draft": lambda: rsvc.create_draft(db, "s1"),
        "report.revise_draft": lambda: rsvc.revise_draft(
            db, {"draft_id": "d1", "content": {}, "feedback": "改"}),
        "report.send_draft": lambda: rsvc.send_draft(db, "s1"),
        "report.record_decision": lambda: rsvc.record_decision(db, "d1", "approve", "t1"),
    }


@pytest.mark.parametrize("entry", list(_write_entries(None).keys()))
def test_write_entry_outside_a_unit_raises(db_session, entry):
    """漏包通路必须当场抛，而不是"没人管就自己提交"（D8=A）。

    这条参数的价值在于红绿可证：把任一写入口里的 `require_unit` 删掉，这一条立刻红。
    """
    with pytest.raises(wu.NoWorkUnit):
        _write_entries(db_session)[entry]()


@pytest.mark.parametrize("reader", ["get_open_gate", "check_binding", "replay_target"])
def test_read_entries_need_no_unit(db_session, reader):
    """读入口不受约束（D8 批准的豁免面）：一次 HTTP 查询不该被迫先开事务。"""
    from app.services.gate_service import gate_service
    fn = getattr(gate_service, reader)
    args = {"get_open_gate": ("c1",), "check_binding": ("c1", ""),
            "replay_target": ("c1",)}[reader]
    assert fn(db_session, *args) in (None, {})


# ── 通路级：每条通路各开一个单元、一次决定一次提交 ─────────────────────

PLAN = {"plan_title": "张三计划", "weekly_goals": ["盐类水解"],
        "daily_tasks": [{"day": 1, "task": "复习"}]}


@pytest.fixture
def seeded(db_session, patch_get_db, monkeypatch):
    """一个学生 + 一位教他的老师 + 假模型（工具通路的端到端夹具）。"""
    from app.models.database import Student, TeacherClassSubject
    db_session.add(Student(student_id="s1", name="张三", class_id="c1", status="approved"))
    db_session.add(TeacherClassSubject(id=1, teacher_id="t1", class_id="c1",
                                       subject="chemistry"))
    db_session.commit()
    import json as _json
    import sys
    mod = sys.modules["app.services.llm_service"]
    monkeypatch.setattr(mod.llm_service, "generate_learning_plan",
                        lambda **kw: {"success": True,
                                      "content": _json.dumps(PLAN, ensure_ascii=False)})
    monkeypatch.setattr("agent.tools.context.current_teacher", lambda: "t1")
    return db_session


def _run(coro):
    import asyncio
    return asyncio.run(coro)


def test_tool_path_commits_once(seeded, monkeypatch):
    """通路④（agent 工具）：生成草稿端到端只提交一次，且模型调用在单元外。"""
    import json as _json
    from agent.tools.diagnosis import generate_learning_plan
    counts = _commits(monkeypatch, seeded)

    out = _json.loads(_run(generate_learning_plan(student_id="s1")))

    assert "draft_id" in out, out
    assert len(counts) == 1, f"一次生成一次提交，实测 {len(counts)} 次"
    from app.models.database import LearningPlanDraft
    assert seeded.query(LearningPlanDraft).count() == 1, "草稿行确实落库了"


def test_guard_gate_registration_commits_once(seeded, monkeypatch):
    """通路③（guard 登记门实体）：一次登记一次提交，登记失败不写半截。"""
    from agent.guard import GuardState
    counts = _commits(monkeypatch, seeded)
    g = GuardState()
    g.conversation_id = "conv-guard"

    assert g.register_gate_entity(kind="boolean", tool_name="bash", kwargs={"command": "ls"},
                                  action="", message="执行命令需要确认") is True
    assert len(counts) == 1


def test_after_commit_hooks_do_not_fire_on_rollback(db_session):
    """回滚的单元里，"提交之后再做的事"一件都不许做。

    这条管的是发送通路那份内存计划缓存：先写缓存再提交，一旦整单回滚，读缓存的接口
    就把一份根本没落库的计划当事实返回给学生 —— 症状比丢写更难查。
    """
    fired: list = []
    with pytest.raises(ValueError):
        with wu.work_unit(db_session):
            wu.after_commit(lambda: fired.append("a"))
            raise ValueError("提交前炸了")
    assert fired == []

    with wu.work_unit(db_session):
        wu.after_commit(lambda: fired.append("b"))
    assert fired == ["b"], "正常收尾必须把登记的副作用做完"


def test_close_miss_rolls_back_the_whole_decision(db_session, monkeypatch):
    """R8 D9=A：关不掉的决定不算生效 —— 批准与关门同生同死。

    造一个"批准写进去、门却已被收敛掉"的现场：单元里先记批准再关一扇不存在的门，
    返回 False 时抛出 ⇒ 批准一并回滚，不会出现"日志说批了、门还开着"。
    """
    from app.models.database import LearningPlanDraft
    from app.services.gate_service import gate_service
    from app.services.plan_draft_service import plan_draft_service as psvc
    db_session.add(LearningPlanDraft(draft_id="d-miss", student_id="s1", version=1,
                                     status="awaiting_confirmation",
                                     content={"plan_title": "t"}))
    db_session.commit()
    counts = _commits(monkeypatch, db_session)

    with pytest.raises(RuntimeError):
        with wu.work_unit(db_session):
            psvc.record_decision(db_session, "d-miss", "approve", "t1")
            if not gate_service.close_gate(db_session, "g-不存在", "executed"):
                raise RuntimeError("门已更新，请重新决定")

    assert len(counts) == 0, "整单回滚：一次都不该提交"
    assert db_session.query(LearningPlanDraft).filter_by(draft_id="d-miss").one() \
        .teacher_approved is False, "批准不得单独生效"


def test_claim_gate_is_a_one_shot_permit(db_session):
    """占门是"一次批准一次许可"：第二次一定抢不到，且不改任何状态。

    这是布尔门（执行命令、删题库）不双执行的唯一根据 —— 关门放在执行之后挡不住
    第二次批准，因为两次批准都能在读到 open 的那一瞬间通过。
    """
    from app.services.gate_service import gate_service
    with wu.work_unit(db_session):
        gid = gate_service.open_gate(db_session, "conv-claim", kind="boolean",
                                     tool_name="delete_bank", kwargs={"set_id": "b1"})
    assert gid

    with wu.work_unit(db_session):
        first = gate_service.claim_gate(db_session, gid)
    with wu.work_unit(db_session):
        second = gate_service.claim_gate(db_session, gid)

    assert first is True and second is False, "同一扇门只许占到一次"
    assert gate_service.get_open_gate(db_session, "conv-claim") is None
    from app.models.database import AgentGate
    row = db_session.query(AgentGate).filter_by(gate_id=gid).one()
    # 2026-10-17（gate-lifecycle 2.7）：占住落的是 `executing`（结果未知），
    # 不是 `executed` —— 「这活儿归我了」与「做完了没」是两个状态，
    # 由 `finish_gate` 收口（成功 → executed，失败 → 退回 open）。
    # 本用例钉的东西没变：同一扇门只许占到一次，抢不到的那次不许改动状态。
    assert row.status == "executing", "抢不到的那次不许改动状态"


def test_claim_gate_only_accepts_an_open_row(db_session):
    """占门的条件是"这一行现在开着"，不是"这一行存在"。

    写成"查一行、赋值"的读改形状，对已经 cancelled/executed 的行也会返回成功 ——
    那张早就翻篇的卡片就又拿到了一次执行许可。
    """
    from app.models.database import AgentGate
    from app.services.gate_service import gate_service
    with wu.work_unit(db_session):
        gid = gate_service.open_gate(db_session, "conv-race", kind="boolean",
                                     tool_name="bash", kwargs={"command": "ls"})
        assert gate_service.close_gate(db_session, gid, "cancelled") is True

    with wu.work_unit(db_session):
        assert gate_service.claim_gate(db_session, gid) is False, \
            "非 open 的行不许被占成执行许可"
    assert db_session.query(AgentGate).filter_by(gate_id=gid).one().status == "cancelled"
