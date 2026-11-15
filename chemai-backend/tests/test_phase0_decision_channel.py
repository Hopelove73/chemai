"""阶段 0 — 决策通道唯一化回归（ADR-0015；issue #18 阶段 0）。

门开着时老师打字的语义只有两种，且**打字永不构成批准**：
- 确认词（同意/好的/发吧…含「不要发送」这类误匹配）→ 引导文案，零副作用；
- 其他文字 → 草稿门=打回+反馈（ADR-0013 防御分支），布尔门=作废待批。

另钉幽灵批准：取消/放弃后 pending 必须清空——否则后一句「好的」
会命中旧的确认分支执行已取消的动作。
"""
import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.guard import GuardState
from app.models.database import Base

CID = "conv-p0"


@pytest.fixture(autouse=True)
def gate_db(monkeypatch):
    """每条用例一个内存库，并把取库的路指过来。

    `_classify_typed_text` / `_void_open_gate` 都**问实体**（gate-lifecycle 2.4 收尾），
    不再读进程内存 —— 所以这一档必须有一个真库和真门行。
    """
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    import app.models.database as db_mod
    monkeypatch.setattr(db_mod, "get_db", lambda: iter([session]))
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _run(coro):
    async def go():
        return await coro
    return asyncio.run(go())


def _open_gate(tool_name: str = "send_learning_plan") -> GuardState:
    """开一扇门（等价 guard 拦下时的登记）——**实体**，不是内存 pending。

    统一用布尔门：本档判定走的是调用方显式传的 `is_plan_gate`，门那行的 kind
    不参与；用布尔门省掉铺草稿链（草稿门的判据还要求草稿行停在 awaiting）。
    """
    from app.services.gate_service import gate_service
    from app.services.work_unit import new_work_unit

    with new_work_unit() as db:
        gate_service.open_gate(db, CID, kind="boolean", tool_name=tool_name,
                               kwargs={"student_id": "s1", "draft_id": "d1"})
    gs = GuardState()
    gs.conversation_id = CID
    return gs


def _gate_open() -> bool:
    from app.services.gate_service import gate_service
    from app.models.database import get_db
    db = next(get_db())
    try:
        return gate_service.get_open_gate(db, CID) is not None
    finally:
        db.close()


def _classify(message: str, guard, is_plan_gate: bool):
    """经 intake 分类（S3 起唯一入口），guidance 的两个 SSE 事件就地拼装——
    与 /stream 调用点同一形状，事件断言口径不变。"""
    import json as _json
    from agent.channel.langgraph_channel import _pending_from_entity
    from app.services import decision_intake
    kind, text = decision_intake.classify_typed(
        CID, message, _pending_from_entity(CID), is_plan_gate)
    if kind == "guidance":
        _t = "data: " + _json.dumps({"type": "text", "content": text},
                                    ensure_ascii=False)
        _d = "data: " + _json.dumps({"type": "done"}, ensure_ascii=False)
        return kind, [_t + "\n\n", _d + "\n\n", "data: [DONE]\n\n"]
    return kind, []


# ── 1. 确认词 → 引导，零副作用 ──────────────────────────────────────

def test_confirm_word_yields_guidance_not_execution():
    gs = _open_gate()
    kind, events = _classify("同意", gs, is_plan_gate=True)
    assert kind == "guidance"
    assert not any('"tool_result"' in e for e in events), "确认词不得执行任何工具"
    assert _gate_open(), "引导不消费待批现场（门还开着）"


def test_negation_with_send_word_is_guidance_never_send():
    """「不要发送」含「发送」会被词表命中——降级后只是引导，绝不发送。"""
    gs = _open_gate()
    kind, events = _classify("不要发送", gs, is_plan_gate=True)
    assert kind == "guidance"
    assert not any('"tool_result"' in e for e in events),         "「不要发送」被当成批准真发送过——回归红线"


def test_guidance_events_are_text_done_only():
    gs = _open_gate()
    _, events = _classify("好的", gs, is_plan_gate=False)
    kinds = []
    for e in events:
        raw = e[len("data: "):].strip() if e.startswith("data: ") else e.strip()
        if raw in ("[DONE]", ""):
            continue
        kinds.append(json.loads(raw)["type"])
    assert kinds == ["text", "done"], kinds


# ── 2. 其他文字 → 草稿门打回 / 布尔门作废 ────────────────────────────

def test_free_text_on_draft_gate_is_reject_feedback():
    gs = _open_gate()
    kind, _ = _classify("把第三周改成复习盐类水解", gs, is_plan_gate=True)
    assert kind == "reject"
    assert _gate_open(), "打回现场保留（修订循环消费）"


def test_free_text_on_boolean_gate_voids_pending():
    gs = _open_gate(tool_name="assign_adaptive_practice")
    kind, _ = _classify("先别管这个", gs, is_plan_gate=False)
    assert kind == "void"
    # 2.4 收尾：`_classify_typed_text` 只**判**语义（纯函数），作废的落点在调用方
    # （`_void_open_gate` 把门关掉）。下面 `test_after_void_closes_the_gate` 钉那条。
    assert kind == "void"


# ── 3. 幽灵批准：取消后 pending 清空 ────────────────────────────────

# 这里原是 `test_cancel_clears_pending_so_no_ghost_approval` —— 测 `request_approval`
# 工具取消后清内存 pending。**2026-10-17 随 gate-lifecycle 2.4 收尾退休**（不是丢覆盖）：
# 那个工具在 ADR-0015 阶段 2 就退役了，2.4 把它的工厂和被它读的字段一起删了。
# "取消不执行、且门不会被收走"这条语义现在钉在
# `tests/test_approval_replay.py::test_direct_replay_cancel_does_not_replay`。


def test_after_void_no_confirm_branch_triggers():
    """作废之后再来确认词：门已关 → 不进引导分支（按普通消息流转）。

    **2.4 收尾改写了驱动方式**：原先"作废"清的是内存 pending，现在落点是
    `_void_open_gate(cid)`（把关着的那一扇关掉）。所以这里显式走那一步 ——
    原先它是调用方顺手做的，而调用方（`/stream`）在分类之后立刻调用它。
    """
    from agent.channel.langgraph_channel import _void_open_gate

    gs = _open_gate()
    kind, _ = _classify("先别管这个", gs, is_plan_gate=False)
    assert kind == "void"
    assert _void_open_gate(CID) is True, "作废必须真的把门关掉 —— 否则卡片还在、工具仍被全拦"
    assert not _gate_open()
    kind2, _ = _classify("好的", gs, is_plan_gate=False)
    assert kind2 == "none", "pending 已清，确认词不再触发任何门分支"
