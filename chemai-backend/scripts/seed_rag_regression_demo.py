"""制造一次可观测的回归：禁用某个金标题的全部期望块 → 跑评测 → 对比 → 恢复。

结论必须真实：评测真的会把这题判为失败，对比页真的显示「弄坏了 1 题」。
"""
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
BASE = "http://127.0.0.1:8001"


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


def main():
    token = call("/api/auth/login", "POST",
                 {"username": "admin", "password": "admin123"})["token"]

    runs = call("/api/rag/evals", token=token)["runs"]
    baseline = next(r["run_id"] for r in runs
                    if r["status"] == "succeeded" and (r.get("metrics") or {}).get("total", 0) >= 10)

    golden = call("/api/rag/golden-questions", token=token)["questions"]
    target = min((g for g in golden if g["status"] == "active" and g["expected_chunks"]),
                 key=lambda g: len(g["expected_chunks"]))
    ids = [c["chunk_id"] for c in target["expected_chunks"]]
    print(f"目标金标 #{target['id']}：{target['question']}（{len(ids)} 个期望块）")
    for cid in ids:
        call(f"/api/kb/chunks/{urllib.parse.quote(cid, safe='')}/status",
             "POST", {"disabled": True}, token)
        print(f"  禁用 {cid}")

    r = call("/api/rag/evals/run", "POST", {}, token)
    if "_error" in r:
        raise SystemExit(r)
    rid = r["run_id"]
    print(f"评测启动 {rid}")
    for _ in range(90):
        time.sleep(2)
        got = call(f"/api/rag/evals/{rid}", token=token)["run"]
        if got["status"] in ("succeeded", "failed"):
            print("结果:", got["metrics"])
            break
    else:
        raise SystemExit("超时")

    cmp = call(f"/api/rag/eval-comparison?baseline={baseline}&candidate={rid}", token=token)
    d = cmp["diff"]
    print(f"对比：弄坏 {len(d['new_fail'])} / 修好 {len(d['fixed'])} / 仍通过 {d['still_pass_count']}")
    for x in d["new_fail"]:
        print(f"  弄坏了 #{x['id']} {x.get('question', '')}")

    for cid in ids:
        call(f"/api/kb/chunks/{urllib.parse.quote(cid, safe='')}/status",
             "POST", {"disabled": False}, token)
    print("已恢复全部分块；baseline =", baseline, " candidate =", rid)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
