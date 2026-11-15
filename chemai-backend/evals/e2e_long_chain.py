"""端到端验收：教师长链路（找学生 → 诊断 → 学习方案 → 布置练习 → 同步家长）。

存在理由
────────
2026-10-16 实测：老师那句原话（「帮我找一下陈湘旻，看看她最近的学习情况，然后针对她
的问题给她出一个专门的个性化学习方案，顺便给她先布置一天的练习题，然后把她的学习
情况同步给她的家长」）在真环境里**跑不通**——卡在第 5 步、家长通知 0、run 永远 running。

这个文件把「跑不通」变成一个**可执行的断言**：它红着，说明有活干；它绿了，才算干完。
它是本次修复的锚，不是事后补的测试。

它驱动的是**老师真实点击的路径**（不是内部函数）：
    1. 发原话 → agent 跑到第一扇门停下
    2. 老师确认：走门决定处理器 `/resume` —— 聊天里的内联卡就是这条
       （gate-lifecycle 2 / 方案 a：聊天内联卡与聊天确认卡同一条决定通路，
       因为只有 `/resume` 的重放能让 agent 续接）
    3. 重复直到没有门，或达到轮次上限
    4. 从库里断言终态

ADR-0026
────────
跑在 `app.shadow_data` 建的**影子副本**上：脚本自己起一个 uvicorn（随机端口）指向影子库，
跑完关掉。绝不写被 git 跟踪的 `chemai.db`。

用法
────
    cd chemai-backend
    python evals/e2e_long_chain.py            # 退出码 0 = 全绿，1 = 有断言红

代价：真调 LLM（DeepSeek），单次几分钟。所以它不在 `--fast` 零花费层里。
"""
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

STUDENT_ID = "241914"          # 陈湘旻
STUDENT_NAME = "陈湘旻"
TEACHER_USER, TEACHER_PWD = "zhou", "zhou123456"
GOAL = ("帮我找一下陈湘旻，看看她最近的学习情况，然后针对她的问题给她出一个专门的"
        "个性化学习方案，顺便给她先布置一天的练习题，然后把她的学习情况同步给她的家长。")
MAX_ROUNDS = 12                # 门循环上限：正常这条链路 2~4 扇门
TURN_TIMEOUT = 600             # 单轮流式上限（秒）
READY_TIMEOUT = 120


# ────────────────────────── 影子服务 ──────────────────────────

def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def start_shadow_server():
    """建影子 + 起服务，返回 (base_url, shadow_home, proc)。

    服务端日志落 `{shadow_home}/server.log`，不再丢进 DEVNULL：链路跑不通时
    「服务端到底做了什么」和「模型说了什么」一样是证据。2026-10-17 查 2.11
    （网关工具清单）时，要问的是「下发给模型的工具名有哪几件」，那答案只在
    服务端 —— 顺带发现 agent 层的 logger.info 因 root 停在 WARNING 而从未打过，
    故这里显式把级别开到 INFO。
    """
    from app.shadow_data import create
    home, env = create()
    port = _free_port()
    base = f"http://127.0.0.1:{port}"
    child_env = dict(os.environ)
    child_env.update(env)
    child_env["PYTHONIOENCODING"] = "utf-8"
    child_env.setdefault("CHEMAI_LOG_LEVEL", "INFO")
    log_file = open(Path(home, "server.log"), "w", encoding="utf-8")
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(PROJECT_ROOT), env=child_env,
        stdout=log_file, stderr=subprocess.STDOUT,
    )
    for _ in range(READY_TIMEOUT):
        try:
            with urllib.request.urlopen(base + "/docs", timeout=2):
                return base, home, proc
        except Exception:
            if proc.poll() is not None:
                raise RuntimeError("影子服务起不来（进程已退出）")
            time.sleep(1)
    proc.terminate()
    raise RuntimeError(f"影子服务 {READY_TIMEOUT}s 内没就绪")


# ────────────────────────── HTTP / SSE ──────────────────────────

def _post(base, path, body, token=None, timeout=60):
    data = json.dumps(body).encode("utf-8") if body is not None else b""
    hdr = {"Content-Type": "application/json"}
    if token:
        hdr["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(base + path, data=data, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode("utf-8", "replace")
            return r.status, (json.loads(raw) if raw.strip() else {})
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except json.JSONDecodeError:
            return e.code, {"_raw": raw[:300]}


def _stream(base, path, body, token, on_event=None):
    """读一条 SSE 流，返回事件列表。"""
    req = urllib.request.Request(
        base + path, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {token}"})
    events = []
    with urllib.request.urlopen(req, timeout=TURN_TIMEOUT) as r:
        for line in r:
            s = line.decode("utf-8", "replace").rstrip("\n")
            if not s.startswith("data: "):
                continue
            p = s[6:].strip()
            if p in ("[DONE]", ""):
                continue
            try:
                e = json.loads(p)
            except json.JSONDecodeError:
                continue
            events.append(e)
            if on_event:
                on_event(e)
    return events


# ────────────────────────── 影子库只读查询 ──────────────────────────

def _db(shadow_home):
    con = sqlite3.connect(f"file:{Path(shadow_home, 'chemai.db').as_posix()}?mode=ro",
                          uri=True)
    con.row_factory = sqlite3.Row
    return con


def _open_gate(shadow_home, cid):
    con = _db(shadow_home)
    try:
        row = con.execute(
            "SELECT gate_id, kind, tool_name, action, draft_id FROM agent_gates "
            "WHERE conversation_id=? AND status='open' ORDER BY id DESC LIMIT 1",
            (cid,)).fetchone()
        return dict(row) if row else None
    finally:
        con.close()


def _run_row(shadow_home, cid):
    con = _db(shadow_home)
    try:
        row = con.execute(
            "SELECT status, steps FROM plan_runs WHERE conversation_id=?", (cid,)
        ).fetchone()
        if not row:
            return None
        return {"status": row["status"], "steps": json.loads(row["steps"] or "[]")}
    finally:
        con.close()


# ────────────────────────── 主流程 ──────────────────────────

def main() -> int:
    print("=" * 72)
    print("端到端验收：教师长链路")
    print("=" * 72)
    base, shadow_home, proc = start_shadow_server()
    server_log = Path(shadow_home, "server.log")
    print(f"[准备] 影子库 {shadow_home}")
    print(f"[准备] 服务   {base}")
    print(f"[准备] 日志   {server_log}")

    failures = []
    trace = []

    def note(msg):
        print(msg, flush=True)
        trace.append(msg)

    try:
        st, d = _post(base, "/api/auth/login",
                      {"username": TEACHER_USER, "password": TEACHER_PWD})
        token = d.get("token", "")
        if not token:
            print(f"[FATAL] 登录失败 HTTP {st}: {d}")
            return 1
        note(f"[登录] {d.get('user', {}).get('name')}")

        st, conv = _post(base, "/api/agent/chat/new?prefix=t-", {}, token)
        cid = conv.get("thread_id", "")
        note(f"[会话] {cid}")

        _said: list = []

        def _flush_said():
            """把模型这一段说的话打出来 —— 诊断「它为什么这么做」要的就是这个。

            2026-10-17 加：查 2.9（重放后偏离计划）时只看得见工具调用，
            看不见模型自己的理由，只能靠推。
            """
            s = "".join(_said).strip().replace("\n", " ")
            _said.clear()
            if s:
                note(f"   ... {s[:500]}")

        def show(e):
            t = e.get("type")
            if t == "text":
                _said.append(str(e.get("content") or ""))
                return
            if t == "tool_call":
                _flush_said()
                note(f"   >>> {e.get('name')}")
            elif t == "gate_open":
                _flush_said()
                note(f"   *** 门开 {e.get('gate_id')} action={e.get('action')}")
            elif t == "error":
                _flush_said()
                note(f"   !!! {e.get('message')}")

        # ── 第 1 轮：发原话 ──
        note("[轮 1] 发送老师原话")
        _stream(base, "/api/agent/chat/langgraph/stream",
                {"persona": "teacher", "message": GOAL, "provider": "deepseek",
                 "conversation_id": cid, "file_access": "workspace"},
                token, show)
        _flush_said()

        # ── 门循环：老师逐扇确认 ──
        for rnd in range(2, MAX_ROUNDS + 1):
            gate = _open_gate(shadow_home, cid)
            if not gate:
                note(f"[轮 {rnd}] 没有开着的门，停止")
                break
            note(f"[轮 {rnd}] 确认门 {gate['gate_id']} (tool={gate['tool_name']})")

            # 聊天内联卡的真实路径 = 门决定处理器（gate-lifecycle 2，方案 a）。
            #
            # 2026-10-17 改：原先这里先打 REST 内联卡端点、再打 /resume，按的是
            # 「两条入口都得能收场」。但两条入口**各有各的主人**：聊天内（有会话）
            # 走 /resume —— 只有那条重放路径能让 agent 续接；REST 端点是没有会话的
            # 那个场景（学生管理页的 DraftGatePanel）的入口。先前两条都打，等于在
            # 聊天场景下走了一条 agent 根本续接不了的路：草稿发出去了、门也关上了，
            # 但没人叫醒 agent，run 永远停在原地。
            note("   门决定处理器 /resume（聊天内联卡的真实路径）")
            # persona 必须带 —— 老师的真实点击走前端 `postStream()`，它把 persona
            # 默认成 'teacher' 并**总是**发出来（`agent-sse.ts:252`）。这里原先漏了，
            # 于是服务端吃 `ChatRequest.persona` 的默认值 'tutor'（对 teacher 角色
            # 也是合法 persona），批准之后整条链路**降级成家教**：teacher.yaml 才有
            # 的 assign_adaptive_practice / generate_parent_report 当场从工具清单里
            # 消失，模型如实汇报「这两个技能在环境里不存在」—— 2026-10-17 实测，
            # 两轮清单 46 件 vs 21 件。断在计划第 5 步的真因就在这里。
            _stream(base, "/api/agent/chat/langgraph/resume",
                    {"persona": "teacher",
                     "message": "approved", "conversation_id": cid, "resume": True,
                     "file_access": "workspace", "workspace": "",
                     "decision": {"action": "approve", "feedback": "",
                                  "draft_id": gate["draft_id"] or "",
                                  "gate_id": gate["gate_id"]}},
                    token, show)
            _flush_said()
        else:
            failures.append(f"门循环超过 {MAX_ROUNDS} 轮仍未收敛（门一直在开）")

        # ── 终态断言 ──
        print()
        print("-" * 72)
        print("终态断言")
        print("-" * 72)

        run = _run_row(shadow_home, cid)
        if run is None:
            failures.append("A0 根本没有 plan_run（长任务没被拆解）")
        else:
            by_skill = {s["skill"]: s["status"] for s in run["steps"]}
            for s in run["steps"]:
                mark = "✓" if s["status"] in ("completed",) else "✗"
                print(f"  {mark} step{s['step']} [{s['status']:22}] {s['skill']}")
            if run["status"] != "completed":
                failures.append(f"A1 run.status={run['status']}（应 completed）")
            stuck = [s["skill"] for s in run["steps"]
                     if s["status"] in ("pending", "running", "awaiting_confirmation")]
            if stuck:
                failures.append(f"A2 仍有未到终态的步骤: {stuck}")

        still_open = _open_gate(shadow_home, cid)
        if still_open:
            failures.append(f"A3 仍有开着的门: {still_open['gate_id']} "
                            f"({still_open['tool_name']})")

        # A3b：卡在 `executing` 的门也一并抓（2026-10-17 补）。
        # `claim_gate` 在执行**之前**占门，正常由 `finish_gate` 收口；两者永远在同一次
        # 请求、同一个进程里，所以跑完之后还停在 `executing` = 重放**没跑完**
        # （进程死在中间，或收口那一步出了事）。原来只查 `open` 看不见它 ——
        # 而"重放到底跑没跑"恰恰是 A5 红时第一个要分清的事：
        # 卡在 executing 是重放没跑完，落成 executed 却查无记录是重放跑了但没做成，
        # 两者处置完全不同。（这一行跑完时进程还活着，所以启动扫描救不了它。）
        con = _db(shadow_home)
        try:
            stuck = con.execute(
                "SELECT gate_id, tool_name FROM agent_gates "
                "WHERE conversation_id=? AND status='executing' ORDER BY id DESC",
                (cid,)).fetchall()
        finally:
            con.close()
        for row in stuck:
            failures.append(f"A3b 有门卡在执行中（重放没跑完）: {row['gate_id']} "
                            f"({row['tool_name']})")

        con = _db(shadow_home)
        try:
            sent = con.execute(
                "SELECT COUNT(*) FROM learning_plan_drafts WHERE student_id=? "
                "AND status='sent'", (STUDENT_ID,)).fetchone()[0]
            if not sent:
                failures.append("A4 学习方案没有 sent 的草稿")

            # 第 5 步的落点：**单个学生走同步路**（`assignment_service.assign` 里
            # `len(students) <= SYNC_LIMIT(5)`），同步路只写 `exam_records` 一行
            # （`_assign_one`：record_id = "asg_<摘要>"、type = PRACTICE、
            # `question_stats.student_id`），**根本不动 assignment_batches** ——
            # 那张表是 >5 人异步批次的进度源。
            # 原断言查 assignment_batches，等于拿「异步批次的表」验「同步路的活」：
            # 第 5 步做成了也永远查不到，把「服务端降级成 tutor」的真 bug 和这条
            # 假红混在一起，白追了一轮（2026-10-17）。
            assigned = con.execute(
                "SELECT COUNT(*) FROM exam_records WHERE record_id LIKE 'asg_%' "
                "AND question_stats LIKE ? "
                "AND created_at >= datetime('now','-30 minutes')",
                (f"%{STUDENT_ID}%",)).fetchone()[0]
            if not assigned:
                failures.append("A5 第 5 步没生效（exam_records 无该生新练习记录）")

            pnotif = con.execute(
                "SELECT COUNT(*) FROM parent_notifications WHERE student_id=? "
                "AND sent_at >= datetime('now','-30 minutes')",
                (STUDENT_ID,)).fetchone()[0]
            if not pnotif:
                failures.append("A6 家长没收到通知（parent_notifications 无新记录）")
        finally:
            con.close()

    except Exception as e:  # noqa: BLE001
        failures.append(f"EXC {type(e).__name__}: {e}")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()

    # ── 取证：把服务端「下发给模型的工具清单」抄出来 ──
    # 影子目录是 atexit 删的，所以这台证据要在退出前抄出来，否则随影子一起没了。
    # 全量日志另存一份稳定路径（`*.log` 已在 .gitignore）：链路跑不通时
    # 「服务端做了什么」和「模型说了什么」一样是证据。
    tools_lines = []
    try:
        raw_log = server_log.read_text(encoding="utf-8", errors="replace")
        kept = PROJECT_ROOT / "evals" / "_last_e2e_server.log"
        kept.write_text(raw_log, encoding="utf-8")
        server_log = kept
        for ln in raw_log.splitlines():
            if "[SingleAgent] Tools" in ln:
                tools_lines.append(ln)
    except OSError:
        pass
    # 门那一侧也抄出来（2026-10-17 补）：`plan_step` 是「门绑了哪一步」的唯一真源，
    # 而"重放完成后哪一步该收尾"全靠它。查「计划卡为什么收不了尾」第一眼看这里 ——
    # 光看工具清单只知道模型调了什么，不知道门把它记在哪一步上。
    gate_lines = []
    try:
        con = _db(shadow_home)
        try:
            for r in con.execute(
                    "SELECT gate_id, tool_name, status, plan_step FROM agent_gates "
                    "ORDER BY id").fetchall():
                gate_lines.append(
                    f"{r['gate_id']} | {r['tool_name']} | {r['status']} | plan_step={r['plan_step']}")
        finally:
            con.close()
    except Exception as e:  # noqa: BLE001
        gate_lines.append(f"(读门失败: {e})")
    if gate_lines:
        print()
        print("-" * 72)
        print("服务端取证：本轮所有确认门（plan_step = 门绑在哪一步）")
        print("-" * 72)
        for ln in gate_lines:
            print(f"  {ln}")

    if tools_lines:
        print()
        print("-" * 72)
        print("服务端取证：每轮实际下发给模型的工具清单")
        print("-" * 72)
        for ln in tools_lines:
            print(f"  {ln.strip()}")
        last = tools_lines[-1]
        for probe in ("assign_adaptive_practice", "generate_parent_report"):
            mark = "✓ 在" if probe in last else "✗ 不在"
            print(f"  {mark}清单：{probe}")

    print()
    print("=" * 72)
    if failures:
        print(f"结果：红 —— {len(failures)} 条断言未过")
        for f in failures:
            print(f"  ✗ {f}")
        print(f"  服务端日志：{server_log}")
        print("=" * 72)
        return 1
    print("结果：绿 —— 长链路端到端跑通")
    print("=" * 72)
    return 0


if __name__ == "__main__":
    sys.exit(main())
