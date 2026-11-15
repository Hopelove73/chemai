"""阶段 2 — 确认门合一（issue #18 阶段 2；ADR-0015）。

通用 gate 实体（agent_gates 表）：draft / boolean 两个 adapter 落同一 seam。
- gate_id 身份：草稿门 g-draft-{draft_id}；布尔门 g-tool-{tool}-{hash}。
- 一决定一动作：决定带 gate_id，与开着的门不一致 → 拒绝执行 + 重发门状态
  （回归红线④：批准门 A 时不得执行门 B 的动作）。
- 门开互斥：每会话一个开着的门；后开的门被全拦（阶段 1 已拦工具，这里钉实体层）。
- 重放后补 dedup 登记：LLM 重调同参工具命中「重复调用」，不再双执行（回归红线⑥）。
"""
import asyncio
import hashlib
import json

import pytest

from agent.guard import GuardState, _make_guarded_tool


def _run(coro):
    async def go():
        return await coro
    return asyncio.run(go())


def _make_draft(db, student_id="s1", created_by="t1") -> str:
    """直接建一条 awaiting 草稿行（不走 LLM），返回 draft_id。"""
    import uuid as _u
    from app.models.database import LearningPlanDraft
    did = _u.uuid4().hex[:12]
    db.add(LearningPlanDraft(
        draft_id=did, student_id=student_id, version=1,
        status="awaiting_confirmation", content={"plan_title": "t"},
        created_by=created_by))
    db.commit()
    return did


# ── 1. gate_id 身份 ──────────────────────────────────────────────────

def test_draft_gate_id_is_stable_per_draft():
    from app.services.gate_service import draft_gate_id, boolean_gate_id
    assert draft_gate_id("d1") == "g-draft-d1"
    assert draft_gate_id("d1") == draft_gate_id("d1")
    assert draft_gate_id("d2") != draft_gate_id("d1")

    kwargs = {"student_id": "s1", "draft_id": "d1"}
    bid = boolean_gate_id("t-A", "assign_adaptive_practice", kwargs)
    assert bid.startswith("g-tool-assign_adaptive_practice-")
    assert len(bid) == len("g-tool-assign_adaptive_practice-") + 8
    assert bid == boolean_gate_id("t-A", "assign_adaptive_practice", dict(kwargs))
    assert bid != boolean_gate_id("t-A", "assign_adaptive_practice", {"student_id": "s2"})


def test_boolean_gate_id_is_scoped_to_its_conversation():
    """T18：编号掺会话。`gate_id` 是全库 UNIQUE 的，只哈希 tool+kwargs 时两位
    教师同工具同参数会拿到同一个编号——`open_gate` 复用同一行，B 批准后执行的是
    挂在 A 会话上的门，A 的角标也多出一条不是她的事。"""
    from app.services.gate_service import boolean_gate_id
    kwargs = {"student_id": "s1", "count": 5}
    a = boolean_gate_id("t-A", "assign_adaptive_practice", kwargs)
    b = boolean_gate_id("t-B", "assign_adaptive_practice", kwargs)
    assert a != b, "不同会话的同参数调用必须各得一道门"



def test_gate_entity_open_decide_replay_lifecycle(db_session, monkeypatch, unit):
    """实体生命周期：open → decide(approve) → executed；草稿门绑定 draft。"""
    import app.models.database as dbmod

    def _fake_get_db():
        yield db_session
    monkeypatch.setattr(dbmod, "get_db", _fake_get_db)

    did = _make_draft(db_session)

    from app.services.gate_service import gate_service
    with unit(db_session):   # 写入口须落在工作单元里（前置一）
        gid = gate_service.open_gate(
            db_session, conversation_id="t1", kind="draft",
            tool_name="send_learning_plan",
            kwargs={"student_id": "s1", "draft_id": did},
            draft_id=did, version=1, action="send_student",
            message="学习计划（第 1 版）等待确认")
    assert gid == f"g-draft-{did}"

    open_gate = gate_service.get_open_gate(db_session, "t1")
    assert open_gate is not None
    assert open_gate["gate_id"] == gid
    assert open_gate["kind"] == "draft"
    assert open_gate["kwargs"] == {"student_id": "s1", "draft_id": did}

    with unit(db_session):   # 写入口须落在工作单元里（前置一）
        gate_service.close_gate(db_session, gid, "executed")


def test_boolean_gate_entity_survives_in_db(db_session, monkeypatch, unit):
    """布尔门实体化：拦下即登记实体，重启后 /history 可重建（ADR-0015 §2）。"""
    import app.models.database as dbmod

    def _fake_get_db():
        yield db_session
    monkeypatch.setattr(dbmod, "get_db", _fake_get_db)

    from app.services.gate_service import gate_service
    kwargs = {"student_id_or_class_id": "s1", "knowledge_points": ["氧化还原"]}
    with unit(db_session):   # 写入口须落在工作单元里（前置一）
        gid = gate_service.open_gate(
            db_session, conversation_id="t2", kind="boolean",
            tool_name="assign_adaptive_practice", kwargs=kwargs,
            action="", message="布置练习需要确认")
    open_gate = gate_service.get_open_gate(db_session, "t2")
    assert open_gate["gate_id"] == gid
    assert open_gate["kind"] == "boolean"
    assert open_gate["tool_name"] == "assign_adaptive_practice"


# ── 2. 强绑定：决定带错 gate_id → 拒绝 + 重发门状态 ──────────────────

def test_decision_with_wrong_gate_id_is_rejected(db_session, monkeypatch, unit):
    import app.models.database as dbmod

    def _fake_get_db():
        yield db_session
    monkeypatch.setattr(dbmod, "get_db", _fake_get_db)

    did = _make_draft(db_session)
    gid = f"g-draft-{did}"

    from app.services.gate_service import gate_service
    with unit(db_session):   # 写入口须落在工作单元里（前置一）
        gate_service.open_gate(db_session, conversation_id="t3", kind="draft",
                               tool_name="send_learning_plan",
                               kwargs={"student_id": "s1", "draft_id": did},
                               draft_id=did, version=1, action="send_student",
                               message="等待确认")

    result = gate_service.check_binding(db_session, "t3", "g-draft-OTHER")
    assert result is not None, "gate_id 不一致必须拒绝（回归红线④）"
    gate = result["open_gate"]
    assert gate["gate_id"] == gid
    assert "gate_mismatch" in result

    assert gate_service.check_binding(db_session, "t3", gid) is None
    # 未带 gate_id（旧客户端）+ 单门开着 → 放行
    assert gate_service.check_binding(db_session, "t3", "") is None


# ── 3. 重放后 dedup 补登记 —— 不再双执行 ─────────────────────────────

def test_replay_remembers_call_so_llm_recall_hits_dedup(db_session, patch_get_db):
    """回归红线⑥：重放执行后补 dedup 登记 —— 重调同参工具被拦，不双执行。

    2026-10-17（gate-lifecycle 2.4 收尾）改写驱动方式：原先手工模拟"清内存 pending
    + 裸 fn 执行 + 补登记"。那个内存字段没了，现在走**真实那条路**
    （`_direct_replay_events`：从门实体取目标 → 执行 → 补 dedup），断言也换成
    真实效果 + 真实护栏。
    """
    from agent.tools.exam_bank import delete_bank
    from app.services.gate_service import gate_service
    from app.services.question_bank import question_bank_service
    from app.services.work_unit import work_unit

    cid = "t-dedup"
    sid = question_bank_service.create_set(db_session, name="待删题库", teacher_id="t1")["set_id"]
    with work_unit(db_session):
        gate_service.open_gate(db_session, cid, kind="boolean",
                               tool_name="delete_bank", kwargs={"set_id": sid})

    gs = GuardState()
    gs.conversation_id = cid

    from agent.channel.langgraph_channel import _direct_replay_events
    _, info = _run(_direct_replay_events(gs, True, cid))
    assert info is not None, "重放该跑掉"
    assert question_bank_service.get_set(db_session, sid) is None, "动作没真的执行"

    # LLM 重调同参 → dedup 拦截，不双执行（重放成功时补过登记）
    guarded = _make_guarded_tool(delete_bank, gs, requires_approval=False)
    out = json.loads(_run(guarded.ainvoke({"set_id": sid})))
    assert out.get("dedup_skipped") is True, out


# ── 4. 布尔门拦下即返回门卡载荷（adapter 据此发卡，不再靠 interrupt）──

def test_requires_approval_block_returns_gate_card_payload(db_session, patch_get_db):
    """布尔门拦下 → approval_needed + gate 信息（与草稿门同构的卡事件来源）。

    必须打补丁 `get_db`：`register_gate_entity` 走的是进程内 `next(get_db())`，
    不打补丁就把门行写进 tracked 的 `chemai.db`（T14 记的那笔债）。
    """
    # 2026-10-17（gate-lifecycle 2.5）：工具名必须是真的。门开出来就得重放得起来，
    # `_TOOL_BY_NAME` 里没有的名字一律不开门 —— 先前这里叫 `fake_tool`，那是夹具
    # 走在了现实前面：生产里 `requires_approval` 的工具全来自 `TOOL_APPROVAL_REQUIRED`，
    # 清一色真工具。
    async def assign_adaptive_practice(student_id: str = ""):
        """Fake destructive tool."""
        return json.dumps({"ok": True})

    gs = GuardState()
    gs.conversation_id = "t9"
    guarded = _make_guarded_tool(assign_adaptive_practice, gs, requires_approval=True)
    out = json.loads(_run(guarded.ainvoke({"student_id": "241914"})))
    assert out.get("approval_needed") is True, out
    assert out.get("gate_id", "").startswith("g-tool-assign_adaptive_practice-"), out
    from app.services.gate_service import gate_service
    assert out["gate_id"] == gate_service.get_open_gate(db_session, "t9")["gate_id"], \
        "卡片编号必须与实体行同一来源（掺会话后两处都要算出同一个值）"
    assert "gate_open_blocked" not in out


def test_agent_no_longer_has_request_approval_tool():
    """interrupt() 退役：v2 工具集里不再有 request_approval（ADR-0015 §2）。"""
    from agent.langgraph_agent_v2 import ALL_TOOLS, _TOOL_BY_NAME
    assert "request_approval" not in _TOOL_BY_NAME
    names = {getattr(t, "__name__", "") for t in ALL_TOOLS}
    assert "request_approval" not in names


# ── 5. W4：门登记装锁 + 失败撤回（不发幽灵门卡）──────────────────────

def test_acquire_gate_refuses_when_a_gate_is_already_open(db_session, patch_get_db):
    """acquire_gate：锁内**复查的是实体**，已有一扇算数的开门就拒绝。

    2.4 收尾：它原先复查内存里那个字段；字段删了，复查改成问门那一行（ADR-0030）。
    返回三态 `ok` / `busy` / `failed` —— 调用方要分开交代（"已有卡在等" vs
    "登记写不进去"），所以不能再压成一个布尔。
    """
    from app.services.gate_service import gate_service
    from app.services.work_unit import work_unit

    cid = "t-serialize"
    g = GuardState()
    g.conversation_id = cid
    kw = dict(kind="boolean", tool_name="delete_bank", kwargs={"set_id": "b1"},
              action="", message="m")

    # 库里还没门 → 第一次拿得到
    assert _run(g.acquire_gate(**kw)) == "ok"
    # 第一扇已经落库了 → 第二扇必须被拒（而且不会再落一扇）
    assert _run(g.acquire_gate(**kw)) == "busy"
    og = gate_service.get_open_gate(db_session, cid)
    assert og is not None and og["tool_name"] == "delete_bank"


def test_concurrent_boolean_gate_cannot_overwrite_draft_gate(db_session, patch_get_db):
    """W4a 回归：并行 tool_calls 的 await 窗口内，布尔门不得覆盖已登记的草稿门
    （旧布尔分支无重验 → 双门齐开 / 草稿门被吞，实测 repro-zhou-0920）。

    **2.4 收尾改写**：原先靠"无 conversation_id + 内存 pending"跑这个竞态。
    字段删了，现在必须**真有会话 + 真门行**才谈得上"覆盖"——断言也从
    "内存里 pending 是谁"改成"库里开着的那扇门是谁"。
    登记搬进锁里之后（`acquire_gate`），第二个门在锁内复查就能看见第一扇。
    """
    from app.services.gate_service import gate_service

    cid = "t-race"
    did = _make_draft(db_session)                 # 草稿门要能算数：草稿行得在 awaiting
    g = GuardState()
    g.conversation_id = cid

    async def gen_tool(student_id: str = ""):
        """生成学习计划（草稿门，登记后不覆盖）。"""
        await asyncio.sleep(0.005)  # 让布尔门也通过入口全拦检查（竞态窗口）
        return json.dumps({"approval_needed": True, "student_id": student_id,
                           "draft_id": did, "version": 1,
                           "action": "send_student", "message_gate": "计划等待确认"})
    gen_tool.__name__ = "generate_learning_plan"  # ∈ _GATE_OPEN_TOOLS → Site2

    async def bool_tool(path: str = ""):
        """工作区外访问（按次审批布尔门，Site3——旧代码无重验的那个）。"""
        await asyncio.sleep(0.02)  # 晚于草稿门登记 → 触发覆盖竞态
        return json.dumps({"approval_needed": True, "message": "需要批准"})
    bool_tool.__name__ = "read_outside"  # ∉ _GATE_OPEN_TOOLS → Site3

    gd = _make_guarded_tool(gen_tool, g)
    gb = _make_guarded_tool(bool_tool, g)

    async def go():
        return await asyncio.gather(gd.ainvoke({"student_id": "s1"}),
                                    gb.ainvoke({"path": "/etc/passwd"}))

    r_draft, r_bool = _run(go())
    draft = json.loads(r_draft) if isinstance(r_draft, str) else r_draft
    boolo = json.loads(r_bool) if isinstance(r_bool, str) else r_bool

    assert draft.get("approval_needed") is True, draft
    assert boolo.get("error") == "waiting_for_gate", boolo
    assert boolo.get("gate_open_blocked") is True, boolo
    # 关键：库里开着的仍是**草稿门**，没被布尔门 read_outside 覆盖成第二扇
    og = gate_service.get_open_gate(db_session, cid)
    assert og is not None and og["kind"] == "draft", og


def test_gate_register_failure_rolls_back_no_ghost_card(db_session, patch_get_db,
                                                       monkeypatch):
    """B1 fail-closed：门实体登记失败 → 撤回拦截 + 可恢复报错，
    绝不发一张重启后重建不出的幽灵门卡。"""
    g = GuardState()
    g.conversation_id = "conv-x"  # 有会话 → register_gate_entity 会真的落库

    def _boom(*args, **kwargs):
        raise RuntimeError("db down")
    # 登记失败 = 那一次开门写不进去（前置一之后 guard 走 work_unit + open_gate）
    monkeypatch.setattr("app.services.gate_service.gate_service.open_gate", _boom)

    async def delete_bank(set_id: str = ""):
        """Fake destructive tool（真工具名，2.5 要求门开得出来才给开）。"""
        return json.dumps({"ok": True})

    guarded = _make_guarded_tool(delete_bank, g, requires_approval=True)
    out = json.loads(_run(guarded.ainvoke({"set_id": "b1"})))
    assert out.get("error") == "gate_register_failed", out
    assert out.get("recoverable") is True, out
    assert "approval_needed" not in out, "登记失败不得发幽灵门卡"
    # 2.4 收尾：撤回的对象从"内存里的 pending"变成"门那一行" —— 它压根没落库
    from app.services.gate_service import gate_service
    assert gate_service.get_open_gate(db_session, "conv-x") is None,         "登记失败却留下了门行（幽灵卡）"
