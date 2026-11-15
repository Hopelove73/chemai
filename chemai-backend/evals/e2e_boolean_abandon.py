"""端到端验收：布尔门 abandon / reject（S2 —— 审查 C1 根治后的行为实测）。

复用 e2e_long_chain 的影子库夹具（ADR-0026：自起 uvicorn 指影子副本，真调
DeepSeek，跑完关掉、绝不写 chemai.db）。两个场景各开一条会话：

  A. abandon —— 「布置一天自适应练习」撞开布尔门后，走真实点击路径
     /resume 发 {action: abandon}。S2 之前这里是**静默空操作**
     （LangGraph 对无挂起图的 Command(resume) 不抛错也不执行）：门不关、
     账不记、步永远停在等待确认。S2 必须做到四件事：门落 cancelled、账本
     恰好一行 cancelled(kind=boolean)、绑定步取消、agent 续接收尾。
  B. reject —— 同场景发 {action: reject, feedback}。ADR-0031 布尔门语义：
     不执行、说一句为什么、不修订不重开；反馈进账本 detail。

用法：
    cd chemai-backend
    python evals/e2e_boolean_abandon.py        # 退出码 0 = 全绿
"""
import json
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from e2e_long_chain import (  # noqa: E402
    MAX_ROUNDS, TEACHER_PWD, TEACHER_USER,
    _db, _open_gate, _post, _run_row, _stream, start_shadow_server,
)

GOAL_SHORT = "帮我给陈湘旻布置一天的自适应练习题，先诊断她的薄弱点再布置。"


def _decide(base, token, cid, gate, action, feedback=""):
    return _stream(base, "/api/agent/chat/langgraph/resume",
                   {"persona": "teacher", "message": "approved" if action == "approve"
                    else "cancelled",
                    "conversation_id": cid, "resume": True,
                    "file_access": "workspace", "workspace": "",
                    "decision": {"action": action, "feedback": feedback,
                                 "draft_id": gate.get("draft_id") or "",
                                 "gate_id": gate["gate_id"]}},
                   token, None)


def _history(base, token, cid):
    """历史投影接口 —— 前端卡片的唯一真源读取面（待批卡/终态卡都从这里来）。"""
    import urllib.request as _u
    req = _u.Request(base + f"/api/agent/chat/history/{cid}",
                     headers={"Authorization": f"Bearer {token}"})
    with _u.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8", "replace"))


def _run_scenario(base, shadow_home, token, cid, action, failures, notes):
    """一个场景：发话 → 布尔门出现时按 action 决定 → 其余门 approve → 断言。

    断言三层：库终态（门/账本/步）、**决定流 SSE**（前端真正渲染的事件）、
    历史投影 + 幽灵门探针（哑门/复活卡/会话卡死这三个老师可见症状的防线）。
    """
    target_gid = None
    decision_events = []
    _stream(base, "/api/agent/chat/langgraph/stream",
            {"persona": "teacher", "message": GOAL_SHORT, "provider": "deepseek",
             "conversation_id": cid, "file_access": "workspace"}, token, None)
    for rnd in range(2, MAX_ROUNDS + 1):
        time.sleep(2)  # 门行落库与上一轮流式收尾之间留一口气
        gate = _open_gate(shadow_home, cid)
        if not gate:
            notes.append(f"[{action}] 轮{rnd} 无门，收敛")
            break
        if gate["kind"] == "boolean" and target_gid is None:
            target_gid = gate["gate_id"]
            fb = "题量太多了，先不要布置" if action == "reject" else ""
            notes.append(f"[{action}] 轮{rnd} 布尔门 {target_gid} "
                         f"(tool={gate['tool_name']}) → 发 {action}")
            decision_events = _decide(base, token, cid, gate, action, fb)
            continue  # 决定后模型续接，可能收尾或撞下一扇门
        notes.append(f"[{action}] 轮{rnd} 门 {gate['gate_id']}"
                     f"(tool={gate['tool_name']}) → approve 放行")
        _decide(base, token, cid, gate, "approve")
    else:
        failures.append(f"[{action}] 门循环超过 {MAX_ROUNDS} 轮未收敛")
    if target_gid is None:
        failures.append(f"[{action}] 没有撞到布尔门（场景没建立，检查模型行为）")
        return target_gid

    # ── S2 断言 ──
    con = _db(shadow_home)
    try:
        g = con.execute("SELECT status, kind, tool_name FROM agent_gates "
                        "WHERE gate_id=?", (target_gid,)).fetchone()
        if not g or g["status"] != "cancelled":
            failures.append(f"[{action}.A1] 门没落 cancelled：{dict(g) if g else None}")
        led = con.execute(
            "SELECT decision, kind, actor_user_id, detail FROM agent_gate_decisions "
            "WHERE gate_id=?", (target_gid,)).fetchall()
        if len(led) != 1:
            failures.append(f"[{action}.A2] 账本应恰好 1 行，实际 {len(led)}")
        else:
            r = led[0]
            want = "rejected" if action == "reject" else "cancelled"
            if r["decision"] != want or (r["kind"] or "") != "boolean":
                failures.append(f"[{action}.A2] 账本口径错：{dict(r)}（应 {want}/boolean）")
            if action == "reject" and "题量" not in (r["detail"] or ""):
                failures.append(f"[{action}.A2] reject 的反馈没进 detail：{r['detail']!r}")
            if not r["actor_user_id"]:
                failures.append(f"[{action}.A2] 账本缺批准人（谁决定的无从回答）")
    finally:
        con.close()

    run = _run_row(shadow_home, cid)
    if run is None:
        # 模型本轮直答未拆计划（LLM 非确定性）：门生命周期断言（A1/A2/A4/A6/A7）
        # 仍全量生效，步级断言只对「有计划」的轮次有意义，降级为提示。
        notes.append(f"[{action}.A3] 本轮模型未走计划路径（无 plan_run），步级断言跳过")
    else:
        for s in run["steps"]:
            notes.append(f"[{action}]   step{s['step']} [{s['status']:>20}] {s['skill']}")
        stuck = [s["skill"] for s in run["steps"]
                 if s["status"] in ("pending", "running", "awaiting_confirmation")]
        if stuck:
            failures.append(f"[{action}.A3] 步骤卡住未到终态: {stuck}")
        if run["status"] not in ("completed",):
            # abandon/reject 后模型可能收尾对账把剩余步标 cancelled —— run 落
            # completed；若它停了，这里红。
            failures.append(f"[{action}.A3] run.status={run['status']}（应 completed）")

    if _open_gate(shadow_home, cid):
        failures.append(f"[{action}.A4] 仍有开着的门")
    con = _db(shadow_home)
    try:
        stuck_exec = con.execute(
            "SELECT gate_id FROM agent_gates WHERE conversation_id=? "
            "AND status='executing'", (cid,)).fetchall()
        if stuck_exec:
            failures.append(f"[{action}.A4] 有门卡在 executing: "
                            f"{[r['gate_id'] for r in stuck_exec]}")
    finally:
        con.close()

    # ── A5 决定流 SSE（前端真正渲染的事件面）──
    # 不该出现 error 事件（「这次确认已经处理过了」家族），不该把已处置的门
    # 再发一遍 gate_open（旧卡复活），且步取消必须以 plan_progress 可见
    # （计划卡显示「已放弃」而不是永远「执行中」）。
    errs = [e for e in decision_events if e.get("type") == "error"]
    if errs:
        failures.append(f"[{action}.A5] 决定流出现 error 事件："
                        f"{[e.get('message') for e in errs[:2]]}")
    revived = [e for e in decision_events
               if e.get("type") == "gate_open" and e.get("gate_id") == target_gid]
    if revived:
        failures.append(f"[{action}.A5] 已处置的门在流里复活（gate_open 重发）")
    if run is not None and not any(
            e.get("type") == "plan_progress" and e.get("status") == "cancelled"
            for e in decision_events):
        failures.append(f"[{action}.A5] 流里没有步取消事件（计划卡不会显示已放弃）")

    # ── A6 历史投影（前端卡片的唯一真源读取面）──
    # 待批卡必须消失（哑门防线）、终态卡必须在（账本 → closed_gates → 前端
    # 复用同一张卡的终态渲染，ADR-0031）。
    try:
        h = _history(base, token, cid)
        if (h.get("gate") or {}).get("gate_id"):
            failures.append(f"[{action}.A6] 历史里仍有待批门（哑门）: "
                            f"{h['gate']['gate_id']}")
        closed = {c.get("gate_id"): c for c in (h.get("closed_gates") or [])}
        card = closed.get(target_gid)
        want = "rejected" if action == "reject" else "cancelled"
        if not card:
            failures.append(f"[{action}.A6] 历史缺终态卡（门凭空消失而非显示处置）")
        elif card.get("decision") != want:
            failures.append(f"[{action}.A6] 终态卡决定口径错："
                            f"{card.get('decision')}（应 {want}）")
        notes.append(f"[{action}] A6 终态卡: "
                     + json.dumps(closed.get(target_gid), ensure_ascii=False)[:160])
    except Exception as e:  # noqa: BLE001
        failures.append(f"[{action}.A6] 历史接口读失败: {e}")

    # ── A7 幽灵门探针：处置完再发一条会撞同类门的消息 ──
    # S2 之前的症状：门没关 → C1-Q3 全拦非只读工具 → 老师看到「已暂缓·等待
    # 确认」卡，越重试越死。现在必须能开出**新**门（且流里不出现暂缓码）。
    probe = _stream(base, "/api/agent/chat/langgraph/stream",
                    {"persona": "teacher",
                     "message": "再给陈湘旻布置一天的自适应练习题。",
                     "provider": "deepseek", "conversation_id": cid,
                     "file_access": "workspace"}, token, None)
    blocked = [e for e in probe
               if "waiting_for_gate" in json.dumps(e, ensure_ascii=False)
               or "gate_open_blocked" in json.dumps(e, ensure_ascii=False)]
    if blocked:
        failures.append(f"[{action}.A7] 后续消息撞上幽灵门（已暂缓卡）: {len(blocked)} 处")
    new_gate = _open_gate(shadow_home, cid)
    if new_gate:
        notes.append(f"[{action}] A7 探针开出新门 {new_gate['gate_id']} → approve 收口")
        _decide(base, token, cid, new_gate, "approve")
        if _open_gate(shadow_home, cid):
            failures.append(f"[{action}.A7] 探针收口后仍有开门")
    else:
        notes.append(f"[{action}] A7 探针未开新门（模型直接答复，未被拦即通过）")
    return target_gid


def main() -> int:
    print("=" * 72)
    print("端到端验收：布尔门 abandon / reject（S2 · 审查 C1 根治）")
    print("=" * 72)
    base, shadow_home, proc = start_shadow_server()
    print(f"[准备] 影子库 {shadow_home}\n[准备] 服务   {base}")
    failures, notes = [], []

    try:
        st, d = _post(base, "/api/auth/login",
                      {"username": TEACHER_USER, "password": TEACHER_PWD})
        token = d.get("token", "")
        if not token:
            print(f"[FATAL] 登录失败 HTTP {st}")
            return 1

        for action in ("abandon", "reject"):
            print(f"\n───── 场景 {action.upper()} ─────")
            st, conv = _post(base, "/api/agent/chat/new?prefix=t-", {}, token)
            cid = conv.get("thread_id", "")
            _run_scenario(base, shadow_home, token, cid, action, failures, notes)
            for n in notes:
                print(f"  {n}")
            notes.clear()
    except Exception as e:  # noqa: BLE001
        failures.append(f"EXC {type(e).__name__}: {e}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except Exception:
            proc.kill()

    print("\n" + "=" * 72)
    if failures:
        print(f"结果：红 —— {len(failures)} 条断言未过")
        for f in failures:
            print(f"  ✗ {f}")
        print(f"  服务端日志：{Path(shadow_home, 'server.log')}")
        return 1
    print("结果：绿 —— 布尔门 abandon/reject 端到端成立")
    return 0


if __name__ == "__main__":
    sys.exit(main())
