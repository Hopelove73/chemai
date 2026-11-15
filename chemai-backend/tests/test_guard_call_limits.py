"""GuardState.call_limits 预算语义守门。

check_limit 是「每次调用 +1，超过 limit 才拦」：limit=3 时第 1~3 次放行、
第 4 次起拦截且计数继续涨——所以老师看到的失败卡可能是「已调用 5 次
（上限 3 次）」：被拦后模型不死心又试，显示的是最后一次的计数。
grep 上限 3→5（2026-10-05）：工件自检流（生成动画后 grep 数 keyframe）
一轮要用 4~5 次，3 会把正当 QA 循环掐在半截。
"""
import pytest

from agent.guard import GuardState


@pytest.fixture
def guard():
    # _ensure_call_limits 首调会整体重绑定 TOOL_CALL_LIMITS 全局——先初始化再改，
    # 避免 fixture 改在旧 dict、check_limit 用了新 dict 的错位
    import agent.guard as g
    g._ensure_call_limits()
    original = dict(g.TOOL_CALL_LIMITS)
    g.TOOL_CALL_LIMITS["grep"] = 5
    yield GuardState()
    g.TOOL_CALL_LIMITS.clear()
    g.TOOL_CALL_LIMITS.update(original)


def test_first_calls_pass_then_block(guard):
    for _ in range(5):
        assert guard.check_limit("grep") is None
    msg = guard.check_limit("grep")
    assert msg is not None and "上限 5 次" in msg and "6 次" in msg


def test_counter_keeps_counting_after_block(guard):
    for _ in range(7):
        guard.check_limit("grep")
    msg = guard.check_limit("grep")
    assert "8 次" in msg, "被拦后的重试也计数——老师看到的次数是最后一次尝试"


def test_unlimited_tool_passes(guard):
    assert guard.check_limit("没有配额的工具") is None
