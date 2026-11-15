"""文件工具四全局的并发隔离回归（grilling 2026-09-22 · 决策 B）。

根因：`_ACCESS_LEVEL` / `_ws_override` / `_elevate_once` / `_session_cwd_rel`
是**进程级模块全局**。生产是单进程无 worker，SSE 请求跑在同一事件循环的不同
task 里；一个老师在处理工作区外访问（设 full / 落 override / 置 elevate）时，
另一个人并发进来，前者的状态会串到后者——工作区被换、越界被放行、档位被抬。

身份上下文早已用 contextvars（agent/tools/context.py）拿到任务级隔离；这四处
对齐同一接缝。每个测试用 `asyncio.gather` 起两个协程、各自 set 不同值并跨
await 让出，断言读回的是自己那份。globals 下最后一个写者覆盖 → 红。
"""
import asyncio
import tempfile
from pathlib import Path

from agent.tools import filesystem as fs


async def _yield_to_sibling(times: int = 3) -> None:
    """多次让出，逼迫另一协程在两个 set/read 之间穿插执行。"""
    for _ in range(times):
        await asyncio.sleep(0)


def _gather(*coros):
    async def run():
        return await asyncio.gather(*coros)
    return asyncio.run(run())


def test_file_access_level_isolated_across_tasks():
    async def branch(target):
        fs.set_file_access_level(target)
        await _yield_to_sibling()
        return fs.file_access_level()

    a, b = _gather(branch("off"), branch("full"))
    assert a == "off", f"A 档被并发污染成 {a!r}"
    assert b == "full", f"B 档被并发污染成 {b!r}"


def test_workspace_root_isolated_across_tasks():
    ws_a = tempfile.mkdtemp(prefix="chemai_wsA_")
    ws_b = tempfile.mkdtemp(prefix="chemai_wsB_")

    async def branch(ws):
        fs.set_workspace_root(ws)
        await _yield_to_sibling()
        return fs.workspace_root()

    root_a, root_b = _gather(branch(ws_a), branch(ws_b))
    assert root_a == Path(ws_a).resolve()
    assert root_b == Path(ws_b).resolve()


def test_elevate_once_isolated_across_tasks():
    """一个 task 的一次性放行，绝不能被并发里从未获批准的兄弟 task 消费掉。

    用事件卡准时序：让 stranger 在 grantor 已置位、尚未自我消费的窗口里读取，
    globals 下必然读到 True（越界被误放行）→ 确定性红。
    """
    async def grantor(ready):
        fs.set_elevate_once()
        ready.set()
        await _yield_to_sibling()

    async def stranger(ready):
        await ready.wait()
        await _yield_to_sibling()
        return fs._consume_elevation()

    async def run():
        ready = asyncio.Event()
        _, leaked = await asyncio.gather(grantor(ready), stranger(ready))
        return leaked

    leaked = asyncio.run(run())
    assert leaked is False, "未获放行的协程消费到了兄弟 task 的一次性放行"


def test_session_cwd_isolated_across_tasks(monkeypatch):
    """两个并发请求各 cd 到不同目录，读回的必须是自己那份。

    会话 cwd 存在「盒子」里（`_session_cwd_box_var` 装 dict），盒子由请求入口的
    `set_file_access_level` 换新——所以每个分支都要先走这一步，才是生产形状。
    同一请求内的多条命令共享同一个盒子（写入要跨工具子 task，见
    tests/test_session_cwd_across_tool_tasks.py），跨请求则各拿各的。
    """
    ws = tempfile.mkdtemp(prefix="chemai_ws_")
    monkeypatch.setenv(fs.WORKSPACE_ENV, ws)

    async def branch(sub):
        fs.set_file_access_level("workspace")
        fs.set_session_cwd(sub)
        await _yield_to_sibling()
        return fs.session_cwd_rel()

    a, b = _gather(branch("教案备课"), branch("试卷真题"))
    assert a == "教案备课", f"A 会话 cwd 被污染成 {a!r}"
    assert b == "试卷真题", f"B 会话 cwd 被污染成 {b!r}"
