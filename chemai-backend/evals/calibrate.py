"""
L2 judge calibration pipeline (Spec 04).

Usage:
    python evals/calibrate.py                       # 全部五族
    python evals/calibrate.py --family question_gen # 单族
    python evals/calibrate.py --provider fake       # 无 API 演示（脚本化判官）
    python evals/calibrate.py --provider deepseek   # 真实判官（需 DEEPSEEK_API_KEY）

Provider default: deepseek when DEEPSEEK_API_KEY is set, else fake (printed).
Output: 每族一致率报告 → evals/calibration/report.json + status.json（族级
启用状态：一致率 >=80% 且样本 >=20 → 已启用，否则 人机混合）。
"""
import argparse
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()  # DEEPSEEK_API_KEY 等判官凭据（与 test_langgraph_agent 同款）

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from evals.judge import (  # noqa: E402
    AGREEMENT_TOLERANCE,
    ENABLE_THRESHOLD,
    FAMILY_NAMES,
    MIN_CALIBRATION_SAMPLES,
    JudgeRunner,
    ScriptedJudgeProvider,
    DeepSeekJudgeProvider,
)

CALIBRATION_DIR = Path(__file__).resolve().parent / "calibration"
REPORT_PATH = CALIBRATION_DIR / "report.json"
STATUS_PATH = CALIBRATION_DIR / "status.json"


def choose_provider(env=None):
    """这次校准用真判官还是脚本化判官。

    `CHEMAI_LLM_MODE=record|replay` 时**不许**因为"没有 key"就自动换成 fake：那种模式下没有
    key 是正常的（磁带替模型答话），而脚本判官是照抄人工分数的，一致率永远是 1.0 —— 于是一次
    什么都没测量的运行会被记成 PASS，还顺手把 `status.json` 里那五个族写成"已启用"。
    本机实测：replay 下清掉 key 跑 calibration，110 次"判官调用"0.16 秒跑完，一致率 1.0。
    要 fake 得显式写 `--provider fake`。
    """
    import os

    env = os.environ if env is None else env
    from app.services.llm_transport import LIVE, MODE_ENV

    if env.get(MODE_ENV, LIVE).strip().lower() != LIVE:
        return "deepseek"
    return "deepseek" if (env.get("DEEPSEEK_API_KEY") or env.get("JUDGE_API_KEY")) else "fake"


def load_calibration_set(family):
    """Load and validate one family's labeled sample set.

    Merges the human seed ({family}.yaml) with the reference-derived set
    ({family}_ref.yaml, AI 依据真题/历史数据生成) when present.
    """
    path = CALIBRATION_DIR / f"{family}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"校准集不存在: {path.name}")
    import yaml
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    samples = list(data.get("samples", []))
    ref_path = CALIBRATION_DIR / f"{family}_ref.yaml"
    if ref_path.exists():
        ref_data = yaml.safe_load(ref_path.read_text(encoding="utf-8"))
        samples += ref_data.get("samples", [])
    errors = []
    if family not in FAMILY_NAMES:
        errors.append(f"未知评估族: {family}")
    for i, s in enumerate(samples):
        if not s.get("id"):
            errors.append(f"sample[{i}] 缺 id")
        if "input" not in s:
            errors.append(f"sample[{i}] 缺 input")
        if "output" not in s:
            errors.append(f"sample[{i}] 缺 output")
        hs = s.get("human_score")
        if not isinstance(hs, (int, float)) or not (0 <= hs <= 100):
            errors.append(f"sample[{i}] human_score 必须为 0-100 数值")
    warnings = []
    if len(samples) < MIN_CALIBRATION_SAMPLES:
        warnings.append(
            f"样本不足 {len(samples)}/{MIN_CALIBRATION_SAMPLES} — 不满足启用门槛（≥{MIN_CALIBRATION_SAMPLES}），"
            "该族保持人机混合模式"
        )
    return samples, errors, warnings


def compute_agreement(judge_results, samples):
    """judge-human agreement rate: |judge - human| <= tolerance counts as agree."""
    if not samples:
        return 0.0, 0
    agrees = 0
    for r, s in zip(judge_results, samples):
        score = r.get("score")
        if score is not None and abs(score - s["human_score"]) <= AGREEMENT_TOLERANCE:
            agrees += 1
    return round(agrees / len(samples), 3), agrees


def make_provider(name, samples_for_script=None):
    if name == "deepseek":
        return DeepSeekJudgeProvider()
    # fake: scripted judge echoing each sample's human score (perfect agreement)
    # — 用于离线演示管线，不代表判官真实能力
    responses = [
        json.dumps({
            "score": s["human_score"], "confidence": "high",
            "rationale": "scripted fake judge (校准管线演示)", "severe_flags": [],
        }, ensure_ascii=False)
        for s in (samples_for_script or [])
    ]
    return ScriptedJudgeProvider(responses)


async def calibrate_family(family, provider_name):
    samples, errors, warnings = load_calibration_set(family)
    if errors:
        return {"family": family, "errors": errors, "warnings": warnings}
    provider = make_provider(provider_name, samples_for_script=samples)
    runner = JudgeRunner(provider)
    results, truncated = await runner.judge_batch(samples, family)
    rate, agrees = compute_agreement(results, samples)
    enabled = (len(samples) >= MIN_CALIBRATION_SAMPLES
               and rate >= ENABLE_THRESHOLD and not errors)
    mode = "已启用" if enabled else ("人机混合" if samples else "未启用")
    return {
        "family": family,
        "name": FAMILY_NAMES[family],
        "samples": len(samples),
        "agreed": agrees,
        "agreement_rate": rate,
        "enable_threshold": ENABLE_THRESHOLD,
        "enabled": enabled,
        "mode": mode,
        "judge_calls": runner.calls,
        "estimated_yuan": runner.cost_yuan,
        "truncated": truncated,
        "warnings": warnings,
    }


async def main():
    # Windows 控制台默认 GBK，统一按 UTF-8 输出（¥ 等符号）
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass

    parser = argparse.ArgumentParser(description="L2 判官校准管线 (Spec 04)")
    parser.add_argument("--family", choices=sorted(FAMILY_NAMES) + ["all"], default="all")
    parser.add_argument("--provider", choices=["deepseek", "fake"], default=None,
                        help="默认：有 DEEPSEEK_API_KEY 用 deepseek，否则 fake")
    args = parser.parse_args()

    if args.provider is None:
        provider_name = choose_provider()
        has_key = bool(os.environ.get("DEEPSEEK_API_KEY") or os.environ.get("JUDGE_API_KEY"))
        if provider_name == "fake":
            print("[note] 未检测到 DEEPSEEK_API_KEY，使用 fake 脚本化判官（管线演示，不代表判官能力）")
        elif not has_key:
            print("[note] 录放带模式下不换 fake 判官：请求由磁带回答，一致率是真判官与人工的一致率")
    else:
        provider_name = args.provider

    families = sorted(FAMILY_NAMES) if args.family == "all" else [args.family]
    report = {"timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "provider": provider_name, "families": {}}
    status = {}

    for family in families:
        print(f"\n=== 校准族: {family} ({FAMILY_NAMES[family]}) ===")
        try:
            result = await calibrate_family(family, provider_name)
        except FileNotFoundError as exc:
            print(f"  [SKIP] {exc}")
            continue
        for e in result.get("errors", []):
            print(f"  [ERROR] {e}")
        for w in result.get("warnings", []):
            print(f"  [WARN] {w}")
        if not result.get("errors"):
            print(f"  样本 {result['samples']} | 一致 {result['agreed']} | "
                  f"一致率 {result['agreement_rate']} (门槛 {ENABLE_THRESHOLD})")
            print(f"  判官调用 {result['judge_calls']} 次 | 预估 ¥{result['estimated_yuan']} | "
                  f"模式: {result['mode']}")
        report["families"][family] = result
        if not result.get("errors"):
            status[family] = {"mode": result["mode"], "agreement_rate": result["agreement_rate"],
                              "samples": result["samples"], "updated": report["timestamp"]}

    CALIBRATION_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    STATUS_PATH.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n报告已写入: {REPORT_PATH}")
    print(f"族状态已写入: {STATUS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
