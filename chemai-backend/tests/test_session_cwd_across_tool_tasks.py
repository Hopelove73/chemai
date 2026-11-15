"""cd 的持久化必须跨过 ToolNode 的子 task 边界（本分支 contextvars 迁移引入的回归）。

LangGraph 的 ToolNode 每轮工具调用执行 `await asyncio.gather(*coros)`，每个工具
跑在**新建的子 task** 里。子 task 拿到的是上下文的副本：`ContextVar.set()` 只改
自己那一份，传不回父上下文——`agent/tools/context.py` 头部 2026-09-14 的探针实测
的是反方向（父→子的**读取**能传进来），写入不能。

后果：contextvars 迁移之后，`cd` 写下的会话 cwd 死在 cd 那一轮的工具 task 内，
下一轮 `dir` 读回空串、回到工作区根。旧的模块级全局能跨调用生效，所以这是本分支
把全局改成 ContextVar 时引入的**功能回归**（不是越权，是失效）。

`evals/test_shell_tools.py::test_session_cwd` 全程在同一个协程里 await bash，
看不到这一层——所以它一直绿。本文件按生产形状起 task，先红后绿。
"""
import asyncio
import json
import os
import tempfile
from pathlib import Path

import pytest

from agent.tools import filesystem as fs
from agent.tools.shell import bash


async def _tool_call(command: str) -> dict:
    """按 ToolNode 的形状执行一次工具调用：新建子 task，跑完即弃。"""
    raw, = await asyncio.gather(bash(command=command))
    return json.loads(raw)


def _run(coro):
    async def main():
        return await coro
    return asyncio.run(main())


@pytest.fixture
def workspace(monkeypatch):
    """工作区里放 sub/inside.txt；档位按请求入口的样子设一次。"""
    ws = Path(tempfile.mkdtemp(prefix="chemai_cwd_"))
    (ws / "sub").mkdir()
    (ws / "sub" / "inside.txt").write_text("x", encoding="utf-8")
    (ws / "root-only.txt").write_text("x", encoding="utf-8")
    monkeypatch.setenv(fs.WORKSPACE_ENV, str(ws))
    fs.set_file_access_level("workspace")   # 请求入口：与三个端点同一动作
    fs.set_workspace_root("")
    return ws


def test_cd_persists_into_the_next_tool_task(workspace):
    """`cd sub` 之后的下一条命令必须仍在 sub 里执行——跨工具 task。"""
    async def scenario():
        first = await _tool_call("cd sub")
        assert first["ok"] is True, first
        listing = await _tool_call("dir" if os.name == "nt" else "ls")
        return listing["stdout"]

    stdout = _run(scenario())
    assert "inside.txt" in stdout, "cd 失效：下一条命令仍在工作区根列目录"
    assert "root-only.txt" not in stdout, "cd 完全没生效"


def test_cd_is_visible_to_the_request_context(workspace):
    """父上下文（请求任务）读到的会话 cwd 必须是工具写下的那个。

    `bash_cwd()` 也供工作区外的其它调用方读取；只断言子 task 内自洽会漏掉
    「父上下文仍是旧值」这半边。"""
    async def scenario():
        await _tool_call("cd sub")
        return fs.session_cwd_rel(), str(fs.bash_cwd())

    rel, cwd = _run(scenario())
    assert rel == "sub"
    assert Path(cwd) == (workspace / "sub").resolve()


def test_cd_does_not_leak_into_the_next_request(workspace):
    """跨请求必须回到工作区根：修复不得把隔离改回进程级共享。

    每个请求入口都会 `set_file_access_level`，它换新盒子——上一轮的 cd 不该
    活到下一轮（这正是 contextvars 迁移要解决的串号面，ADR-0020）。"""
    async def first_request():
        await _tool_call("cd sub")
        return fs.session_cwd_rel()

    async def second_request():
        fs.set_file_access_level("workspace")   # 新请求：同端点入口动作
        listing = await _tool_call("dir" if os.name == "nt" else "ls")
        return listing["stdout"]

    assert _run(first_request()) == "sub"
    stdout = _run(second_request())
    assert "root-only.txt" in stdout, "下一请求没有回到工作区根"
    assert "inside.txt" not in stdout, "上一请求的 cd 串到了本请求"
