"""苏格拉底三层协议 eval（批 2 S5a / REQ-SHP-001..003）—— E2E E8 的 LLM 跑分。

场景真源: evals/socratic_hint_protocol.yaml（10 场景 × judge_rules）。
断言对象是 agent 行为（首回合是否探状态、是否单步推进、何时给答案），
不是文本措辞 —— 措辞由 judge 的语义判定吸收。

跑分: 每个场景把 history 交给 persona=student 的 v2 agent（真实身份解析，
ADR-0014），取最终回复，逐条 judge_rules 用 LLM 判官做二元判定。agent 与
判官都走 httpx 出口 → --tapes record/replay 全覆盖（ADR-0023）。

用法:
    python evals/run_evals.py --slow --only socratic_protocol                      # live
    python evals/run_evals.py --slow --only socratic_protocol --tapes record       # 真打并录
    python evals/run_evals.py --slow --only socratic_protocol --tapes replay       # 离线复现
"""
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# script 条目不经过根 conftest —— 与 run_evals.py 同款影子库兜底（ADR-0026）
from app.shadow_data import activate as _activate_shadow  # noqa: E402

_activate_shadow()

from evals.conftest import EvalReport  # noqa: E402
from evals.test_langgraph_agent import _reset_checkpointers, run_langgraph_agent  # noqa: E402

SCENARIO_YAML = Path(__file__).resolve().parent / "socratic_hint_protocol.yaml"


def load_scenarios() -> list:
    import yaml
    data = yaml.safe_load(SCENARIO_YAML.read_text(encoding="utf-8"))
    return data.get("scenarios", [])


def judge_rule(rule: str, history: list, reply: str) -> dict:
    """单条协议规则的二元判定。走 llm_service（httpx 出口）→ 磁带可录放。"""
    from app.services.llm_service import llm_service
    dialog = "\n".join(f"[{m.get('role', 'user')}] {m.get('content', '')}"
                       for m in history)
    prompt = (
        "你是严格的评估判官。依据完整对话上下文，判断【助手最新回复】是否满足协议规则。\n\n"
        f"对话历史:\n{dialog}\n\n"
        f"助手最新回复:\n{reply}\n\n"
        f"协议规则: {rule}\n\n"
        '只返回 JSON: {"pass": true, "reason": "一句话依据"} 的形式，pass 为布尔值。'
    )
    result = llm_service.generate_text(
        prompt,
        system_prompt="你是苛刻但公正的评估判官。只输出 JSON，不输出任何其他文字。",
        temperature=0.0,
    )
    if not result.get("success"):
        return {"pass": False, "reason": f"judge 调用失败: {result.get('error', '未知')}"}
    content = result.get("content", "")
    try:
        if "```json" in content:
            content = content.split("```json")[1].split("```")[0]
        start, end = content.find("{"), content.rfind("}")
        data = json.loads(content[start:end + 1])
        return {"pass": bool(data.get("pass")), "reason": str(data.get("reason", ""))}
    except (ValueError, AttributeError) as exc:
        return {"pass": False, "reason": f"judge 输出不可解析: {exc}: {content[:120]}"}


def test_socratic_protocol(report: EvalReport):
    scenarios = load_scenarios()
    if not scenarios:
        print("\n  [SKIP] socratic_hint_protocol.yaml 无场景")
        return

    async def run():
        for scenario in scenarios:
            sid = scenario["id"]
            desc = scenario.get("description", "")
            print(f"\n  [{sid}] {desc[:50]}...", end=" ", flush=True)

            history = scenario.get("history", [])
            if not history:
                report.add(f"socratic: {sid}", False, "场景缺少 history")
                continue
            # history 的最后一条是本轮用户消息，其余作为先前对话注入
            msg = history[-1].get("content", "")
            prior = history[:-1]

            traj = await run_langgraph_agent(
                msg, scenario.get("persona", "student"), conversation=prior)
            if traj.get("error"):
                report.add(f"socratic: {sid}", False, f"AGENT ERROR: {traj['error']}")
                print("ERROR")
                continue
            reply = traj.get("output", "")
            if not reply.strip():
                report.add(f"socratic: {sid}", False, "agent 回复为空")
                print("EMPTY")
                continue

            all_pass = True
            for rule in scenario.get("judge_rules", []):
                verdict = judge_rule(rule, history, reply)
                ok = verdict["pass"]
                if not ok:
                    all_pass = False
                report.add(f"socratic: {sid} :: {rule[:40]}", ok,
                           f"{verdict['reason']} | 回复片段: {reply[:120]}")
            print("PASS" if all_pass else "FAIL", f"({traj['latency_s']}s)")

    import asyncio
    asyncio.run(run())
    _reset_checkpointers()


def run() -> EvalReport:
    report = EvalReport()
    print("\n" + "=" * 60)
    print("  SOCRATIC HINT PROTOCOL (LLM judge, REQ-SHP-001..003)")
    print("=" * 60)
    test_socratic_protocol(report)
    print(report.summary())
    return report


if __name__ == "__main__":
    rep = run()
    failures = sum(1 for r in rep.results if not r["passed"])
    sys.exit(1 if failures > 0 else 0)
