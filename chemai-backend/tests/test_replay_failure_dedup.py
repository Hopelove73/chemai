"""批准重放失败不得锁死重试（eng 审查 D2，决策 A）。

`_direct_replay_events` 无条件 `remember_call`（`langgraph_channel.py:787`）。
该登记的目的写在 `guard.py:187` 注释里：防止 LLM 重调同参工具造成**双执行**。
但重放抛异常时根本没有执行任何东西，登记后老师重试的那一次会被
`check_dedup` 拦成「检测到重复调用，已跳过」——而 `guard_state` 按会话缓存，
`seen_calls` 跨回合存活，于是任务永久卡在失败态，老师怎么催都只看到一句重复调用。

修复：只有重放成功才登记 dedup；失败时反向 `forget_call`（该方法本就是为
「未实际执行」而设，`guard.py:181`）。

本文件另外钉住两件事（eng 三轮 F2/D4）：重放失败要在服务端留一条 `gate_replay_failed`，
dedup 登记自己失败要留 `gate_dedup_update_failed` —— 页面载荷里那句人话只有老师当场看得见。
"""
import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.guard import GuardState
from agent.channel.langgraph_channel import _direct_replay_events
from app.models.database import Base
from app.services.gate_service import gate_service
from app.services.work_unit import new_work_unit


def _key(name: str, kwargs: dict) -> str:
    return f"{name}:{json.dumps(kwargs, sort_keys=True, ensure_ascii=False)}"


# 门行的 `tool_name` 必须是注册表里的真名字（2.5：重放目标要构造得出来）
_NAME = "delete_bank"
_KWARGS = {"set_id": "b-1"}


@pytest.fixture(autouse=True)
def env(monkeypatch):
    """每条用例一个内存库（`_entity_db` / `work_unit._session` 都是延迟绑定的）。"""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine, expire_on_commit=False)()
    import app.models.database as db_mod
    monkeypatch.setattr(db_mod, "get_db", lambda: iter([session]))
    yield session
    session.close()
    Base.metadata.drop_all(engine)


def _pending(monkeypatch, fn, cid: str = "t-replay"):
    """开一扇**真门**，执行体换成受控函数。

    **2026-10-17（2.4 收尾）改写**：原先往内存 `pending_approval` 里塞一个假函数、
    cid 传空。那个字段删了，重放目标现在只从门实体来 —— 所以这里必须有会话 + 门行。
    执行体仍可注入：`_rebuild_pending_from_gate` 按门行的 `tool_name` 去
    `_TOOL_BY_NAME` 取函数，把那一项换掉即可（这样失败/异常分支仍然可控）。
    """
    import agent.langgraph_agent_v2 as _v2
    with new_work_unit() as db:
        gate_service.open_gate(db, cid, kind="boolean",
                               tool_name=_NAME, kwargs=dict(_KWARGS))
    monkeypatch.setitem(_v2._TOOL_BY_NAME, _NAME, fn)
    guard = GuardState()
    guard.conversation_id = cid
    return guard


async def _raising(*a, **k):
    raise RuntimeError("下游服务 500")


async def _returning_error(*a, **k):
    return json.dumps({"error": "学员不存在"}, ensure_ascii=False)


async def _ok(*a, **k):
    return json.dumps({"ok": True, "assigned": 5}, ensure_ascii=False)


def _run(guard, cid: str = "t-replay"):
    return asyncio.run(_direct_replay_events(guard, True, cid, ""))


def test_failed_replay_does_not_register_dedup_key(monkeypatch):
    """重放抛异常 → 什么都没跑，不得留下 dedup 键（老师重试必须放行）。"""
    guard = _pending(monkeypatch, _raising)

    events, info = _run(guard)

    assert _key(_NAME, _KWARGS) not in guard.seen_calls, \
        "重放失败却登记了 dedup，老师重试会被『检测到重复调用』拦死"
    payload = json.loads(events[0])
    assert payload["success"] is False
    assert info["success"] is False


def test_error_result_replay_does_not_register_dedup_key(monkeypatch):
    """工具返回 error（同样未达成）→ 也不得锁死重试。"""
    guard = _pending(monkeypatch, _returning_error)

    _run(guard)

    assert _key(_NAME, _KWARGS) not in guard.seen_calls


def test_successful_replay_still_registers_dedup_key(monkeypatch):
    """成功重放保持原语义：登记，防 LLM 重调双执行（回归护栏）。"""
    guard = _pending(monkeypatch, _ok)

    events, info = _run(guard)

    assert info["success"] is True
    assert _key(_NAME, _KWARGS) in guard.seen_calls, \
        "成功重放必须登记 dedup，否则同一破坏性工具会双执行"


def test_failed_replay_leaves_a_server_side_trace(caplog, monkeypatch):
    """重放炸了要在服务端留一条记录，不能只把「重放失败」塞进页面载荷。

    载荷那句话只有老师当场看得见；事后问「昨晚那扇门到底跑没跑、炸在哪一步」，
    日志里如果没有 `gate_replay_failed`，这条通路就是查无此人（eng 三轮 F2/D4）。"""
    import logging
    guard = _pending(monkeypatch, _raising)

    with caplog.at_level(logging.WARNING):
        _run(guard)

    traces = [r.getMessage() for r in caplog.records]
    assert any("gate_replay_failed" in m and "下游服务 500" in m for m in traces), \
        f"重放失败必须留痕，实际日志：{traces}"


def test_dedup_bookkeeping_failure_is_not_silent(caplog, monkeypatch):
    """dedup 登记本身失败也要留痕：它决定的是"老师的重试会不会被拦"，静默等于埋雷。"""
    import logging
    guard = _pending(monkeypatch, _ok)

    def boom(*a, **k):
        raise RuntimeError("seen_calls 写不进去")

    monkeypatch.setattr(guard, "remember_call", boom)

    with caplog.at_level(logging.WARNING):
        _run(guard)

    assert any("gate_dedup_update_failed" in r.getMessage() for r in caplog.records)
