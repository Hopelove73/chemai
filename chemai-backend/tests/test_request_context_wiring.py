"""请求入口的 contextvar 注入守卫（eng-review 2026-09-24 · 发现 T1）。

本次改造把「进程级模块全局」换成「入口注入 + 任务级 contextvar」（ADR-0020）。
注入这一步是**新代码**，而现有测试全部直接调 setter：

    tests/test_filesystem_contextvars.py  fs.set_file_access_level(...)
    tests/test_persona_context.py         set_current_persona("teacher")

于是把某个入口里的 `set_file_access_level(request.file_access)` 或
`set_current_persona(persona)` 整行删掉，上面这些测试**一条都不会红**，
线上却立刻回到改造前的故障：

  · 老师在前端关掉文件访问 → 该端点读不到档位，仍按默认工作区放行；
  · 教师请求里模型省略 persona 调 chemistry_tutor → 静默掉回学生提示。

所以这里守的是「接线」而不是「线」：AST 扫入口函数体，断言三个档位 setter
都在、且实参取自 request（写字面量同样是断线）。与
`tests/test_gate_open_payload_single_source.py` 的 wire 契约扫描同一套路。
"""
import ast
from pathlib import Path

CHANNEL = Path(__file__).resolve().parents[1] / "agent" / "channel" / "langgraph_channel.py"
AGENT_V2 = Path(__file__).resolve().parents[1] / "agent" / "langgraph_agent_v2.py"

# 三个 agent 对话入口：都会走到工具，因此都必须注入请求档位
ENTRY_FUNCS = (
    "agent_chat_langgraph_stream",
    "agent_chat_langgraph",
    "agent_chat_langgraph_resume",
)


def _func(path: Path, name: str) -> ast.AST:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{path.name} 里找不到函数 {name}——入口改名请同步本守卫")


def _call(node: ast.AST, fname: str):
    for sub in ast.walk(node):
        if isinstance(sub, ast.Call):
            fn = sub.func
            name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", "")
            if name == fname:
                return sub
    return None


def _arg_src(call: ast.Call) -> str:
    return ast.unparse(call.args[0]) if call.args else ""


def test_every_agent_entry_injects_file_access_level():
    """三个入口都必须把 request.file_access 落到 contextvar。"""
    for fname in ENTRY_FUNCS:
        entry = _func(CHANNEL, fname)
        call = _call(entry, "set_file_access_level")
        assert call is not None, f"{fname} 不再注入文件访问档位：前端开关对该端点失效"
        assert _arg_src(call) == "request.file_access", \
            f"{fname} 的 set_file_access_level 实参应为 request.file_access，实为 {_arg_src(call)!r}"


def test_every_agent_entry_injects_workspace_root():
    """三个入口都必须把 request.workspace 落到 contextvar（老师自定义工作区）。"""
    for fname in ENTRY_FUNCS:
        entry = _func(CHANNEL, fname)
        call = _call(entry, "set_workspace_root")
        assert call is not None, f"{fname} 不再注入工作区根：老师自定义路径失效、回落默认目录"
        assert _arg_src(call) == "request.workspace", \
            f"{fname} 的 set_workspace_root 实参应为 request.workspace，实为 {_arg_src(call)!r}"


def test_agent_factory_injects_request_persona():
    """建 agent 时必须注入 persona，否则 chemistry_tutor 掉回学生提示。"""
    factory = _func(AGENT_V2, "create_chemai_agent")
    call = _call(factory, "set_current_persona")
    assert call is not None, \
        "create_chemai_agent 不再注入 persona：教师省略 persona 时静默用学生提示（v1 退役要根治的那条）"
    assert _arg_src(call) == "persona", \
        f"set_current_persona 实参应为请求的 persona，实为 {_arg_src(call)!r}"


def test_agent_is_built_with_the_resolved_persona_everywhere():
    """`_get_or_create_agent` 的首个实参必须是解析后的 persona，不能是 request.persona。

    persona 在 `create_chemai_agent` 里既决定 system prompt 又决定工具白名单
    （`agent/langgraph_agent_v2.py:225-242`），而 `request.persona` 是客户端自报、
    教师端常常省略的那个字段。草稿门「打回+反馈」分支就传过它（eng 审查 F2）：
    那一轮修订拿着空 persona 建 agent，提示词与工具集都不是老师这一轮该用的。
    """
    offenders = []
    for node in ast.walk(ast.parse(CHANNEL.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "_get_or_create_agent":
            if not node.args or _arg_src(node) != "persona":
                offenders.append(f"line {node.lineno}: {_arg_src(node) or '(无位置实参)'}")
    assert not offenders, \
        "_get_or_create_agent 用了未经 _resolve_persona 的 persona：" + "; ".join(offenders)


def test_identity_injection_covers_all_three_entries():
    """身份注入（服务端身份，ADR-0014 fail-closed 的前提）同样不能漏端点。

    非流式那个缺口就是历史上漏出来的，故与档位一起守。"""
    for fname in ENTRY_FUNCS:
        entry = _func(CHANNEL, fname)
        assert _call(entry, "set_current_identity") is not None, \
            f"{fname} 不再注入请求身份：收身份参数的工具将一律拒绝（或更糟——放行）"
