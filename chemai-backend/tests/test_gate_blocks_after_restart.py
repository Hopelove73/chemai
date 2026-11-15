"""门开着就全拦 —— 重启之后还成不成立（gate-lifecycle 2.4）。

**它挡的是什么。** C1-Q3 那条「有未决门时 agent 的一切工具调用都被拒」，判据一直是
**内存里**的 `guard_state.pending_approval`。而内存随进程一起没了 —— 这是重启的定义。
门那行在库里，确认卡照显示（卡片本来就来自库），可那道闸没了：老师看着一张还在等他的
卡，agent 已经能往下推进。

`_rebuild_pending_from_gate` 的存在本身就承认了这个缺口（它专门用来在「内存 pending
已失」时从实体重建），但它只被**重放**那条路调用，拦不拦得住工具这条路上没人问库。
"""
import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agent.guard import GuardState, _make_guarded_tool
from app.models.database import Base
from app.services import work_unit as wu
from app.services.gate_service import gate_service


def _run(coro):
    async def go():
        return await coro
    return asyncio.run(go())


def _db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine, expire_on_commit=False)()


def _pin(monkeypatch, session):
    """把 guard 自己的 `get_db` 指到这个库上（guard 是延迟绑定取它的）。

    必须走 `monkeypatch`：直接赋值换的是**模块全局**，用完不还原就把后面所有
    用例的库指到一个已关掉的会话上 —— 本会话实测，5 个排在后面的**无关**测试文件
    当场全红（`test_notification_dispatch` 等），单跑又全绿。
    """
    import app.models.database as _db_mod
    monkeypatch.setattr(_db_mod, "get_db", lambda: iter([session]))


def test_open_gate_in_the_db_blocks_tools_after_a_restart(monkeypatch):
    """重启：内存清零，门还在库里 → 工具仍然不许执行。

    反例就是现状：`GuardState` 是新建的（`pending_approval` 为 None），
    于是这一整条闸门形同不存在。
    """
    db = _db()
    _pin(monkeypatch, db)
    try:
        with wu.work_unit(db):
            gate_service.open_gate(db, "t-restart", kind="boolean",
                                   tool_name="delete_bank", kwargs={"bank_id": "b1"})
        # 重启后的那一轮：全新的 GuardState，内存里什么都没有
        gs = GuardState()
        gs.conversation_id = "t-restart"

        ran = []

        async def any_tool(student_id: str = ""):
            """Fake tool."""
            ran.append(student_id)
            return json.dumps({"ok": True})

        guarded = _make_guarded_tool(any_tool, gs, requires_approval=False)
        out = json.loads(_run(guarded.ainvoke({"student_id": "241914"})))

        assert ran == [], "重启后门还开着，工具却执行了 —— 那道闸只认内存"
        assert out.get("error") == "waiting_for_gate", out
    finally:
        db.close()


def test_no_gate_means_no_blocking(monkeypatch):
    """反向钉子：库里没门时一律放行 —— 查库写严了会把 agent 整个锁死。

    （这条必须和上面那条一起看：拦的依据是**库里真有一扇门**，不是"查不到就拦"。）
    """
    db = _db()
    _pin(monkeypatch, db)
    try:
        gs = GuardState()
        gs.conversation_id = "t-clean"

        ran = []

        async def any_tool(student_id: str = ""):
            """Fake tool."""
            ran.append(student_id)
            return json.dumps({"ok": True})

        guarded = _make_guarded_tool(any_tool, gs, requires_approval=False)
        out = json.loads(_run(guarded.ainvoke({"student_id": "241914"})))

        assert ran == ["241914"], f"没门却被拦：{out}"
    finally:
        db.close()


def test_a_closed_gate_stops_blocking(monkeypatch):
    """门关掉之后闸要抬起 —— 否则老师处理完卡片，agent 照样动不了。"""
    db = _db()
    _pin(monkeypatch, db)
    try:
        with wu.work_unit(db):
            gid = gate_service.open_gate(db, "t-close", kind="boolean",
                                         tool_name="delete_bank", kwargs={"bank_id": "b1"})
        with wu.work_unit(db):
            gate_service.close_gate(db, gid, "executed")

        gs = GuardState()
        gs.conversation_id = "t-close"

        ran = []

        async def any_tool(student_id: str = ""):
            """Fake tool."""
            ran.append(student_id)
            return json.dumps({"ok": True})

        guarded = _make_guarded_tool(any_tool, gs, requires_approval=False)
        out = json.loads(_run(guarded.ainvoke({"student_id": "241914"})))

        assert ran == ["241914"], f"门已关却还在拦：{out}"
    finally:
        db.close()
