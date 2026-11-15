"""陈旧门草稿 id 不得挂到后续步骤（安全门加固 Commit ②F）。

`_last_gate_draft_id` 只在工具结果带 approval_needed 时更新；一个不带
approval_needed、却被已打开的门拦下（gate_open_blocked）而进入 awaiting 的
步骤，会继承**上一扇门**的 draft_id 写进自己的步 JSON。服务重启按步上的
gate_draft_id 重建门卡（ADR-0013）时就会指向错误草稿。

修法：每个 tool_end 开头清零，只有本轮真正开门的工具写回自己的 draft_id。

2026-10-16 补（gate-lifecycle 1.7）：拦下路径现在**根本不产生步转移**，
上面那条通道已经不存在 —— 用例随之改为钉住那个更强的性质（第二步纹丝不动、
不带任何 draft_id）。清零那半仍然要留着：它是通用的卫生规则，不是只为这条通道。
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import Base
import app.models.database as db_mod
from app.services.plan_run_service import plan_run_service
from agent.langgraph_sse_v2 import LangGraphSSEAdapterV2


def _patch_db(monkeypatch):
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool,
    )
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


def _tool_end(adapter, name, payload):
    return adapter.feed({
        "event": "on_tool_end", "name": name,
        "data": {"output": json.dumps(payload, ensure_ascii=False)},
    })


def _step(run_steps, n):
    return next(s for s in run_steps if s["step"] == n)


def test_gate_open_blocked_step_does_not_inherit_stale_draft_id(monkeypatch):
    Session = _patch_db(monkeypatch)
    db = Session()
    try:
        plan_run_service.start_run(db, "conv-f", "计划", [
            {"step": 1, "skill": "send_learning_plan", "description": "发计划"},
            {"step": 2, "skill": "delete_bank", "description": "删题库"},
        ])
    finally:
        db.close()

    adapter = LangGraphSSEAdapterV2(conversation_id="conv-f")
    # 用持久化 run 的步骤初始化内存态（模拟上一流已有活跃 run）
    db = Session()
    try:
        adapter._plan_steps = [dict(s) for s in plan_run_service.get_active_run(db, "conv-f")["steps"]]
    finally:
        db.close()
    adapter._plan_active = True

    # 1) 第一扇门：send_learning_plan 生成 approval_needed，携带 draft_id D1
    _tool_end(adapter, "send_learning_plan", {
        "approval_needed": True, "action": "send_student", "draft_id": "D1",
    })
    db = Session()
    try:
        run = plan_run_service.get_active_run(db, "conv-f")
        assert _step(run["steps"], 1)["status"] == "awaiting_confirmation"
        assert _step(run["steps"], 1).get("gate_draft_id") == "D1", "第一扇门应挂 D1"
    finally:
        db.close()

    # 2) delete_bank 被**已打开的门**拦下（gate_open_blocked，自身不带 draft_id）：
    #    拦下路径不产生任何步转移，所以第二步**不进**等待态 —— 泄漏的通道本身没了。
    #
    #    ⚠️ 2026-10-16 语义变更（gate-lifecycle 1.7）：原用例让第二步进
    #    awaiting_confirmation 再断言它没继承 D1。现在这条更强：它压根不动。
    _tool_end(adapter, "delete_bank", {"gate_open_blocked": True})
    db = Session()
    try:
        run = plan_run_service.get_active_run(db, "conv-f")
        s2 = _step(run["steps"], 2)
        assert s2["status"] == "pending", f"被门拦下不得改动步骤状态：{s2}"
        assert not s2.get("gate_draft_id"), f"第二步不该带任何草稿 id：{s2!r}"
        # 第一扇门不受影响，D1 仍在它该在的那一步上
        assert _step(run["steps"], 1).get("gate_draft_id") == "D1"
    finally:
        db.close()
