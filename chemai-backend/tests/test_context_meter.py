"""上下文占用计量：锚点、估算回退，以及"窗口由谁说了算"。

这块此前全仓零断言（`context_meter` 与它的 SSE 载荷），而它显示的是老师能直接看到的
一个数字。本轮把用量解析收进模型出口那条缝（ADR-0023 Q14），顺手把窗口下发也钉住：
前端原先硬编码 128_000，改一次 `CONTEXT_WINDOW` 环境变量就会让圆环显示一个错的百分比
—— 而"已用 40%"与"已用 78%"在环上看着几乎一样，没人会当场发现。
"""
import pathlib
import re

import pytest

from agent.context_meter import (
    build_context_event, estimate_message_tokens, estimate_surface, extract_prompt_tokens,
)
from app.config import config


class _Msg:
    """够用就好的 LangChain 消息替身：meter 只读 content / type / tool_calls。"""

    def __init__(self, type_, content, tool_calls=None):
        self.type = type_
        self.content = content
        self.tool_calls = tool_calls or []


MESSAGES = [
    _Msg("system", "你是化学教研助手" * 10),
    _Msg("human", "给张三出 5 道盐类水解的题"),
    _Msg("ai", "", tool_calls=[{"name": "generate_questions"}]),
    _Msg("tool", "题目已生成" * 20),
]


# ── 锚点与回退 ────────────────────────────────────────────────────────────

def test_provider_reported_usage_is_the_anchor():
    ev = build_context_event(9_000, MESSAGES, 128_000)
    assert ev["used_tokens"] == 9_000, "provider 报的数就是权威，不许被估算覆盖"
    assert ev["percent"] == 7


def test_without_a_provider_number_it_falls_back_to_the_estimate():
    """`None` 走估算分支；估算的三段之和必须等于 used_tokens，否则页面会出现对不上的数。"""
    est = build_context_event(None, MESSAGES, 128_000)
    assert est["used_tokens"] > 0
    assert est["used_tokens"] == sum(est["breakdown"].values())
    assert est["breakdown"] == dict(zip(
        ("system_tokens", "tools_tokens", "message_tokens"), estimate_surface(MESSAGES)))


def test_percent_clamps_at_100_because_the_ring_has_no_other_end():
    ev = build_context_event(400_000, MESSAGES, 128_000)
    assert ev["percent"] == 100


def test_missing_window_does_not_divide_by_zero():
    assert build_context_event(1_000, MESSAGES, 0)["percent"] == 0


def test_breakdown_absorbs_the_billing_that_is_not_in_the_conversation():
    """provider 计费含系统提示与工具 schema，二者不在会话消息里，余量归 system。"""
    ev = build_context_event(9_000, MESSAGES, 128_000)
    b = ev["breakdown"]
    assert b["system_tokens"] > estimate_surface(MESSAGES)[0], "余量没并进 system"
    assert b["tools_tokens"] + b["message_tokens"] <= 9_000


# ── 载荷形状就是前后端契约（字段只增不改）──────────────────────────────────

def test_context_event_wire_shape():
    ev = build_context_event(1_200, MESSAGES, 128_000)
    assert set(ev) == {"type", "used_tokens", "context_window", "percent", "breakdown"}
    assert ev["type"] == "context"
    assert set(ev["breakdown"]) == {"system_tokens", "tools_tokens", "message_tokens"}, \
        "前端 ContextMeter 按这三个键取值，改名就是静默空白"


def test_estimate_surface_splits_by_role_not_by_position():
    system, tools, messages = estimate_surface(MESSAGES)
    assert system == estimate_message_tokens("system", "你是化学教研助手" * 10)
    assert tools > 0 and messages > 0


# ── 用量解析住在缝里，这里只留入口 ─────────────────────────────────────────

def test_extract_prompt_tokens_delegates_to_the_seam():
    """字段名（prompt_tokens / input_tokens / token_usage）由 `llm_transport` 认。"""

    class _Out:
        usage_metadata = {"input_tokens": 4_096}
        response_metadata = {}

    assert extract_prompt_tokens(_Out()) == 4_096
    assert extract_prompt_tokens(None) is None
    assert extract_prompt_tokens([_Out()]) == 4_096, "流式列表取最后一条"


# ── 窗口下发：后端配置说了算 ───────────────────────────────────────────────

@pytest.fixture
def history_client(monkeypatch):
    """只挂会话路由，并把 checkpoint 读替成"没有检查点"，绝不碰真实的 57MB checkpoint 库。"""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from agent.channel import conversation as conversation_mod
    from app.middleware import conversation as guard
    from app.middleware.auth import create_access_token
    from app.models.database import Base, get_db

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine, expire_on_commit=False)()

    class _NoCp:
        async def aget_tuple(self, _cfg):
            return None

    async def _fake_cp():
        return _NoCp()
    monkeypatch.setattr("agent.langgraph_agent_v2._get_v2_checkpointer", _fake_cp)
    monkeypatch.setattr(guard, "thread_exists", lambda thread_id: True)

    app = FastAPI()
    app.include_router(conversation_mod.router, prefix="/api/agent")

    def override_get_db():
        yield db

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        c.headers.update({"Authorization": f"Bearer {create_access_token('T_chem', 'teacher')}"})
        guard.claim_conversation(db, "t-1", {"user_id": "T_chem", "role": "teacher",
                                             "school_id": "sch1"})
        yield c
    db.close()


def test_history_delivers_the_configured_window(history_client, monkeypatch):
    monkeypatch.setattr(config, "CONTEXT_WINDOW", 32_000)
    body = history_client.get("/api/agent/chat/history/t-1").json()
    assert body["context_window"] == 32_000, "前端只能拿后端报的窗口，不许自己猜"
    # `closed_gates`：本会话最近关掉的门（批准/打回/放弃后落终态），前端据此把这些
    # 卡片改成终态渲染。没有它，卡片只能停在"待确认"，刷新后还挂在那儿。
    assert set(body) == {"messages", "thread_id", "run", "gate", "closed_gates",
                         "context_window"}, \
        "前端按 `context_window` 这个键取值：改名等于把圆环静默关掉"


def test_the_window_the_page_estimates_with_is_the_window_the_stream_reports(history_client):
    """同一页上的两个数必须同一个来源：历史响应与 SSE `context` 事件报同一个窗口。

    这条是"老师看见 40% 其实是 78%"那类错误的绊线：一侧硬编码、另一侧读配置，
    它就会红 —— 而这两条路径在页面上是同屏出现的。
    """
    body = history_client.get("/api/agent/chat/history/t-1").json()
    event = build_context_event(None, [], config.CONTEXT_WINDOW)
    assert event["context_window"] == body["context_window"] == config.CONTEXT_WINDOW


# ── 取整口径：前后端必须同一组数 ───────────────────────────────────────────

# 前端 `Math.round` 是 half-up；Python 内建 `round` 是 half-to-even（0.5→0、2.5→2）。
# 这组边界值钉住口径：改了后端就会红，改前端常量则前端的孪生用例会红。
GOLDEN_PERCENT = [(640, 128_000, 1), (3200, 128_000, 3), (5760, 128_000, 5),
                  (1280, 128_000, 1), (64_000, 128_000, 50), (99_900, 128_000, 78)]


@pytest.mark.parametrize("used,window,percent", GOLDEN_PERCENT)
def test_percent_uses_half_up_like_the_frontend(used, window, percent):
    assert build_context_event(used, [], window)["percent"] == percent


def test_history_error_branch_still_delivers_the_window_without_touching_the_db(history_client,
                                                                                monkeypatch):
    """检查点读挂时不再顺带查一轮 run/门：那次查询正是把 200 变成 500 的地方。"""
    class _Boom:
        async def aget_tuple(self, _cfg):
            raise RuntimeError("checkpoint 读挂了")

    async def _boom():
        return _Boom()
    monkeypatch.setattr("agent.langgraph_agent_v2._get_v2_checkpointer", _boom)
    def _no_db(*_a, **_k):
        raise AssertionError("错误分支不该再查库")
    monkeypatch.setattr("agent.channel.conversation._active_run_payload", _no_db)

    body = history_client.get("/api/agent/chat/history/t-1").json()
    assert body["messages"] == [] and "error" in body
    assert body["context_window"] == config.CONTEXT_WINDOW


def test_the_two_golden_lists_are_the_same_numbers():
    """前后端两份黄金三元组必须是同一组数：各钉一边口径的前提是"同一个数"。

    两份清单靠人抄一定会漂（这一轮已经抓到过一次漂），所以直接对源文本。正向对照不可省：
    正则匹配不到会返回空表，"两边都空"也是一种绿，那种绿最坏。
    """
    ts = (pathlib.Path(__file__).resolve().parents[1]
          / "frontend/tests/context-meter.test.ts")
    block = ts.read_text(encoding="utf-8").split("const GOLDEN")[1].split("\n]")[0]
    triples = [(int(a), int(b), int(c)) for a, b, c in
               re.findall(r"\[\s*([\d_]+)\s*,\s*([\d_]+)\s*,\s*(\d+)\s*\]", block)]
    assert len(triples) == len(GOLDEN_PERCENT) >= 4, f"前端清单没读到，正则漂了：{triples}"
    assert triples == GOLDEN_PERCENT, f"两份清单漂了：前端 {triples} != 后端 {GOLDEN_PERCENT}"
