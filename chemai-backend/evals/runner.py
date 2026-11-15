"""
ChemAI Eval Runner Core — fast/slow dual-layer gate engine.

Spec:   docs/specs/evals/spec-01-eval-infra.md (seam 1: runner CLI)
Design: docs/evals/eval-system-design.md §4/§5

Pure logic, zero API: registry-driven, testable with fake registries
(see evals/test_runner.py).

Gate rules:
  - fast layer: any non-expected failure -> blocked (exit 1)
  - slow layer: only severity "severe" vetoes (exit 1); fail/warn -> report only
  - expected_fail: failed + expected -> "pending" (exempt, not a failure);
                   passed + expected -> warning "remove expected_fail marker"
Case status: pass / pass_expected / pending / fail / severe

Egress (`egress=` / `--tapes`): how the `needs_api` entries talk to models.
  live   — as before, real calls (default; the entry itself decides nothing)
  record — real calls, tapes written under evals/cassettes/<entry name>
  replay — tapes only, zero network; a miss is a loud failure, never a fallback
Runner logic stays zero-API either way: it only sets the child's environment.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
REPORTS_DIR = Path(__file__).resolve().parent / "reports"
# 一条评估一个子目录。全组共用一个目录会怎样：两条评估打出同一个键时，重放游标会互相吃掉
# 对方的序号 —— 第二条拿到别人的第二条响应，测试还是绿的。
CASSETTES_DIR = Path(__file__).resolve().parent / "cassettes"

EGRESS_ENV = "CHEMAI_LLM_MODE"
CASSETTE_DIR_ENV = "CHEMAI_CASSETTE_DIR"
CASSETTE_SCOPE_ENV = "CHEMAI_CASSETTE_SCOPE"
EGRESSES = ("live", "record", "replay")


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _has_tapes(directory: Path) -> bool:
    """这个条目目录下有没有磁带文件（磁带是一层平铺的 `<provider>__<键>.json`）。

    这只是"带子在不在"的粗判，不是"带子对不对"：真正的答案在缝上 —— 缺哪一盘，replay 会
    当场报 `cassette miss`，而不是在这里靠数文件猜。
    """
    return directory.is_dir() and any(directory.rglob("*.json"))


def egress_env(entry, egress):
    """这一条评估该在什么出口模式下跑 —— 只有 `needs_api` 的条目会被改动。

    快跑层的条目根本不打模型，给它们设模式只会把"这条其实偷偷打了网络"这件事藏起来（默认
    live 下它会照常花钱；标了 replay 却没有它的磁带，反而说不清是谁的错）。真要抓那种漏网，
    抓它的是 `tests/test_llm_transport_egresses.py` 那条 AST 绊线，不是这里。
    """
    if egress == "live" or not entry.get("needs_api"):
        return {}
    name = entry["name"]
    return {EGRESS_ENV: egress,
            CASSETTE_DIR_ENV: str(CASSETTES_DIR / name),
            CASSETTE_SCOPE_ENV: name}


# status -> summary bucket
_STATUS_BUCKET = {
    "pass": "passed",
    "pending": "pending",
    "fail": "failed",
    "severe": "severe",
    "pass_expected": "warnings",
}


def _classify(case_name, passed, is_expected, severity="fail"):
    """Map (passed, expected_fail) to (status, severity, warning)."""
    if passed and is_expected:
        return "pass_expected", "warn", f"REMOVE expected_fail marker: {case_name} now passes"
    if passed:
        return "pass", "pass", ""
    if is_expected:
        return "pending", "pass", ""
    return ("severe" if severity == "severe" else "fail"), severity, ""


def _run_entry(entry, egress="live"):
    """Run one registry entry -> list of case dicts (name, passed, severity, details)."""
    kind = entry.get("kind", "function")
    extra_env = egress_env(entry, egress)
    try:
        if kind == "callable":  # test-only
            return entry["fn"]()
        if kind == "function":
            import importlib
            # `kind="function"` 是在 runner 进程里跑的，所以环境变量得临时换上去再还回去。
            # 出口模式对它的意义和给子进程的一样：这条评估打的模型调用要落在自己的磁带目录里。
            previous = {k: os.environ.get(k) for k in extra_env}
            os.environ.update(extra_env)
            try:
                mod = importlib.import_module(entry["module"])
                result = getattr(mod, entry["func"])()
            finally:
                for k, v in previous.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            results = getattr(result, "results", result)
            return [
                {
                    "name": r.get("name", f"{entry['name']}[{i}]"),
                    "passed": bool(r.get("passed")),
                    "severity": r.get("severity", "fail"),
                    "details": r.get("details", ""),
                }
                for i, r in enumerate(results)
            ]
        module_file = entry["module"].replace(".", "/") + ".py"
        if kind == "pytest":
            cmd = [sys.executable, "-m", "pytest", module_file, "-q", "--no-header"]
        elif kind == "script":
            cmd = [sys.executable, module_file, *entry.get("args", [])]
        else:
            raise ValueError(f"unknown entry kind: {kind}")
        proc = subprocess.run(
            cmd, cwd=str(PROJECT_ROOT), capture_output=True,
            # 不写 encoding 就按区域设置解码 —— 中文 Windows 上是 GBK，而评估脚本自己把
            # stdout 重设成了 UTF-8。读线程当场抛 UnicodeDecodeError，`proc.stdout` 变成 None，
            # 于是失败条目的 details 永远是空的（"红，但看不出为什么红"）。
            text=True, encoding="utf-8", errors="replace",
            timeout=entry.get("timeout", 300),
            env={**os.environ, **extra_env} if extra_env else None,
        )
    except subprocess.TimeoutExpired:
        severity = entry.get("failure_severity", "fail")
        return [{"name": entry["name"], "passed": False, "severity": severity, "details": "timeout"}]
    except Exception as exc:  # entry crashed -> a failed case, never a crashed runner
        severity = entry.get("failure_severity", "fail")
        return [{"name": entry["name"], "passed": False, "severity": severity,
                 "details": f"{type(exc).__name__}: {exc}"}]
    passed = proc.returncode == 0
    # failure_severity: L6 端到端失败 = severe → 慢跑层一票否决（Spec 01/06）
    severity = "fail" if passed else entry.get("failure_severity", "fail")
    details = (proc.stdout or "")[-800:] if not passed else ""
    return [{"name": entry["name"], "passed": passed, "severity": severity, "details": details}]


def run_group(registry, group="fast", only=None, report_dir=None, extra_warnings=None,
              egress="live"):
    """Run one group of the registry. Returns (report, exit_code)."""
    if egress not in EGRESSES:
        raise ValueError(f"--tapes 只接受 {'|'.join(EGRESSES)}，当前为 {egress!r}")
    started = time.time()
    layers_out = {}
    summary = {"total": 0, "passed": 0, "failed": 0, "severe": 0, "pending": 0, "warnings": 0}
    warnings = list(extra_warnings or [])

    selected = [
        e for cfg in registry.values() for e in cfg.get("entries", [])
        if e.get("group", cfg.get("group", "fast")) == group and (not only or e["name"] == only)
    ]
    if egress == "replay":
        # 离线重放最坏的失败方式不是"红"，是"红得看不懂"：九条各自报一次 miss，
        # 而真相是"这批磁带根本没录"。所以在这里一次说清。
        missing = [e["name"] for e in selected
                   if e.get("needs_api") and not _has_tapes(CASSETTES_DIR / e["name"])]
        if missing:
            raise ValueError(
                f"--tapes replay：这些条目没有磁带：{', '.join(missing)}。"
                f" 先录一遍：--tapes record（会真打模型、真花钱）")

    for layer_id, layer_cfg in registry.items():
        entries = [
            e for e in layer_cfg.get("entries", [])
            if e.get("group", layer_cfg.get("group", "fast")) == group
        ]
        if only:
            entries = [e for e in entries if e["name"] == only]
        layer_out = {
            "id": layer_id,
            "name": layer_cfg.get("name", layer_id),
            "unimplemented": not layer_cfg.get("entries"),
            "entries": [],
        }
        if not layer_cfg.get("entries"):
            layer_out["note"] = layer_cfg.get("status", "未实现")
        for entry in entries:
            cases = _run_entry(entry, egress)
            entry_out = {"name": entry["name"], "cases": []}
            expected_fail = entry.get("expected_fail")
            for case in cases:
                is_expected = (expected_fail is True) or (
                    isinstance(expected_fail, list) and case["name"] in expected_fail
                )
                status, severity, warning = _classify(
                    case["name"], case["passed"], is_expected, case.get("severity", "fail")
                )
                entry_out["cases"].append({
                    "name": case["name"],
                    "status": status,
                    "severity": severity,
                    "details": case.get("details", ""),
                })
                summary["total"] += 1
                summary[_STATUS_BUCKET[status]] += 1
                if warning:
                    warnings.append(warning)
            layer_out["entries"].append(entry_out)
        layers_out[layer_id] = layer_out

    if only and summary["total"] == 0:
        raise ValueError(f"--only: no entry named '{only}' in {group} layer")

    blocked_reasons = []
    if group == "fast":
        failing = [
            c for layer in layers_out.values() for e in layer["entries"] for c in e["cases"]
            if c["status"] in ("fail", "severe")
        ]
        if failing:
            blocked_reasons.append(f"fast layer: {len(failing)} deterministic failure(s) — gate blocked")
    else:
        severe = [
            c for layer in layers_out.values() for e in layer["entries"] for c in e["cases"]
            if c["status"] == "severe"
        ]
        if severe:
            blocked_reasons.append(f"slow layer: {len(severe)} severe error(s) — release vetoed")

    verdict = "blocked" if blocked_reasons else "pass"
    report = {
        "timestamp": _now(),
        "group": group,
        # 归档报告必须说得出这次有没有花钱：同一份 report 里 live 与 replay 的分数是一样的读法，
        # 但"离线重放跑出来的"和"真打了模型的"是两件事（ADR-0023 Q3）。
        "egress": egress,
        "layers": layers_out,
        "summary": summary,
        "warnings": warnings,
        "cost": {"judge_calls": 0, "estimated_yuan": 0},  # 占位，Spec 04 填充
        "gate": {"verdict": verdict, "reasons": blocked_reasons},
        "elapsed_seconds": round(time.time() - started, 1),
    }
    if report_dir is not None:
        save_report(report, report_dir)
    return report, (1 if verdict == "blocked" else 0)


def save_report(report, report_dir=None):
    """Archive one run and append it to the trend index. Returns report file path."""
    report_dir = Path(report_dir) if report_dir is not None else REPORTS_DIR
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    file_path = report_dir / f"{stamp}-{report['group']}.json"
    file_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    index = report_dir / "index.json"
    runs = []
    if index.exists():
        try:
            runs = json.loads(index.read_text(encoding="utf-8")).get("runs", [])
        except (json.JSONDecodeError, OSError):
            runs = []
    runs.append({
        "timestamp": report["timestamp"],
        "file": file_path.name,
        "group": report["group"],
        # 趋势序列（spec-01 决策 5 说 index.json 就是趋势数据源）必须看得出这一次有没有花钱：
        # 报告正文里有 `egress`，但看趋势的人翻的是这张表。
        "egress": report.get("egress", "live"),
        "verdict": report["gate"]["verdict"],
        "passed": report["summary"]["passed"],
        "failed": report["summary"]["failed"] + report["summary"]["severe"],
        "pending": report["summary"]["pending"],
    })
    index.write_text(json.dumps({"runs": runs}, ensure_ascii=False, indent=2), encoding="utf-8")
    return file_path


_ICON = {"pass": "PASS", "pending": "PEND", "fail": "FAIL",
         "severe": "SEV ", "pass_expected": "WARN"}


def format_report(report):
    lines = [
        "=" * 64,
        f"  ChemAI Eval Report — {report['group']} layer  ({report['timestamp']})",
        "=" * 64,
    ]
    for layer_id, layer in report["layers"].items():
        if layer["unimplemented"]:
            lines.append(f"  [{layer_id}] {layer['name']}: 未实现（{layer.get('note', '')}）")
            continue
        for entry in layer["entries"]:
            for c in entry["cases"]:
                icon = _ICON[c["status"]]
                lines.append(f"  [{icon}] [{layer_id}] {entry['name']} / {c['name']}")
                if c["details"] and c["status"] in ("fail", "severe"):
                    lines.append(f"         {c['details'][:200]}")
    s = report["summary"]
    lines.append("-" * 64)
    lines.append(
        f"  Total {s['total']} | Pass {s['passed']} | Fail {s['failed']} | Severe {s['severe']}"
        f" | Pending {s['pending']} | Warn {s['warnings']} | {report['elapsed_seconds']}s"
    )
    for w in report["warnings"]:
        lines.append(f"  ! {w}")
    g = report["gate"]
    lines.append(
        f"  GATE: {g['verdict'].upper()}" + (f" — {'; '.join(g['reasons'])}" if g["reasons"] else "")
    )
    lines.append("=" * 64)
    return "\n".join(lines)
