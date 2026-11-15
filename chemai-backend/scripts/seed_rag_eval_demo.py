"""补齐 RAG 演示数据：3 条未覆盖问题（坏案例）+ 两轮评测（含一次真实回归）。

第二轮前会通过**管理 API**禁用金标依赖的一个分块（在服务进程内失效缓存），
跑完再恢复 —— 这样对比页能真实显示「弄坏了 1 题」。
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:8001"
OUT_OF_SCOPE = [
    "2026 年诺贝尔化学奖颁给了谁？",
    "大学结构化学里的群论怎么用在分子轨道上？",
    "这道竞赛题里提到的卡宾中间体稳定吗？",
]


def call(path, method="GET", body=None, token=None):
    req = urllib.request.Request(BASE + path, method=method)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    data = None
    if body is not None:
        req.add_header("Content-Type", "application/json")
        data = json.dumps(body).encode()
    try:
        with urllib.request.urlopen(req, data) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"_error": e.code, "detail": e.read().decode()[:200]}


def login():
    d = call("/api/auth/login", "POST", {"username": "admin", "password": "admin123"})
    return d["token"]


def write_miss_traces():
    from agent.tools.tutoring import _kb_resources
    from app.services.kb_context import build_kb_context
    from app.services.qa_trace import _summarize_hits
    from app.models.database import QaTrace, get_session_factory

    made = 0
    for i, q in enumerate(OUT_OF_SCOPE, start=90):
        kb_out = build_kb_context(q, **_kb_resources())
        hits = _summarize_hits(kb_out)
        s = get_session_factory()()
        try:
            if s.get(QaTrace, f"tr_seed{i}") is not None:
                made += 1
                continue
            s.add(QaTrace(
                trace_id=f"tr_seed{i}", user_id="241902", role="student",
                conversation_id=f"seed:tr_seed{i}", question=q,
                retrieval_config=kb_out.get("config"), hits=hits,
                zero_hit=len(hits) == 0,
                top1_score=hits[0]["score"] if hits else None,
                answer="（该问题超出高中化学知识库范围，助教应说明未覆盖）",
                refusal_flag=kb_out.get("mode") == "miss",
                latency_ms=1800, model_name="deepseek-flash", kb_index_version="kb_chunks",
                feedback="down", ops_label="bad", bad_reason="缺知识" if not hits else "超纲",
            ))
            s.commit()
        finally:
            s.close()
        made += 1
        print(f"  未覆盖[{i}] {q[:24]} → hits={len(hits)} mode={kb_out.get('mode')}")
    return made


def run_eval_and_wait(token, label):
    r = call("/api/rag/evals/run", "POST", {}, token)
    if "_error" in r:
        raise SystemExit(f"启动评测失败：{r}")
    rid = r["run_id"]
    print(f"  {label} 启动：{rid}（{r['questions']} 题）")
    for _ in range(120):
        time.sleep(2)
        got = call(f"/api/rag/evals/{rid}", token=token)
        st = got["run"]["status"]
        if st in ("succeeded", "failed"):
            print(f"  {label} 结束：{st} metrics={got['run']['metrics']}")
            if st == "failed":
                raise SystemExit(got["run"].get("error"))
            return rid
    raise SystemExit("评测超时")


def main():
    token = login()
    print("① 写未覆盖问题的坏案例")
    n = write_miss_traces()
    print(f"   共 {n} 条")

    print("② 基线评测（已有成功运行则复用）")
    existing = call("/api/rag/evals", token=token).get("runs", [])
    done = [r for r in existing if r["status"] == "succeeded" and (r.get("metrics") or {}).get("total", 0) >= 10]
    baseline = done[0]["run_id"] if done else run_eval_and_wait(token, "baseline")
    print(f"   baseline = {baseline}")

    print("③ 禁用金标 #1 依赖的分块（模拟一次内容事故）")
    golden = call("/api/rag/golden-questions", token=token)["questions"]
    target = next(g for g in golden if g["status"] == "active" and g["expected_chunks"])
    chunk_id = target["expected_chunks"][0]["chunk_id"]
    cid_q = urllib.parse.quote(chunk_id, safe="")
    call(f"/api/kb/chunks/{cid_q}/status", "POST", {"disabled": True}, token)
    print(f"   已禁用 {chunk_id}（金标 #{target['id']}: {target['question'][:20]}）")

    print("④ 第二轮评测")
    candidate = run_eval_and_wait(token, "candidate")

    print("⑤ 逐题对比")
    cmp = call(f"/api/rag/eval-comparison?baseline={baseline}&candidate={candidate}", token=token)
    d = cmp["diff"]
    print(f"   弄坏 {len(d['new_fail'])} 题 / 修好 {len(d['fixed'])} 题 / "
          f"仍失败 {len(d['still_fail'])} / 仍通过 {d['still_pass_count']}")
    for x in d["new_fail"]:
        print(f"   弄坏了：#{x['id']} {x.get('question', '')[:24]}")

    print("⑥ 恢复分块（演示数据不留副作用）")
    call(f"/api/kb/chunks/{urllib.parse.quote(chunk_id, safe='')}/status", "POST", {"disabled": False}, token)
    print("   已恢复")
    print(f"\n完成：baseline={baseline} candidate={candidate}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
