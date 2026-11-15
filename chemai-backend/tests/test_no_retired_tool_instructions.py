"""模型可见说明里不许出现已退役工具的名字（2026-09-24 全量 code-review）。

`request_approval` 随 ADR-0015 阶段 2 退役，v2 不再把它绑给模型（该事实已由
`test_phase2_gate_unification.test_agent_no_longer_has_request_approval_tool`
钉住）。但工具的 docstring 是逐字送进模型上下文的说明书：审查时六处说明书仍在写
「先调 request_approval 确认」，模型照做只会去调一个不存在的工具，再按 system
prompt 的「失败治理」条款盲目重试——门本来就不靠模型开口，说明书却在教它开口。
这类漂移没有编译器会报，靠 review 守迟早漏，所以钉成断言。

例外名单是**棘轮**不是宽松化：多一个违规者红；名单里的违规者修好了却忘了删条目，
一样红。
"""
import re

from agent.langgraph_agent_v2 import ALL_TOOLS

RETIRED_TOOL = "request_approval"

# 棘轮例外：工具名 ->（本轮不动它的原因，拆除条件）
KNOWN_EXEMPT = {
    "bash": (
        "agent/tools/shell.py 本轮另有在途改动（白名单拒绝提示语），不与之混作同一处修改",
        "那份改动落地后，把 docstring 改成确认门的真实机制并将 bash 移出本表",
    ),
}


def _doc(tool) -> str:
    return tool.__doc__ or ""


def _assert_exactly_the_exemptions(found: set[str], how_to_fix: str):
    assert found == set(KNOWN_EXEMPT), (
        f"违规工具：{sorted(found - set(KNOWN_EXEMPT))}。{how_to_fix}"
        + (f" 已修好请把条目删掉：{sorted(set(KNOWN_EXEMPT) - found)}。"
           if set(KNOWN_EXEMPT) - found else "")
    )


def test_tool_docs_never_instruct_a_retired_tool():
    """说明书里出现已退役工具名 = 教模型调不存在的工具，无论上下文都说不得。"""
    _assert_exactly_the_exemptions(
        {t.__name__ for t in ALL_TOOLS if RETIRED_TOOL in _doc(t)},
        "改为描述真实机制：首次调用被确认门拦下 → 老师在确认卡上决定 → "
        "系统确定性重放，期间不要重复调用本工具、也没有独立确认工具可调。",
    )


def test_docs_that_mention_the_gate_also_say_what_happens_next():
    """提到「会被批准/确认」就必须同时说清门之后会怎样（自动重放、别重复调用）。

    只写「需要批准」而不写重放，模型会停在门前后反复重试同一工具——审查发现的
    那六处说明书正是这个形状，才长期没人察觉。"""
    _assert_exactly_the_exemptions(
        {t.__name__ for t in ALL_TOOLS
         if ("需要批准" in _doc(t) or "需要老师确认" in _doc(t))
         and "重放" not in _doc(t)},
        "补上门的下文：批准后系统自动重放本次调用，不要重复调用本工具。",
    )


# ── persona 提示词：可以提「不必调」，不许写成「先去调」─────────────────
_NEGATIONS = ("不需要", "无需", "不用", "不要", "禁止", "勿")


def test_persona_prompts_mention_retired_tools_only_as_prohibitions():
    """人设提示词与工具说明一样逐字进模型上下文。

    「你不需要自己调 request_approval」留着有用（模型见过旧轨迹会学着调）；
    写成指导句就重新制造了这次审查发现的缺陷。"""
    import yaml
    from pathlib import Path

    personas = sorted((Path(__file__).resolve().parents[1]
                       / "agent" / "personas").glob("*.yaml"))
    assert personas, "persona 目录为空，断言失去对象"
    for p in personas:
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        prompt = str(cfg.get("system_prompt") or "")
        for sent in re.split(r"[。\n]", prompt):
            if RETIRED_TOOL in sent:
                assert any(n in sent for n in _NEGATIONS), (
                    f"{p.name} 把已退役的 {RETIRED_TOOL} 写成了指导句：{sent.strip()}"
                )
