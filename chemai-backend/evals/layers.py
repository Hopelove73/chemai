"""
ChemAI Eval layer registry — data only (no logic).

Design: docs/evals/eval-system-design.md §2/§4
Spec:   docs/specs/evals/spec-01-eval-infra.md

Fast layer = L0 + L1 + L4 + L5 (zero-LLM, seconds, CI gate)
Slow layer = L2 + L3 + L6 (LLM-costing, pre-release/nightly)

entry kinds:
  function — import module, call func(), expect an object with .results
  pytest   — subprocess: python -m pytest <module>
  script   — subprocess: python <module>.py <args>
  callable — test-only (fn carried in the entry itself)
needs_api: entry makes real LLM/API calls; such entries carry group="slow".
expected_fail: True (whole entry) or list of case names — eval-first red tests
               are exempt from the gate while marked (Spec 01 mechanism).
"""

LAYERS = {
    "L0": {
        "name": "工具自身正确性",
        "group": "fast",
        "entries": [
            {"name": "unit_tools", "kind": "function", "module": "evals.test_unit_tools", "func": "run"},
            {"name": "tool_l0", "kind": "function", "module": "evals.test_tool_l0", "func": "run"},
            # Spec 03: 四件套 L0 已转绿、expected_fail 已移除 → 常驻门禁
            {"name": "filesystem_l0", "kind": "function", "module": "evals.test_filesystem_tools", "func": "run"},
            # bash 沙箱测试同时充当 L3 确定性对抗语料（注入/危险命令/逃逸）
            {"name": "shell_l0", "kind": "function", "module": "evals.test_shell_tools", "func": "run"},
            # classifier 走 Gateway LLM 分类（known-flaky 注释为证），需要 API → 慢跑层
            {"name": "classifier", "kind": "function", "module": "evals.test_classifier", "func": "run",
             "needs_api": True, "group": "slow"},
            # 学习技能基线（ADR-0029 批⑥）：注册面（白名单回退即红）/接棒载荷/家长周报
            {"name": "learning_skills", "kind": "function", "module": "evals.test_learning_skills", "func": "run"},
        ],
    },
    "L1": {
        "name": "行为轨迹",
        "group": "fast",
        "entries": [
            {"name": "baseline_behavior", "kind": "pytest", "module": "evals.test_baseline_behavior"},
            # UI v2.3 守门（三端去 AI 味升级：令牌纪律/字阶/动效集中/渐变收敛，
            # 零浏览器确定性子集；浏览器基线/边界见 evals/e2e-ui-v23-checklist.md）
            {"name": "ui_v23", "kind": "script", "module": "evals.test_ui_v23",
             "timeout": 300},
            # Spec 05: 出题交互契约 + 班级薄弱点注入（纯函数单测，零 LLM）
            {"name": "weakpoint_contract", "kind": "pytest", "module": "evals.test_weakpoint"},
            # Spec 06: plan_summary/plan_progress SSE 事件契约（纯适配器测试，零 LLM）
            {"name": "sse_plan_contract", "kind": "pytest", "module": "evals.test_sse_plan"},
            # 工作区按次审批：guard 层批准后单次重放（纯逻辑，零 LLM）
            {"name": "guard_elevation", "kind": "pytest", "module": "evals.test_guard_elevation"},
            {"name": "trajectory_boundary", "kind": "script", "module": "evals.test_langgraph_agent",
             "args": ["--boundary"], "timeout": 300},
            {"name": "trajectory_golden", "kind": "script", "module": "evals.test_langgraph_agent",
             "args": ["--golden"], "needs_api": True, "group": "slow", "timeout": 900},
            {"name": "trajectory_workflow_panel", "kind": "script", "module": "evals.test_langgraph_agent",
             "args": ["--workflow", "--inline-panel"], "needs_api": True, "group": "slow", "timeout": 1200},
            {"name": "trajectory_planning", "kind": "script", "module": "evals.test_langgraph_agent",
             "args": ["--planning"], "needs_api": True, "group": "slow", "timeout": 600},
            {"name": "trajectory_langgraph", "kind": "script", "module": "evals.test_langgraph_agent",
             "args": ["--langgraph"], "needs_api": True, "group": "slow", "timeout": 600},
        ],
    },
    "L2": {
        "name": "输出与交互质量（判官）",
        "group": "slow",
        "entries": [
            # 判官 harness 逻辑单测（假 provider，零成本）→ 快跑层确定性子集
            {"name": "judge_harness", "kind": "pytest", "module": "evals.test_judge", "group": "fast"},
            # 真实校准（deepseek 备选 provider）→ 慢跑层；族启用状态见
            # evals/calibration/status.json（一致率 ≥80% 且样本 ≥20 才启用）
            {"name": "judge_calibration", "kind": "script", "module": "evals.calibrate",
             "needs_api": True, "group": "slow", "timeout": 900},
            # 苏格拉底三层协议（批 2 S5a / REQ-SHP-001..003，场景真源
            # socratic_hint_protocol.yaml）→ 慢跑层；agent+判官全走 httpx 出口，可录放
            {"name": "socratic_protocol", "kind": "script", "module": "evals.test_socratic_protocol",
             "needs_api": True, "group": "slow", "timeout": 1200},
        ],
    },
    "L3": {
        "name": "安全护栏",
        "group": "slow",
        "entries": [
            # 确定性 isolation 子集（TOOL_META 断言）保留在快跑层以维持 CI 覆盖；
            # LLM 成本的对抗语料（注入/越权）随 Spec 03 进慢跑层
            {"name": "safety_isolation", "kind": "pytest", "module": "evals.test_safety_isolation",
             "group": "fast"},
        ],
    },
    "L4": {
        "name": "性能预算",
        "group": "fast",
        "entries": [
            # 埋点接缝 + SLO 软警告（Spec 02）：纯逻辑单测，零 LLM。
            # 真实运行的 SLO 检查由 invoke_with_metrics 供慢跑层消费。
            {"name": "metrics_slo", "kind": "pytest", "module": "evals.test_metrics"},
        ],
    },
    "L5": {
        "name": "稳定性回归",
        "group": "fast",
        "entries": [
            # Spec 02 已修复退出挂起（checkpointer aiosqlite 连接现被关闭）
            {"name": "regression_defects", "kind": "pytest", "module": "evals.test_regression_defects"},
            {"name": "regression_complete", "kind": "pytest", "module": "evals.test_regression_complete",
             "needs_api": True, "group": "slow", "timeout": 600},
            {"name": "trajectory_regression", "kind": "script", "module": "evals.test_langgraph_agent",
             "args": ["--regression"], "needs_api": True, "group": "slow", "timeout": 600},
        ],
    },
    "L6": {
        "name": "端到端任务",
        "group": "slow",
        "entries": [
            # Spec 06: 端到端失败 = severe → 慢跑层一票否决（failure_severity）
            {"name": "e2e_scenarios", "kind": "script", "module": "evals.test_langgraph_agent",
             "args": ["--e2e"], "needs_api": True, "group": "slow",
             "timeout": 900, "failure_severity": "severe"},
        ],
    },
}
