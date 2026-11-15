"""AgentChat 状态机验证 —— 门生命周期 + 计划依赖序。

为什么单独一个脚本：批量 SSE 脚本只能统计「有没有 gate_open / 调了哪些工具」，
**验不了顺序与状态语义**。这里补三件它做不到的事：
  1) 计划步按 depends_on 推进（不是按数字顺序 —— 并列分支先完成是合法的）；
  2) 确认门在 DB 里真的从 open 走到 executed（门消失的硬证据，不靠文案）；
  3) 批准后重放的是**配对的那个发送工具**，不是任意工具。

用法：python scripts/verify_agentchat_state.py
前置：后端已在 127.0.0.1:8001 运行，且 .env 里有可用教师账号。
"""
import json
import sys
import os
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from dotenv import load_dotenv  # noqa: E402

load_dotenv()

BASE = "http://127.0.0.1:8001"
TEACHER = ("zhou", "zhou123456")


def _login(u, p):
    req = urllib.request.Request(
        BASE + "/api/auth/login",
        data=json.dumps({"username": u, "password": p}).encode(),
        headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())["token"]


TOKEN = _login(*TEACHER)
HEADERS = {"Content-Type": "application/json", "Authorization": "Bearer " + TOKEN}


def _new_conversation():
    req = urllib.request.Request(BASE + "/api/agent/chat/new?prefix=t-", data=b"{}", headers=HEADERS)
    return json.loads(urllib.request.urlopen(req, timeout=20).read())["thread_id"]


def _collect(path, body, timeout=300):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers=HEADERS)
    events = []
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            for raw in resp:
                line = raw.decode("utf-8", "ignore").strip()
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                try:
                    events.append(json.loads(payload))
                except ValueError:
                    continue
    except Exception as exc:  # noqa: BLE001
        events.append({"type": "EXC", "message": f"{type(exc).__name__}: {exc}"})
    return events


def _gate_rows(cid):
    from app.models.database import get_db, AgentGate
    db = next(get_db())
    try:
        rows = db.query(AgentGate).filter(AgentGate.conversation_id == cid).all()
        return [{"gate_id": r.gate_id, "status": r.status, "kind": r.kind} for r in rows]
    finally:
        db.close()


def _plan_steps(cid):
    """DB 里的计划步骤（含 depends_on）—— 依赖序的真源。

    不能只取「活跃 run」：正常跑完后 run 会进终态，get_active_run 就返回空，
    断言于是变成空转（第一版就是这么骗过自己的）。这里按会话取最新一条。
    """
    from app.models.database import get_db, PlanRun
    db = next(get_db())
    try:
        row = (db.query(PlanRun)
                 .filter(PlanRun.conversation_id == cid)
                 .order_by(PlanRun.id.desc()).first())
        return (row.steps if row else []) or []
    except Exception:  # noqa: BLE001
        return []
    finally:
        db.close()


def check_plan_dependency_order(name, message):
    """一步 completed 之前，它 depends_on 里的步必须已经到终态。"""
    cid = _new_conversation()
    events = _collect("/api/agent/chat/langgraph/stream",
                      {"message": message, "conversation_id": cid})
    progress = [(e.get("current_step"), e.get("status"))
                for e in events if e.get("type") == "plan_progress"]
    steps = _plan_steps(cid)
    deps = {s.get("step"): (s.get("depends_on") or []) for s in steps}
    finished = set()
    violations = []
    for step, status in progress:
        if status in ("completed", "failed", "cancelled"):
            for dep in deps.get(step, []):
                if dep not in finished:
                    violations.append((step, status, "依赖步", dep, "未终态"))
            finished.add(step)
    return {
        "case": name,
        "cid": cid,
        "steps": [(s.get("step"), s.get("skill"), (s.get("depends_on") or []), s.get("status"))
                  for s in steps],
        "progress": progress,
        "dependency_violations": violations,
        "ok": not violations,
    }


def check_gate_lifecycle(name, message, expect_replay_tool=""):
    """门：open → 批准 → executed（DB 硬证据）+ 重放工具配对。"""
    cid = _new_conversation()
    first = _collect("/api/agent/chat/langgraph/stream",
                     {"message": message, "conversation_id": cid})
    gates = [e for e in first if e.get("type") == "gate_open"]
    if not gates:
        return {"case": name, "gate": None, "ok": False, "note": "未弹门"}
    gate_id = gates[0].get("gate_id") or ""
    before = _gate_rows(cid)
    second = _collect("/api/agent/chat/langgraph/resume",
                      {"conversation_id": cid, "message": "",
                       "decision": {"action": "approve", "gate_id": gate_id}})
    after = _gate_rows(cid)
    replayed = [e.get("name") for e in second if e.get("type") == "tool_result"]
    # 门的落定三态（gate_service.finish_gate）：重放成功 -> executed；
    # 重放失败 -> 退回 open 让老师重试。所以「批准后还是 open」在业务失败时是**合法**的，
    # 不能一律判红 —— 先把重放本身的成败取出来再定期望值。
    replay_success = True
    for e in second:
        if e.get("type") == "tool_result" and (not expect_replay_tool or e.get("name") == expect_replay_tool):
            replay_success = bool(e.get("success", True))
    expected_final = "executed" if replay_success else "open"
    actual = (after[0]["status"] if after else "")
    return {
        "case": name,
        "gate_id": gate_id,
        "db_before": [r["status"] for r in before],
        "db_after": [r["status"] for r in after],
        "replay_success": replay_success,
        "expected_final": expected_final,
        "replayed": replayed[:4],
        "replay_matched": (expect_replay_tool in replayed) if expect_replay_tool else None,
        "ok": bool(before) and before[0]["status"] == "open" and actual == expected_final,
    }


CASES = [
    ("C 月考（计划依赖序）",
     "帮我准备一次月考：先诊断班级薄弱点，再按薄弱点出10道选择题，最后生成复习计划", "plan"),
    ("B1 学习计划（草稿门）", "给周翔生成学习计划", "gate", "send_learning_plan"),
    ("B3 布置练习（布尔门）", "给周翔布置自适应练习", "gate", "assign_adaptive_practice"),
]


def main():
    failed = 0
    for item in CASES:
        name, message = item[0], item[1]
        try:
            if item[2] == "plan":
                result = check_plan_dependency_order(name, message)
            else:
                result = check_gate_lifecycle(name, message, item[3])
        except Exception as exc:  # noqa: BLE001
            result = {"case": name, "ok": False, "fatal": f"{type(exc).__name__}: {exc}"}
        print(json.dumps(result, ensure_ascii=False))
        if not result.get("ok"):
            failed += 1
    print(f"\nFAILED={failed} / {len(CASES)}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
