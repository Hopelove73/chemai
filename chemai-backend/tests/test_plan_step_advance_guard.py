"""计划步骤推进的两处守卫（安全门加固 Commit ②F：核验 agent3 #5/#6）。

#5（MED）：`compute_tool_transition` 的 `_find_step` 回退此前对「成功即打勾」
路径也生效——一个与计划无关、却成功的工具（read/bash 等）会把当前 running 步
替它打勾，计划被野工具误推进/解错步。修法：成功/失败只认同名 skill 的步骤；
回退只保留给门/拦下路径（门确实可能挂在不同名步骤上）。

#6：plan_task 出计划卡时 SSE 把首步显示成 running，但 run 落库仍是 pending。
服务重启按 DB 重建 → 首步被当成没开始。修法：落库前把内存首步置 running，
start_run 持久化真实进度。
"""
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.models.database import Base
import app.models.database as db_mod
from app.services.plan_run_service import compute_tool_transition, plan_run_service
from agent.langgraph_sse_v2 import LangGraphSSEAdapterV2


# ── #5 ──────────────────────────────────────────────────────────────

def _two_step_plan():
    return [
        {"step": 1, "skill": "generate_questions", "description": "出题", "status": "running"},
        {"step": 2, "skill": "assign_adaptive_practice", "description": "布置", "status": "pending"},
    ]


def test_unrelated_successful_tool_does_not_complete_running_step():
    """缺陷 #5：与计划无关的工具成功，绝不能替 running 的出题步打勾。"""
    steps = _two_step_plan()
    tr = compute_tool_transition(steps, "read", {"content": "文件内容", "path": "x.txt"})
    assert tr == [], f"无关工具不应产生步转移：{tr}"
    assert steps[0]["status"] == "running", "出题步不得被 read 完成"
    assert steps[1]["status"] == "pending", "布置步不得被提前启动"


def test_matching_skill_success_still_completes_and_starts_next():
    """回归护栏：真正的同名步成功仍要正常打勾并启动下一步。"""
    steps = _two_step_plan()
    tr = compute_tool_transition(steps, "generate_questions", {"ok": True, "total": 5})
    assert (1, "completed") in tr
    assert (2, "running") in tr
    assert steps[0]["status"] == "completed"
    assert steps[1]["status"] == "running"


def test_blocked_tool_does_not_touch_any_step():
    """被已开之门拦下的**不同名**工具：不做任何步转移。

    ⚠️ 2026-10-16 语义变更（gate-lifecycle 1.4/1.7）：原用例断言「回退挂到当前
    running 步」（2026-09-14 的「门不只挂同名步骤」）。那条回退是错的：计划里没有
    同名步，恰恰说明这扇门**不属于计划里的任何一步**，硬塞给一个碰巧在跑的无关步
    就是伪造归属 —— 老师看到「第 N 步等待确认」，而第 N 步压根没在等什么。
    见 tasks.md 的 0.2（端到端实测：step5 就这么卡住）。
    """
    steps = [
        {"step": 1, "skill": "send_learning_plan", "description": "发计划", "status": "running"},
        {"step": 2, "skill": "delete_bank", "description": "删题库", "status": "pending"},
    ]
    tr = compute_tool_transition(steps, "send_report_to_parent", {"gate_open_blocked": True})
    assert tr == [], f"被门拦下不得产生步转移：{tr}"
    assert steps[0]["status"] == "running", "无关步不得被改写"
    assert steps[1]["status"] == "pending"


def test_no_draft_to_send_is_not_a_failed_step():
    """`no_draft_to_send` 不是失败：那一步什么都没发，正确动作是回去生成草稿。

    2026-10 真机演示实测：第 8 步 send_report_to_parent 被标 ✗ failed，老师看到
    「发送家长报告失败」，而实际上没有任何发送尝试发生——它被新加的「没有草稿就别
    开布尔门」判据挡了回来（返回 error=no_draft_to_send）。同样不得推进/打勾。
    """
    steps = [
        {"step": 8, "skill": "send_report_to_parent", "description": "发报告", "status": "running"},
    ]
    tr = compute_tool_transition(
        steps, "send_report_to_parent", {"error": "no_draft_to_send", "recoverable": True})
    assert tr == [], f"不得产生步转移：{tr}"
    assert steps[0]["status"] == "running", "既没成功也没失败，状态保持原样"


def test_failed_unrelated_tool_does_not_fail_running_step():
    """缺陷 #5 对称面：无关工具失败也不该把 running 步标 failed。"""
    steps = _two_step_plan()
    tr = compute_tool_transition(steps, "bash", {"error": "boom"})
    assert tr == []
    assert steps[0]["status"] == "running"


# ── #6 ──────────────────────────────────────────────────────────────

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


def test_plan_task_persists_first_step_as_running(monkeypatch):
    """缺陷 #6：plan_task 后 DB run 的首步应为 running，而非停留在 pending。"""
    _patch_db(monkeypatch)
    adapter = LangGraphSSEAdapterV2(conversation_id="conv-p6")
    events = adapter.feed({
        "event": "on_tool_end", "name": "plan_task",
        "data": {"output": json.dumps({
            "goal": "出5题并布置", "total_steps": 2,
            "steps": [
                {"step": 1, "skill": "generate_questions", "description": "出题"},
                {"step": 2, "skill": "assign_adaptive_practice", "description": "布置"},
            ],
        }, ensure_ascii=False)},
    })
    db = db_mod  # patched get_db
    run = plan_run_service.get_active_run(next(db.get_db()), "conv-p6")
    assert run is not None, "plan_task 应落库活跃 run"
    assert run["steps"][0]["status"] == "running", f"首步应持久化为 running：{run['steps']}"
    assert run["steps"][1]["status"] == "pending"

    # D4：概览卡必须报真实步骤态。原先 plan_summary 把每步硬编码成 pending，
    # 与紧随其后的 plan_progress(running) 自相矛盾——按 plan_summary 做初始
    # 渲染的客户端（新界面、事件回放）会把已开工的首步显示成未开始。
    summary = next(json.loads(e) for e in events if '"plan_summary"' in e)
    assert [s["status"] for s in summary["steps"]] == ["running", "pending"], \
        f"plan_summary 应报真实步骤态：{summary['steps']}"
