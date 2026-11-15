"""确认门存量对账（gate-lifecycle 批次 3.2）—— 一次性，可重复跑。

用法:
    python scripts/db_checkpoint.py            # ← 先跑这个（仓库纪律）
    python scripts/repair_gates.py --dry-run   # 先看要动什么
    python scripts/repair_gates.py             # 真跑
    python scripts/db_checkpoint.py            # 跑完再 checkpoint

**它调的就是 T17 那个每日作业的那几个函数**（`app/jobs/handlers.py`），
只是从命令行手动跑一轮、并且能先看后动。两份实现必然漂开，所以这里一行业务逻辑都不写。

它会做：① 收敛**假门**（按判据不算数却还挂着 open 的行）② 对账**卡死的 run**。
T17 还有第三件（作废超期未决的门），这里**刻意不跑** —— 手动跑一轮时顺手把老师
手上正在处理的卡片收掉，是最不该发生的事。要收那些，等每日作业满 14 天。

**也不动那批「算数」的开门。** 它们不是垃圾，是**真实的待老师决定项**
（2026-10-17 实测：41 扇算数门全是 t- 老师会话、最近 1–3 天产生，
工具分布 assign_adaptive_practice 15 / share_artifact 14 / read 6 / …）。
脚本替老师作废它们，等于替老师扔卡片 —— 它们的出路是待确认页，或 T17 的过期作业。
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def _report(db, tag: str) -> tuple:
    from app.models.database import AgentGate
    from app.services.gate_service import pending_gate_criteria
    total = db.query(AgentGate).filter(AgentGate.status == "open").count()
    counting = db.query(AgentGate).filter(
        AgentGate.status == "open", *pending_gate_criteria()).count()
    print(f"[{tag}] open 门 {total}：算数 {counting}、不算数 {total - counting}")
    return total, counting


def _stuck(db) -> int:
    from app.models.database import PlanRun
    from app.services.gate_service import gate_service
    from app.services.plan_run_service import _STEP_TERMINAL
    n = 0
    for run in db.query(PlanRun).filter(PlanRun.status.in_(("running", "paused"))).all():
        if gate_service.get_open_gate(db, run.conversation_id):
            continue
        if any(s.get("status") not in _STEP_TERMINAL for s in (run.steps or [])):
            n += 1
    return n


def main() -> int:
    ap = argparse.ArgumentParser(description="确认门存量对账（一次性，可重复跑）")
    ap.add_argument("--dry-run", action="store_true", help="只报告，不写库")
    args = ap.parse_args()

    from app.models.database import get_db
    from app.jobs.handlers import reconcile_plan_runs, supersede_stale_gates_full

    db = next(get_db())
    try:
        _total, counting = _report(db, "前")
        stuck_before = _stuck(db)
        print(f"[前] 卡死的 run {stuck_before} 个")
    finally:
        db.close()

    if args.dry_run:
        print("\n--dry-run：以上是要动的，没有写库。")
        return 0

    db = next(get_db())
    try:
        print(f"[①] 假门收敛 {supersede_stale_gates_full(db)} 行 → superseded")
        out = reconcile_plan_runs(db)
        print(f"[②] 对账 run {len(out['settled'])} 个"
              f"（另有 {out['skipped_waiting_on_gate']} 个在等老师，未动）")
        # 回读校验在 `reconcile_plan_runs` 里做过了；这里再把它的结论摆出来
        if out["unfinished"]:
            print(f"[②] ⚠ 回读发现 {len(out['unfinished'])} 个 run 仍未终态："
                  f"{out['unfinished'][:3]}")
        else:
            print("[②] 回读校验通过：该收的 run 已无未终态步骤")
    finally:
        db.close()

    db = next(get_db())
    try:
        _total, counting_after = _report(db, "后")
        print(f"     算数的 {counting_after} 扇是**真实的待决定项**，脚本刻意不碰 —— "
              f"交给待确认页或 T17 的 14 天过期作业。")
    finally:
        db.close()
    print("\n跑完记得：python scripts/db_checkpoint.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
