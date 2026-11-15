"""
Runner meta-tests — verify the fast/slow gate engine itself (Spec 01, seam 1).

Fake registries drive verdict decisions; zero API, zero LLM. These tests pin
the EXTERNAL behavior of the eval system: exit codes, gate verdicts, report
structure, expected_fail semantics, and trend archiving.
"""
import json
import sys

import pytest

from evals.runner import run_group, save_report


# ── fake registry helpers ────────────────────────────────────────────────

def _reg(*entries, group="fast"):
    return {"L0": {"name": "工具自身正确性", "group": group, "entries": list(entries)}}


def _callable(name, fn, expected_fail=None):
    entry = {"name": name, "kind": "callable", "fn": fn}
    if expected_fail is not None:
        entry["expected_fail"] = expected_fail
    return entry


def _cases(pairs, severity="fail"):
    return [{"name": n, "passed": p, "severity": severity} for n, p in pairs]


def _flatten(report):
    return [c for l in report["layers"].values() for e in l["entries"] for c in e["cases"]]


# ── fast gate ────────────────────────────────────────────────────────────

def test_fast_all_green_passes():
    entry = _callable("ok", lambda: _cases([("c1", True), ("c2", True)]))
    report, code = run_group(_reg(entry), "fast")
    assert code == 0
    assert report["gate"]["verdict"] == "pass"
    assert report["summary"] == {
        "total": 2, "passed": 2, "failed": 0, "severe": 0, "pending": 0, "warnings": 0,
    }


def test_fast_failure_blocks():
    entry = _callable("bad", lambda: _cases([("c1", True), ("c2", False)]))
    report, code = run_group(_reg(entry), "fast")
    assert code == 1
    assert report["gate"]["verdict"] == "blocked"
    assert report["summary"]["failed"] == 1
    assert "deterministic failure" in report["gate"]["reasons"][0]


# ── expected_fail semantics (eval-first) ─────────────────────────────────

def test_expected_fail_failure_is_pending():
    entry = _callable("red", lambda: _cases([("c1", False)]), expected_fail=True)
    report, code = run_group(_reg(entry), "fast")
    assert code == 0, "expected-fail red tests must not block the gate"
    case = _flatten(report)[0]
    assert case["status"] == "pending"
    assert report["summary"]["pending"] == 1
    assert report["summary"]["failed"] == 0


def test_expected_fail_pass_warns_remove():
    entry = _callable("red", lambda: _cases([("c1", True)]), expected_fail=True)
    report, code = run_group(_reg(entry), "fast")
    assert code == 0
    case = _flatten(report)[0]
    assert case["status"] == "pass_expected"
    assert any("REMOVE expected_fail" in w for w in report["warnings"])


def test_expected_fail_name_list_granularity():
    entry = _callable(
        "mixed",
        lambda: _cases([("red_case", False), ("real_case", False)]),
        expected_fail=["red_case"],
    )
    report, code = run_group(_reg(entry), "fast")
    assert code == 1, "non-expected failure must still block"
    statuses = {c["name"]: c["status"] for c in _flatten(report)}
    assert statuses == {"red_case": "pending", "real_case": "fail"}


# ── slow gate ────────────────────────────────────────────────────────────

def test_slow_fail_does_not_block():
    entry = _callable("noisy", lambda: _cases([("c1", False)]))
    report, code = run_group(_reg(entry, group="slow"), "slow")
    assert code == 0, "score noise must not veto a release"
    assert report["gate"]["verdict"] == "pass"
    assert report["summary"]["failed"] == 1


def test_slow_severe_blocks():
    entry = _callable("veto", lambda: _cases([("c1", False)], severity="severe"))
    report, code = run_group(_reg(entry, group="slow"), "slow")
    assert code == 1
    assert report["gate"]["verdict"] == "blocked"
    assert "severe" in report["gate"]["reasons"][0]


def test_entry_failure_severity_escalates(monkeypatch):
    """Spec 06: L6 e2e 失败 = severe（failure_severity 条目级升级）。"""
    import evals.runner as runner_mod

    def fake_run(cmd, **kwargs):
        class P:
            returncode = 1
            stdout = "e2e failed"
        return P()

    monkeypatch.setattr(runner_mod.subprocess, "run", fake_run)
    entry = {"name": "e2e", "kind": "pytest", "module": "fake", "failure_severity": "severe"}
    report, code = run_group(_reg(entry, group="slow"), "slow")
    assert code == 1, "severe e2e failure must veto the slow gate"
    case = _flatten(report)[0]
    assert case["status"] == "severe"


# ── layers / report structure ────────────────────────────────────────────

def test_unimplemented_layer_marked():
    reg = {
        "L0": {"name": "工具", "group": "fast", "entries": []},
        "L4": {"name": "性能预算", "group": "fast", "entries": [], "status": "未实现 — Spec 02"},
    }
    report, code = run_group(reg, "fast")
    assert code == 0
    assert report["layers"]["L4"]["unimplemented"] is True
    assert report["layers"]["L4"]["note"] == "未实现 — Spec 02"
    assert report["summary"]["total"] == 0


def test_report_structure():
    entry = _callable("ok", lambda: _cases([("c1", True)]))
    report, _ = run_group(_reg(entry), "fast")
    assert set(report) >= {"timestamp", "group", "layers", "summary", "warnings", "cost", "gate", "elapsed_seconds"}
    assert report["cost"] == {"judge_calls": 0, "estimated_yuan": 0}
    assert report["gate"]["verdict"] in ("pass", "blocked")


def test_entry_exception_becomes_failure():
    def boom():
        raise RuntimeError("boom")

    report, code = run_group(_reg(_callable("boom", boom)), "fast")
    assert code == 1
    case = _flatten(report)[0]
    assert case["status"] == "fail"
    assert "boom" in case["details"]


def test_function_entry_normalizes_results(monkeypatch):
    class FakeMod:
        @staticmethod
        def run():
            class R:
                results = [{"name": "c1", "passed": True}]

            return R()

    monkeypatch.setitem(sys.modules, "fake", FakeMod)
    entry = {"name": "f", "kind": "function", "module": "fake", "func": "run"}
    report, code = run_group(_reg(entry), "fast")
    assert code == 0
    assert report["summary"]["passed"] == 1


# ── only filter ──────────────────────────────────────────────────────────

def test_only_filter_runs_single_entry():
    reg = _reg(
        _callable("a", lambda: _cases([("a1", True)])),
        _callable("b", lambda: _cases([("b1", False)])),
    )
    report, code = run_group(reg, "fast", only="a")
    assert code == 0
    assert report["summary"] == {
        "total": 1, "passed": 1, "failed": 0, "severe": 0, "pending": 0, "warnings": 0,
    }


def test_only_unknown_entry_raises():
    with pytest.raises(ValueError):
        run_group(_reg(_callable("a", lambda: [])), "fast", only="nope")


def test_extra_warnings_land_in_report():
    entry = _callable("ok", lambda: _cases([("c1", True)]))
    report, code = run_group(_reg(entry), "fast", extra_warnings=["tool 'x' missing from coverage"])
    assert code == 0
    assert report["warnings"] == ["tool 'x' missing from coverage"]
    assert report["summary"]["warnings"] == 0, "extra warnings do not count as case warnings"


def test_coverage_warnings_flags_missing_tools():
    from evals.tool_coverage import coverage_warnings
    manifest = {"tool_a": {"category": "l0", "where": "x"}}
    warnings = coverage_warnings(manifest=manifest, tool_names=["tool_a", "tool_b"])
    assert warnings == ["tool 'tool_b' missing from L0 coverage manifest (evals/tool_coverage.py)"]
    assert coverage_warnings(manifest=manifest, tool_names=["tool_a"]) == []


# ── trend archiving ──────────────────────────────────────────────────────

def test_save_report_archives_and_indexes(tmp_path):
    entry = _callable("ok", lambda: _cases([("c1", True)]))
    report, code = run_group(_reg(entry), "fast", report_dir=tmp_path)
    assert code == 0

    files = list(tmp_path.glob("*-fast.json"))
    assert len(files) == 1
    saved = json.loads(files[0].read_text(encoding="utf-8"))
    assert saved["gate"]["verdict"] == "pass"

    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert len(index["runs"]) == 1
    assert index["runs"][0]["file"] == files[0].name

    # second run appends to the same index (trend series)
    run_group(_reg(_callable("ok", lambda: _cases([("c1", True)]))), "fast", report_dir=tmp_path)
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert len(index["runs"]) == 2


# ── 模型出口模式（--tapes live|record|replay）────────────────────────────

def _needs_api_script(name, **extra):
    entry = {"name": name, "kind": "script", "module": "evals.demo", "needs_api": True}
    entry.update(extra)
    return entry


def test_live_mode_changes_nothing_and_needs_api_entries_get_the_seam():
    from evals.runner import egress_env

    assert egress_env(_needs_api_script("classifier"), "live") == {}
    assert egress_env({"name": "unit_tools"}, "replay") == {}, "不打模型的条目不该被改环境"

    env = egress_env(_needs_api_script("classifier"), "replay")
    assert env["CHEMAI_LLM_MODE"] == "replay"
    assert env["CHEMAI_CASSETTE_SCOPE"] == "classifier"
    assert env["CHEMAI_CASSETTE_DIR"].endswith("classifier")


def test_two_entries_do_not_share_one_cassette_directory():
    """共用一个目录时，两条评估打出同一个键会互相吃掉对方的重放序号，测试照样绿。"""
    from evals.runner import egress_env

    a = egress_env(_needs_api_script("classifier"), "record")[ "CHEMAI_CASSETTE_DIR"]
    b = egress_env(_needs_api_script("trajectory_golden"), "record")["CHEMAI_CASSETTE_DIR"]
    assert a != b and a.endswith("classifier") and b.endswith("trajectory_golden"), (a, b)


def test_subprocess_entries_actually_get_the_env(monkeypatch):
    import evals.runner as runner
    import subprocess

    seen = {}

    def _fake_run(cmd, **kw):
        seen["env"] = kw.get("env")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(subprocess, "run", _fake_run)
    cases = runner._run_entry(_needs_api_script("classifier"), "replay")
    assert cases[0]["passed"] is True
    assert seen["env"]["CHEMAI_LLM_MODE"] == "replay"
    assert seen["env"]["CHEMAI_CASSETTE_DIR"].endswith("classifier")

    plain = runner._run_entry({"name": "unit_tools", "kind": "script", "module": "evals.demo"}, "replay")
    assert plain[0]["passed"] is True and seen["env"] is None, "不打模型的条目不该拿到改过的环境"


def test_in_process_entries_set_and_restore_the_environment(monkeypatch, tmp_path):
    """`kind="function"` 是在 runner 进程里跑的：环境要换上去，也要还回来。

    还不回去的话，后面那条评估会以为自己也在 replay 里 —— 于是一整批评估静默地跑在错误的
    出口模式上。
    """
    import types

    import evals.runner as runner
    import os

    mod = types.ModuleType("evals.test_egress_probe")
    observed = {}

    def _run():
        observed["mode"] = os.environ.get("CHEMAI_LLM_MODE")
        observed["dir"] = os.environ.get("CHEMAI_CASSETTE_DIR")
        return [{"name": "inside", "passed": observed["mode"] == "replay"}]

    mod.run = _run
    monkeypatch.setitem(sys.modules, "evals.test_egress_probe", mod)

    for k in ("CHEMAI_LLM_MODE", "CHEMAI_CASSETTE_DIR", "CHEMAI_CASSETTE_SCOPE"):
        monkeypatch.delenv(k, raising=False)
    cases = runner._run_entry({"name": "classifier", "kind": "function",
                              "module": "evals.test_egress_probe", "func": "run",
                              "needs_api": True}, "replay")
    assert cases[0]["passed"] is True, observed
    assert observed["dir"].endswith("classifier")
    for k in ("CHEMAI_LLM_MODE", "CHEMAI_CASSETTE_DIR", "CHEMAI_CASSETTE_SCOPE"):
        assert os.environ.get(k) is None, f"{k} 没还回去，后面的评估会在错误的出口模式上跑"


def test_replay_without_tapes_refuses_before_running_anything(monkeypatch, tmp_path):
    import evals.runner as runner

    monkeypatch.setattr(runner, "CASSETTES_DIR", tmp_path / "cassettes")
    ran = []
    monkeypatch.setattr(runner, "_run_entry",
                        lambda entry, egress="live": ran.append(entry["name"]) or [])
    with pytest.raises(ValueError, match="没有磁带"):
        run_group(_reg(_needs_api_script("classifier"), group="slow"), "slow", egress="replay")
    assert ran == [], "预检失败却还是跑了评估"

    (tmp_path / "cassettes" / "classifier" / "deepseek").mkdir(parents=True)
    (tmp_path / "cassettes" / "classifier" / "deepseek" / "t1.json").write_text("{}", encoding="utf-8")
    report, _code = run_group(_reg(_needs_api_script("classifier"), group="slow"), "slow",
                              egress="replay")
    assert ran == ["classifier"] and report["egress"] == "replay"


def test_unknown_egress_mode_is_refused():
    with pytest.raises(ValueError, match="tapes"):
        run_group(_reg(_callable("ok", lambda: _cases([("c1", True)]))), "fast", egress="sometimes")


def test_a_failing_childs_output_survives_the_decode(monkeypatch, tmp_path):
    """中文 Windows 上 `text=True` 默认按 GBK 解码，而评估脚本自己把 stdout 设成了 UTF-8。

    读线程会当场抛 UnicodeDecodeError，`proc.stdout` 变成 None —— 于是失败条目的 details 永远是
    空的：报告说"红"，却一个字都没说为什么红。
    """
    import evals.runner as runner

    (tmp_path / "probe_utf8.py").write_text(
        "import sys\n"
        "try:\n"
        "    sys.stdout.reconfigure(encoding='utf-8')\n"
        "except Exception:\n"
        "    pass\n"
        "print('判官一致率低于门槛 0.8')\n"
        "sys.exit(1)\n", encoding="utf-8")
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    cases = runner._run_entry({"name": "probe", "kind": "script", "module": "probe_utf8"}, "live")
    assert cases[0]["passed"] is False
    assert "判官一致率" in cases[0]["details"], \
        f"子进程的中文输出没活过解码，details = {cases[0]['details']!r}"


# ── 录放带模式下不许换成脚本判官 ──────────────────────────────────────────

def test_the_calibration_judge_does_not_downgrade_under_tapes():
    """fake 判官是照抄人工分数的，一致率永远 1.0 —— 一次什么都没测量的运行会记成 PASS。

    本机实测：replay 下清掉 key 跑 `judge_calibration`，110 次"判官调用"0.16 秒跑完、五族一致率
    全 1.0、`status.json` 全部写成"已启用"；磁带真正回答时同一批样本是 0.043 / 1.0 / 0.25 /
    0.091 / 0.273。差的那一位数就是这次测量有没有发生。
    """
    from evals.calibrate import choose_provider

    # live + 无 key：仍然允许 fake（那是"管线演示"的正当用法）
    assert choose_provider({}) == "fake"
    assert choose_provider({"DEEPSEEK_API_KEY": "sk-x"}) == "deepseek"
    # 录/放模式下没有 key 是常态（请求不出本机），所以不能拿它当降级理由
    assert choose_provider({"CHEMAI_LLM_MODE": "replay"}) == "deepseek"
    assert choose_provider({"CHEMAI_LLM_MODE": "record"}) == "deepseek"
    # 显式要 fake 仍然给：它是 `--provider fake` 的正当用法，只是不会悄悄发生
    assert choose_provider({"CHEMAI_LLM_MODE": "replay", "DEEPSEEK_API_KEY": ""}) == "deepseek"



def test_the_judge_model_name_comes_from_config(monkeypatch):
    """判官的键里带着型号名：写死一个型号，换了型号磁带就整批对不上。"""
    from evals.judge import DeepSeekJudgeProvider
    from app.core.config import settings

    for k in ("JUDGE_MODEL", "DEEPSEEK_MODEL"):
        monkeypatch.delenv(k, raising=False)
    assert DeepSeekJudgeProvider().model == settings.DEEPSEEK_MODEL


def test_a_missing_key_only_stops_the_judge_when_it_would_actually_leave(monkeypatch):
    """缺 key 是"出不了门"的意思；磁带会答话时它不是理由（replay 的正卖点是 CI 零凭据）。"""
    import asyncio

    import pytest

    from evals.judge import DeepSeekJudgeProvider

    monkeypatch.delenv("JUDGE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("CHEMAI_LLM_MODE", raising=False)
    monkeypatch.delenv("CHEMAI_CASSETTE_DIR", raising=False)
    prov = DeepSeekJudgeProvider(model="m")
    with pytest.raises(RuntimeError, match="DEEPSEEK_API_KEY"):
        asyncio.run(prov.complete("q"))


def test_replay_serves_the_judge_from_tapes_without_credentials(tmp_path, monkeypatch):
    """一条磁带 + 没有 key = 判官照样答。这条红了就说明离线重放又要靠假判官撑场面了。"""
    import asyncio
    import json

    from app.services.llm_transport import CassetteStore, request_key
    from evals.judge import DeepSeekJudgeProvider

    url = "https://api.deepseek.com/v1/chat/completions"
    body = json.dumps({"model": "m", "temperature": 0,
                       "messages": [{"role": "user", "content": "q"}]}).encode()
    store = CassetteStore(str(tmp_path))
    store.record(request_key(url, body), url, 200, {},
                 json.dumps({"choices": [{"message": {"content": "分数 42"}}]}).encode())
    monkeypatch.setenv("CHEMAI_LLM_MODE", "replay")
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", str(tmp_path))
    monkeypatch.delenv("JUDGE_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    prov = DeepSeekJudgeProvider(model="m")
    assert asyncio.run(prov.complete("q")) == "分数 42"


def test_the_trend_index_records_whether_this_run_cost_money(tmp_path):
    """`index.json` 是趋势数据源（spec-01 决策 5）：只看这张表也得能问出"这次花了钱没有"。"""
    report, _code = run_group(_reg(_callable("ok", lambda: _cases([("c1", True)]))),
                              "fast", report_dir=tmp_path, egress="replay")
    index = json.loads((tmp_path / "index.json").read_text(encoding="utf-8"))
    assert index["runs"][0]["egress"] == "replay", index["runs"][0]
    assert report["egress"] == "replay"
