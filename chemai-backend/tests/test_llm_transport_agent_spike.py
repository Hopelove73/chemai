"""第一刀（架构报告候选 2 的 Q9）：注入 ChatOpenAI 后，agent 回路能不能只靠磁带走完。

这一步唯一的目的就是去风险 —— 罩不住 agent 回路，整条接缝就没意义，所以它排在
LLMService / DeepSeekProvider 铺开之前。要证的事按共识写死：

· replay 下 create_react_agent 完整跑完「一轮工具调用 + 一轮最终回答」，全程不碰网络；
· 流式（tool_calls delta 与末尾 usage 块）与非流式（chat.completion JSON）两种形状都录得下、放得出；
· 请求变一点 → 硬失败，不静默走网络（Q3）；
· 注入的 AsyncClient 保住 SDK 自己的超时与连接上限（Q1 把接缝放在传输层，不能顺手把
  生产的 timeout 语义换掉）。
"""
import asyncio
import json
import os

import httpx
import pytest
from langchain_core.tools import tool
from langgraph.prebuilt import create_react_agent

from app.services import llm_transport as lt
from cassette_fakes import ScriptedAsync

JSON_CT = "application/json"
SSE_CT = "text/event-stream"


def _completion(msg, finish="stop", content=None, usage=None, model="deepseek-chat"):
    """非流式形状：openai SDK 会把它解析成 ChatCompletion 对象。"""
    body = {"id": "chatcmpl-spike", "object": "chat.completion", "created": 1, "model": model,
            "choices": [{"index": 0, "message": msg, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return json.dumps(body, ensure_ascii=False).encode(), JSON_CT


TOOL_CALL_JSON = _completion(
    {"role": "assistant", "content": None,
     "tool_calls": [{"id": "call_1", "type": "function",
                     "function": {"name": "diagnose_student",
                                  "arguments": '{"student_id": "S-1"}'}}]},
    finish="tool_calls")
FINAL_JSON = _completion({"role": "assistant", "content": "该生主导障碍是概念理解"},
                         usage={"prompt_tokens": 137, "completion_tokens": 8, "total_tokens": 145})


def _chunk(delta, finish=None, usage=None, model="deepseek-chat"):
    body = {"id": "chatcmpl-spike", "object": "chat.completion.chunk", "created": 1,
            "model": model, "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    if usage:
        body["usage"] = usage
    return "data: " + json.dumps(body, ensure_ascii=False) + "\n\n"


def _sse(lines):
    return ("".join(lines) + "data: [DONE]\n\n").encode(), SSE_CT


TOOL_CALL_SSE = _sse([
    _chunk({"role": "assistant"}),
    _chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                            "function": {"name": "diagnose_student",
                                         "arguments": '{"student_id": "S-1"}'}}]}),
    _chunk({}, finish="tool_calls"),
])
FINAL_SSE = _sse([
    _chunk({"content": "该生"}),
    _chunk({"content": "主导障碍是概念理解"}),
    _chunk({}, finish="stop", usage={"prompt_tokens": 137, "completion_tokens": 8,
                                     "total_tokens": 145}),
])



@tool
def diagnose_student(student_id: str) -> str:
    """诊断一名学生的障碍分布。"""
    return json.dumps({"student_id": student_id, "concept": 0.53, "reading": 0.3},
                      ensure_ascii=False)


def _model(mode, store_dir, inner=None, retries=0):
    """照 build_model_client 的同一条形状建客户端（record 阶段要换内层替身，所以在这里组）。"""
    transport = None if mode == lt.LIVE else lt.CassetteTransport(
        store=lt.CassetteStore(store_dir), mode=mode, inner=inner)
    client = httpx.AsyncClient(transport=transport, **lt.SDK_CLIENT_KWARGS)
    from langchain_openai import ChatOpenAI
    return ChatOpenAI(model="deepseek-chat", api_key="sk-spike",
                      base_url="https://api.deepseek.com/v1", temperature=0.3,
                      max_tokens=4096, stream_usage=True, max_retries=retries,
                      http_async_client=client)


def _run_agent(model):
    async def _go():
        agent = create_react_agent(model, [diagnose_student])
        return await agent.ainvoke({"messages": [("user", "诊断 S-1 的障碍分布")]})
    return asyncio.run(_go())


# ── 去风险本体 ────────────────────────────────────────────────────────────

def test_record_then_replay_completes_a_tool_call_round_without_the_network(tmp_path):
    inner = ScriptedAsync([TOOL_CALL_JSON, FINAL_JSON])
    store_dir = str(tmp_path / "cassettes")

    first = _run_agent(_model(lt.RECORD, store_dir, inner=inner))
    assert inner.calls == 2, "record 阶段应当真的走两次网络"
    text_a = first["messages"][-1].content

    # 重放：内层 transport 不给 —— 只要有一次真网络企图就会当场炸
    second = _run_agent(_model(lt.REPLAY, store_dir))
    assert second["messages"][-1].content == text_a
    kinds = [type(m).__name__ for m in second["messages"]]
    assert "ToolMessage" in kinds, f"工具那一轮没重放出来：{kinds}"


def test_non_stream_tool_call_reassembles_identically_on_replay(tmp_path):
    store_dir = str(tmp_path / "cassettes")
    inner = ScriptedAsync([TOOL_CALL_JSON])
    msg = asyncio.run(_model(lt.RECORD, store_dir, inner=inner).ainvoke("诊断 S-1"))
    assert msg.tool_calls and msg.tool_calls[0]["name"] == "diagnose_student"

    replayed = asyncio.run(_model(lt.REPLAY, store_dir).ainvoke("诊断 S-1"))
    assert replayed.tool_calls == msg.tool_calls


def _drain(model, prompt):
    """流式跑完，返回 (逐块列表, 累加后的整条消息)。

    为什么要累加：langchain 把 tool_calls 的解析放在**累加之后**（单块的
    `tool_calls` 一直是空的，只有 `tool_call_chunks`）。磁带放的是原始 SSE 字节，
    解析归上层，所以断言也必须落在累加后的消息上 —— 与共识 Q5「录原始字节、
    断言在解析后」是同一条。
    """
    async def _one():
        parts = []
        combined = None
        async for part in model.astream(prompt):
            parts.append(part)
            combined = part if combined is None else combined + part
        return parts, combined
    return asyncio.run(_one())


def test_streamed_tool_call_deltas_reassemble_on_replay(tmp_path):
    """流式 delta 拼不出 tool_calls 的话，整条路就没法测 —— 这是最可能碎的地方。"""
    store_dir = str(tmp_path / "cassettes")
    inner = ScriptedAsync([TOOL_CALL_SSE])

    _, recorded = _drain(_model(lt.RECORD, store_dir, inner=inner), "诊断 S-1")
    _, replayed = _drain(_model(lt.REPLAY, store_dir), "诊断 S-1")
    assert replayed.tool_calls == recorded.tool_calls
    assert [tc["name"] for tc in replayed.tool_calls] == ["diagnose_student"], replayed.tool_calls
    assert replayed.tool_calls[0]["args"] == {"student_id": "S-1"}


def test_usage_from_the_stream_survives_replay(tmp_path):
    """「上下文已用 N%」靠的就是这个数；重放丢了它，前端就只能拿估算值。"""
    store_dir = str(tmp_path / "cassettes")
    inner = ScriptedAsync([FINAL_SSE])

    parts, recorded = _drain(_model(lt.RECORD, store_dir, inner=inner), "讲一下盐类水解")
    before = dict(recorded.usage_metadata or {})
    text_a = "".join(m.content for m in parts)

    parts, replayed = _drain(_model(lt.REPLAY, store_dir), "讲一下盐类水解")
    after = dict(replayed.usage_metadata or {})
    assert after == before and after.get("input_tokens") == 137, after
    assert "".join(m.content for m in parts) == text_a == "该生主导障碍是概念理解"


def _chain(err):
    """顺着 __cause__ / __context__ 往下收 —— openai 会把 transport 抛的异常包一层。"""
    seen, cur, out = set(), err, []
    while cur is not None and id(cur) not in seen:
        seen.add(id(cur))
        out.append(cur)
        cur = cur.__cause__ or cur.__context__
    return out


def test_agent_replay_with_a_changed_prompt_misses_and_never_reaches_the_network(tmp_path):
    """Q3 在 agent 回路里的形状：请求变了就硬失败，而且不掉头去联网。

    为什么要单独验、且不照 `pytest.raises(CassetteMiss)` 写：openai 的 `_base_client`
    用 `except Exception` 把 transport 抛出的任何东西包成 `APIConnectionError`，miss
    不会原样冒出来。而"抛了个异常"又太松 —— 一条真网络错误也算过。replay 模式下
    inner 是 None，真联网会炸成 httpx.ConnectError，所以只有"异常链里能找到
    CassetteMiss"才同时证硬失败与不联网这两件事。
    """
    store_dir = str(tmp_path / "cassettes")
    inner = ScriptedAsync([TOOL_CALL_JSON, FINAL_JSON])
    _run_agent(_model(lt.RECORD, store_dir, inner=inner))

    async def _changed():
        agent = create_react_agent(_model(lt.REPLAY, store_dir), [diagnose_student])
        return await agent.ainvoke({"messages": [("user", "换成诊断 S-9 的障碍分布")]})
    with pytest.raises(Exception) as err:
        asyncio.run(_changed())
    chain = _chain(err.value)
    assert any(isinstance(e, lt.CassetteMiss) for e in chain), \
        f"异常链里没有 miss：{[type(e).__name__ for e in chain]}"


# ── 生产路径不受影响的两条护栏 ────────────────────────────────────────────

def test_live_client_keeps_the_sdks_timeouts_and_limits(monkeypatch):
    """注入客户端不能顺手改掉生产那条请求的超时、连接上限与重定向语义。

    直接跟 openai 自己的默认常量比，而不是比 hardcoded 数值 —— 升级 SDK 后若默认值变了，
    这条会红，提醒我们同步 `SDK_TIMEOUT`/`SDK_LIMITS`；比字面量只会把旧值钉死。
    """
    from openai._constants import DEFAULT_CONNECTION_LIMITS, DEFAULT_TIMEOUT

    monkeypatch.setenv("CHEMAI_LLM_MODE", lt.LIVE)
    assert lt.SDK_TIMEOUT == DEFAULT_TIMEOUT, f"超时默认值漂了：{lt.SDK_TIMEOUT}"
    assert lt.SDK_LIMITS == DEFAULT_CONNECTION_LIMITS, f"连接上限漂了：{lt.SDK_LIMITS}"
    client = lt.build_model_client()
    assert client.is_closed is False
    assert client.follow_redirects is True, "SDK 建客户端时会 setdefault(follow_redirects=True)"
    asyncio.run(client.aclose())


def test_replay_without_a_cassette_dir_refuses_to_start(monkeypatch):
    """忘了设磁带目录就该当场抛，而不是悄悄退回 live。"""
    monkeypatch.setenv("CHEMAI_LLM_MODE", lt.REPLAY)
    monkeypatch.delenv("CHEMAI_CASSETTE_DIR", raising=False)
    with pytest.raises(RuntimeError):
        lt.build_model_client()


@pytest.mark.parametrize("mode", [lt.LIVE, lt.RECORD, lt.REPLAY])
def test_guard_factory_wires_the_transport(tmp_path, monkeypatch, mode, fake_deepseek_key):
    """生产工厂 `get_langchain_model` 必须走同一条路 —— 否则测试测的是一条、跑的是另一条。

    顺带钉住重试：三种模式一律留 2 次（ADR-0023 决策 10）。record 留了才把 429→200 记成
    同一盘带的两条；replay 也必须留 —— 关成 0 的话 SDK 拿到磁带里第一条 429 就抛
    RateLimitError，录进去的带反而放不出来，失败长得像"供应商挂了"。
    """
    from agent.guard import get_langchain_model
    os.makedirs(tmp_path / "cassettes", exist_ok=True)  # replay 不建目录，见 `_engine_for`
    monkeypatch.setenv("CHEMAI_LLM_MODE", mode)
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", str(tmp_path / "cassettes"))
    model = get_langchain_model("deepseek")
    # root_client 是同步那条；我们注入的是 async 通道，落在 root_async_client 上。
    transport = model.root_async_client._client._transport
    assert model.max_retries == 2, f"三种模式都得留 2 次重试，当前 {model.max_retries}"
    if mode == lt.LIVE:
        assert not isinstance(transport, lt.CassetteTransport), "live 下这层必须是透明的"
    else:
        assert isinstance(transport, lt.CassetteTransport), \
            f"工厂没把接缝接上，replay 会静默走网络：{transport}"
        assert transport.mode == mode


def test_a_tape_containing_a_rate_limit_still_replays_through_the_sdk(tmp_path):
    """录进去的 429→200 必须能在 replay 里被 SDK 的重试走完。

    不对称时（replay 关重试）SDK 拿到第一条 429 就抛 RateLimitError：一次真实限流让那盘带
    永久不可重放，而失败长得像"供应商挂了"。这条就是决策 10 两半一起改的绊线。
    """
    store_dir = str(tmp_path / "cassettes")
    body, ct = _completion({"role": "assistant", "content": "水解是可逆的"}, content=None,
                           usage={"prompt_tokens": 12, "completion_tokens": 3, "total_tokens": 15})
    rate = httpx.Response(429, headers={"content-type": ct},
                          content=json.dumps({"error": {"message": "rate limit",
                                                        "type": "rate_limit_error"}}).encode())

    class RateThenOk(ScriptedAsync):
        async def handle_async_request(self, request):
            self.calls += 1
            return rate if self.calls == 1 else httpx.Response(200, headers={"content-type": ct},
                                                              content=body)

    inner = RateThenOk([])
    # retries=2 与生产工厂一致（`get_langchain_model` 三种模式都留 2 次），
    # 其余用例用 0：那里要证的是形状与不联网，不是退避。
    first = asyncio.run(_model(lt.RECORD, store_dir, inner=inner, retries=2).ainvoke("水解是什么"))
    assert first.content == "水解是可逆的" and inner.calls == 2, "record 阶段 SDK 应当重试一次"

    # 重放要用同一个调用形状（ainvoke）：换成 astream 请求体就多带 stream/stream_options，
    # 那是另一盘磁带，测不到重试。
    replayed = asyncio.run(_model(lt.REPLAY, store_dir, retries=2).ainvoke("水解是什么"))
    assert replayed.content == first.content, "重放必须把这盘带放成同一个答案"
