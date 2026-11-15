"""录放带接缝（ADR-0023 提案）：命中键、重放形状、miss 硬失败。

共识里定死的几件事，逐条钉：
· 键含 provider + model + 归一化 body —— 换模型即整批 miss，逼一次显式重录；
· Authorization / 时间戳一类易变字段不进键，messages 与 tools 一定进；
· 重放回的是录制时的原始字节（流式就是整串 SSE），不改写、不重组；
· 同一键按录制顺序多次重放，用完即 miss —— 网络级重试的 429→200 序列才测得到；
· 默认 live：没显式设模式就绝不悄悄进入录放（生产误开 record 会把真实流量写进仓库）。
"""
import asyncio
import json
import os
import pathlib

import httpx
import pytest

from app.services.llm_transport import (
    CassetteMiss, CassetteStore, CassetteSyncTransport, CassetteTransport,
    LIVE, RECORD, REPLAY, _engine_for, _provider_of, model_sync_transport, model_transport,
    refuse_recording_in_server, replay_scope,
    normalize_request, normalize_usage,
    prompt_tokens_of, request_key, resolve_mode,
)

URL = "https://api.deepseek.com/v1/chat/completions"


def _body(model="deepseek-chat", content="把 0.5mol 的 H2SO4 稀释到 250mL", tools=None):
    b = {"model": model, "messages": [{"role": "user", "content": content}],
         "temperature": 0.3, "max_tokens": 4096}
    if tools is not None:
        b["tools"] = tools
    return json.dumps(b, ensure_ascii=False).encode()


def _client(transport):
    return httpx.AsyncClient(transport=transport)


def _get(client):
    """跑一次 POST，返回 (status, body_bytes)。"""
    headers = {"Authorization": "***", "Content-Type": "application/json"}

    async def _run():
        async with client as c:
            r = await c.post(URL, headers=headers, content=_body())
            return r.status_code, r.content
    return asyncio.run(_run())


def _store(tmp_path):
    return CassetteStore(str(tmp_path / "cassettes"))


# ── 命中键 ──────────────────────────────────────────────────────────────

def test_a_rotated_api_key_does_not_invalidate_the_tape(tmp_path):
    """密钥轮换不该让整批磁带失效 —— 请求头根本不进键（Q4）。

    判据走通路而不是 `request_key`：那个函数的签名里就没有 headers 参数，拿它自比一次是同义
    反复（上一版正是这样写的，标题里的 "ignores authorization" 因此一条证据都没有）。录的
    时候用 key-A、放的时候换 key-B，仍然命中才是"头没参与"的证明。
    """
    payload = _body()
    store = _store(tmp_path)

    class FakeInner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, json={"ok": True})

    async def _go(mode, api_key):
        transport = CassetteTransport(store=store, mode=mode, inner=FakeInner())
        async with _client(transport) as c:
            r = await c.post(URL, headers={"Authorization": f"Bearer {api_key}"},
                             content=payload)
            return r.status_code, r.json()

    assert asyncio.run(_go(RECORD, "sk-A")) == (200, {"ok": True})
    assert asyncio.run(_go(REPLAY, "sk-B")) == (200, {"ok": True}), \
        "换了密钥就 miss：请求头进键了"
    # 密钥原文也不许留在磁带里（要进 git 给人 review）
    tape = store.tape(request_key(URL, payload))
    assert "sk-A" not in json.dumps(tape, ensure_ascii=False)


def test_key_includes_model_because_a_new_model_is_not_a_regression_baseline():
    """换模型即 miss：拿旧模型的响应测新模型不叫回归（共识 Q11）。"""
    old = request_key(URL, _body(model="deepseek-chat"))
    new = request_key(URL, _body(model="deepseek-reasoner"))
    assert old != new
    assert "deepseek-chat" in old and "deepseek-reasoner" in new


def test_key_includes_tools_and_messages():
    """工具描述是模型选择的唯一输入（CONTEXT.md），改了就必须 miss。"""
    base = request_key(URL, _body())
    with_tool = request_key(URL, _body(tools=[{"type": "function",
                                              "function": {"name": "diagnose_student"}}]))
    other_text = request_key(URL, _body(content="换一道题"))
    assert base != with_tool != other_text


def test_normalize_drops_volatile_body_fields_but_keeps_messages():
    raw = json.loads(_body().decode())
    raw.update({"user": "u-9", "timestamp": "2026-09-27T10:00:00Z", "seed": 42})
    norm = normalize_request(raw)
    assert "user" not in norm and "timestamp" not in norm and "seed" not in norm
    assert norm["messages"][0]["content"].startswith("把 0.5mol")


def test_unparseable_body_still_gets_a_stable_key():
    """非 JSON 请求体（少数端点会发）也要能录能放，键只按字节算。"""
    raw = b"not-json"
    assert request_key(URL, raw) == request_key(URL, raw)


def test_two_get_queries_do_not_share_a_tape(tmp_path):
    """Bing 兜底那条是 `GET /search?q=<检索词>`，请求体为空 —— 只按 body 算键的话，任意两个
    检索词撞成同一盘带，重放时拿"氧气"的网页去答"钠"的问题，而且一声不响。

    判据是回出来的内容对得上问的那个词，不是键不相等：撞了键以后两次重放都"成功"，只有
    内容会说话。
    """
    from urllib.parse import quote
    base = "https://www.bing.com/search"
    store = _store(tmp_path)

    class FakeInner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, text="结果:" + httpx.URL(request.url).params["q"])

    async def _go(mode, q):
        transport = CassetteTransport(store=store, mode=mode, inner=FakeInner())
        async with _client(transport) as c:
            r = await c.get(f"{base}?q={quote(q)}")
            return r.text

    for q in ("氧气 的性质", "钠 的化合物"):
        assert asyncio.run(_go(RECORD, q)) == f"结果:{q}"
    assert store.count() == 2, "两条检索词该各一盘带，撞了才是一条"
    for q in ("氧气 的性质", "钠 的化合物"):
        assert asyncio.run(_go(REPLAY, q)) == f"结果:{q}"


def test_a_rotating_access_token_in_the_query_does_not_miss(tmp_path):
    """凭证放在查询串上的端点（本仓的百度通路正是这种形状，`grading_client.py:58`）：令牌
    每两小时轮换一次，照原值算键就是每两小时整批 miss —— 与"请求头不进键"同一个理由。

    打码只打在键与留档里；真实请求照原样发出去（record 走的是 `request.url`，不是这份摘要）。
    """
    def _url(token, image_id):
        return (f"https://ai.baidu.com/pic/v1/query?access_token={token}"
                f"&image_id={image_id}")

    same = request_key(_url("T-1", "9"), b"")
    assert same == request_key(_url("T-2", "9"), b""), "换了令牌就 miss：查询串没打码"
    assert same != request_key(_url("T-1", "8"), b""), "换了图片却命中同一盘：正事没进键"
    assert "T-1" not in same and "access_token" not in same, "键里躺着原文，而文件名要进 git"

    store = _store(tmp_path)

    class FakeInner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, text="cut")

    async def _go(mode):
        transport = CassetteTransport(store=store, mode=mode, inner=FakeInner())
        async with _client(transport) as c:
            return (await c.get(_url("LIVE-TOKEN-abc", "9"))).text
    asyncio.run(_go(RECORD))
    blob = json.dumps(store.tape(request_key(_url("LIVE-TOKEN-abc", "9"), b"")),
                      ensure_ascii=False)
    assert "LIVE-TOKEN-abc" not in blob, "活令牌落进磁带文件了"
    assert "image_id=9" in blob, "留档里没有查询串，miss 报告就说不清差在哪"
    assert asyncio.run(_go(REPLAY)) == "cut", "磁带里的 URL 剥了查询串，重放却要靠它命中"


# ── 重放与录制 ──────────────────────────────────────────────────────────

def test_replay_returns_the_recorded_bytes_verbatim(tmp_path):
    payload = _body()
    key = request_key(URL, payload)
    store = _store(tmp_path)
    store.record(key, URL, 200, {"content-type": "text/event-stream"},
                 b'data: {"choices":[{"delta":{"content":"\xe7\xa8\x80\xe9\x87\x8a"}}]}\n\n')

    status, body = _get(_client(CassetteTransport(store=store, mode=REPLAY)))

    assert status == 200
    assert body == b'data: {"choices":[{"delta":{"content":"\xe7\xa8\x80\xe9\x87\x8a"}}]}\n\n'


def test_replay_miss_fails_loudly_and_names_the_closest_tapes(tmp_path):
    """miss 不开真网络、不猜：报错并说清与最接近的差在哪几个字段（共识 Q3）。"""
    store = _store(tmp_path)
    store.record(request_key(URL, _body()), URL, 200, {}, b"data: {}")

    async def _run():
        async with _client(CassetteTransport(store=store, mode=REPLAY)) as c:
            return await c.post(URL, json={"model": "deepseek-chat",
                                           "messages": [{"role": "user", "content": "完全不同的问题"}]})
    with pytest.raises(CassetteMiss) as err:
        asyncio.run(_run())
    msg = str(err.value)
    assert "miss" in msg.lower()
    assert "messages" in msg, f"应指出差在哪：{msg}"


def test_same_key_replays_in_recorded_order_then_exhausts(tmp_path):
    """429 → 200 这种重试序列必须原样重放；用完就是 miss，不静默复用最后一条。"""
    payload = _body()
    key = request_key(URL, payload)
    store = _store(tmp_path)
    store.record(key, URL, 429, {}, b'{"error":"rate"}')
    store.record(key, URL, 200, {}, b'{"ok":true}')

    transport = CassetteTransport(store=store, mode=REPLAY)

    async def _once():
        async with _client(transport) as c:
            r = await c.post(URL, content=payload)
            return r.status_code, r.content
    assert asyncio.run(_once()) == (429, b'{"error":"rate"}')
    assert asyncio.run(_once()) == (200, b'{"ok":true}')
    with pytest.raises(CassetteMiss):
        asyncio.run(_once())


def test_record_writes_then_replays_and_still_hits_the_network(tmp_path):
    """record 模式：原样透传给内层 transport，同时落盘；不改变行为。"""
    calls = []

    class FakeInner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            calls.append(await request.aread())
            return httpx.Response(200, json={"recorded": True})

    store = _store(tmp_path)
    payload = _body()

    async def _once(mode):
        async with _client(CassetteTransport(store=store, mode=mode,
                                             inner=FakeInner())) as c:
            r = await c.post(URL, headers={"authorization": "***"}, content=payload)
            return r.status_code, r.json() if 'json' in r.headers.get('content-type', '') else r.content
    assert asyncio.run(_once(RECORD)) == (200, {"recorded": True})
    assert len(calls) == 1, "record 模式必须真的走网络，否则录不到东西"

    key = request_key(URL, payload)
    assert store.tape(key) is not None


def test_live_mode_never_touches_the_cassette(tmp_path):
    store = _store(tmp_path)
    seen = []

    class FakeInner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            seen.append(1)
            return httpx.Response(200, text="live")

    async def _run():
        async with _client(CassetteTransport(store=store, mode=LIVE, inner=FakeInner())) as c:
            return await c.get(URL)
    r = asyncio.run(_run())
    assert r.text == "live" and seen == [1]
    assert os.listdir(store.root) == [] or store.count() == 0, "live 不该写盘"


def test_recording_a_compressed_response_does_not_decode_it_twice(tmp_path):
    """端点报 `content-encoding: gzip` 时，transport 层读到的已是 httpx 解开的字节。

    重建响应若照抄那个头，客户端会对同一份字节再解一次 —— record 当场 `DecodingError`
    （实测），而这是只有真录制时才会撞上的路：DeepSeek/OpenAI 兼容端点确实会压。
    存盘那份已经剥掉头（`_SKIP_RESPONSE_HEADERS`），所以只有返回路径会炸。
    """
    import gzip
    plain = b'{"ok": true}'

    class GzInner(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request):
            return httpx.Response(200, headers={"content-type": "application/json",
                                                "content-encoding": "gzip"},
                                  content=gzip.compress(plain))

    store = _store(tmp_path)

    async def _once(mode, inner=None):
        async with _client(CassetteTransport(store=store, mode=mode, inner=inner)) as c:
            r = await c.post(URL, content=_body())
            return r.status_code, r.content
    assert asyncio.run(_once(RECORD, GzInner())) == (200, plain), "record 不该炸或再压一层"
    assert asyncio.run(_once(REPLAY)) == (200, plain), "重放必须交出同一份字节"


def test_recording_a_compressed_response_survives_the_synchronous_egress_too(tmp_path):
    """同步出口（`LLMService`）也得扛得住压缩响应：它与异步那条共用逻辑，但走不同返回路径。"""
    import gzip
    plain = b'{"ok": true}'

    class GzInner(httpx.BaseTransport):
        def handle_request(self, request):
            return httpx.Response(200, headers={"content-type": "application/json",
                                                "content-encoding": "gzip"},
                                  content=gzip.compress(plain))

    store = _store(tmp_path)

    def _once(mode):
        with httpx.Client(transport=CassetteSyncTransport(store=store, mode=mode,
                                                           inner=GzInner() if mode == RECORD else None)) as c:
            r = c.post(URL, content=_body())
            return r.status_code, r.content
    assert _once(RECORD) == (200, plain)
    assert _once(REPLAY) == (200, plain)


def test_replay_cursor_is_process_wide_not_per_client(tmp_path, monkeypatch):
    """`LLMService` 每次调用新建客户端；游标若按实例存，429→200 的磁带永远放回 429。

    Q10 说的是"同一键按录制顺序多次重放"，那只能挂在进程上：真实通路里没有一个对象
    活得过一次以上的调用。这里走 `model_transport()` 工厂（生产那条路），两次调用
    各自新建 transport，仍要依次拿到 429 与 200，第三条响到 exhausted。
    """
    monkeypatch.setenv("CHEMAI_LLM_MODE", REPLAY)
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", str(tmp_path / "cassettes"))
    store = CassetteStore(str(tmp_path / "cassettes"))
    payload = _body()
    key = request_key(URL, payload)
    store.record(key, URL, 429, {}, b'{"error":"rate"}')
    store.record(key, URL, 200, {}, b'{"ok":true}')

    async def _once():
        async with httpx.AsyncClient(transport=model_transport()) as c:
            r = await c.post(URL, content=payload)
            return r.status_code, r.content
    assert asyncio.run(_once()) == (429, b'{"error":"rate"}')
    assert asyncio.run(_once()) == (200, b'{"ok":true}')
    with pytest.raises(CassetteMiss, match="exhausted"):
        asyncio.run(_once())


# ── 模式默认值（生产与 CI 的安全边界）────────────────────────────────────

def test_mode_defaults_to_live(monkeypatch):
    monkeypatch.delenv("CHEMAI_LLM_MODE", raising=False)
    assert resolve_mode() == LIVE


def test_replay_mode_requires_a_cassette_dir(monkeypatch, tmp_path):
    monkeypatch.setenv("CHEMAI_LLM_MODE", REPLAY)
    monkeypatch.delenv("CHEMAI_CASSETTE_DIR", raising=False)
    with pytest.raises(RuntimeError) as err:
        resolve_mode()
    assert "CHEMAI_CASSETTE_DIR" in str(err.value)


# ── 用量解析（Q14：收进这条缝，字段名各家不同）──────────────────────────────

def test_normalize_usage_accepts_the_two_provider_spellings():
    """OpenAI 兼容端点报 prompt/completion，LangChain 与部分端点报 input/output。

    「上下文已用 N%」和往后的成本统计都只能有一个口径，否则同一轮对话在两个地方
    显示两个数（共识 Q14）。
    """
    a = normalize_usage({"prompt_tokens": 137, "completion_tokens": 8, "total_tokens": 145})
    b = normalize_usage({"input_tokens": 137, "output_tokens": 8, "total_tokens": 145})
    assert a == b == {"prompt_tokens": 137, "completion_tokens": 8, "total_tokens": 145}


def test_normalize_usage_derives_the_total_when_a_provider_omits_it():
    got = normalize_usage({"prompt_tokens": 90, "completion_tokens": 10})
    assert got["total_tokens"] == 100


def test_normalize_usage_reads_the_nested_token_usage_shape():
    """LangChain 的 `response_metadata.token_usage` 是同一件事的第三种写法。"""
    assert normalize_usage({"token_usage": {"prompt_tokens": 5, "completion_tokens": 2,
                                            "total_tokens": 7}})["prompt_tokens"] == 5


def test_normalize_usage_on_nothing_is_honest_about_nothing():
    assert normalize_usage(None) is None
    assert normalize_usage({}) is None
    assert normalize_usage({"reasoning_tokens": 3}) is None, "认不出的字段不该被猜成用量"


def test_prompt_tokens_of_prefers_the_provider_number_over_estimates():
    class _Out:
        usage_metadata = {"input_tokens": 1200, "output_tokens": 20, "total_tokens": 1220}
        response_metadata = {}
    assert prompt_tokens_of(_Out()) == 1200


def test_prompt_tokens_of_falls_back_to_response_metadata():
    class _Out:
        usage_metadata = None
        response_metadata = {"token_usage": {"prompt_tokens": 812}}
    assert prompt_tokens_of(_Out()) == 812


def test_prompt_tokens_of_takes_the_last_of_a_streamed_list():
    """流式聚合会给一串 output，最后那条才带齐 usage。"""
    class _Out:
        def __init__(self, u):
            self.usage_metadata = u
            self.response_metadata = {}
    assert prompt_tokens_of([_Out({"input_tokens": 10}), _Out({"input_tokens": 640})]) == 640


def test_prompt_tokens_of_returns_none_rather_than_zero():
    """0 与"没有"必须能区分：`build_context_event` 靠 None 决定要不要回退估算。"""
    assert prompt_tokens_of(None) is None
    assert prompt_tokens_of(object()) is None
    assert prompt_tokens_of({"usage": {"prompt_tokens": 0}}) is None



# ── 键的可移植性与留档形状（第三轮 /review 抓到的）─────────────────────────

def test_provider_label_is_the_vendor_not_the_first_host_label():
    """`open.bigmodel.cn` 的第一段是 "open"，不是供应商标。

    这个名字会出现在磁带文件名、`near()` 的分组和日志字段里，错了三处一起错。
    """
    assert _provider_of("https://api.deepseek.com/v1/chat/completions") == "deepseek"
    assert _provider_of("https://open.bigmodel.cn/api/paas/v4/chat/completions") == "bigmodel"
    assert _provider_of("https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions") == "aliyuncs"
    assert _provider_of("https://api.xiaomimimo.com/v1/chat/completions") == "xiaomimimo"


def test_the_endpoint_is_part_of_the_key():
    """同一台 host 的两个端点不能互吃对方的重放序号。

    `DeepSeekProvider` 打 `/beta/chat/completions`，`LLMService` 打
    `/v1/chat/completions`，body 完全一样 —— 键里不含路径时两者命中同一盘磁带，
    进程级游标下第二个请求会拿到第一个的重试序列。
    """
    body = _body()
    v1 = request_key("https://api.deepseek.com/v1/chat/completions", body)
    beta = request_key("https://api.deepseek.com/beta/chat/completions", body)
    assert v1 != beta
    assert "v1:chat:completions" in v1 and "beta:chat:completions" in beta


def test_a_tape_is_not_pinned_to_the_machine_that_recorded_it(monkeypatch):
    """系统提示把 `Path.home()` 原文拼进 messages —— 不归一，评估在家目录不同的机器上必 miss。"""
    import pathlib as _p
    def body(home):
        return json.dumps({"model": "deepseek-chat", "messages": [
            {"role": "system", "content": f"用户主目录: {home}；桌面目录通常是: {home}/Desktop"},
            {"role": "user", "content": "读一下桌面上的成绩表"}]}, ensure_ascii=False).encode()
    monkeypatch.setattr(_p.Path, "home", staticmethod(lambda: _p.PurePosixPath("C:/Users/Administrator")))
    a = request_key(URL, body("C:/Users/Administrator"))
    monkeypatch.setattr(_p.Path, "home", staticmethod(lambda: _p.PurePosixPath("/home/runner")))
    b = request_key(URL, body("/home/runner"))
    assert a == b, "同一份提示换机器就 miss，磁带等于绑死在一台开发机上"
    # 真提示词不同（家目录不同）仍必须不同键：只归一"本机主目录"这一个串
    assert request_key(URL, body("{HOME}")) == a


def test_answer_sheet_images_are_not_stored_verbatim_but_still_distinct(tmp_path):
    """家长/批改路径会把整张手写答卷以 data URI 塞进 messages：留档换成摘要，键仍区分不同图片。"""
    def img(blob):
        return json.dumps({"model": "deepseek-chat", "messages": [
            {"role": "user", "content": [{"type": "image_url",
                                          "image_url": {"url": f"data:image/png;base64,{blob}"}}]}]},
            ensure_ascii=False).encode()
    a, b = img("QUJD" * 40), img("RElU" * 40)
    assert request_key(URL, a) != request_key(URL, b), "两张不同的答卷不能放中同一盘带"
    store = _store(tmp_path)
    store.record(request_key(URL, a), URL, 200, {}, b"{}", normalized=normalize_request(
        json.loads(a.decode())))
    tape = json.loads(open(store._path(request_key(URL, a)), encoding="utf-8").read())
    dumped = json.dumps(tape["normalized"], ensure_ascii=False)
    assert "base64,QUJD" not in dumped and "data-uri sha256=" in dumped


def test_a_corrupt_tape_is_a_miss_not_a_crash(tmp_path, caplog):
    """磁带要进 git 靠人改，缺字段/坏字节必须仍报 CassetteMiss。

    KeyError 与 JSONDecodeError 不像 `CassetteMiss` 那样被上层放行，会被 `except Exception`
    认成"接口抖动"或"换下一个 provider 再试" —— 磁带损坏伪装成供应商故障，是最难查的一种。
    """
    store = _store(tmp_path)
    good = request_key(URL, _body())
    store.record(good, URL, 200, {}, b'{"ok":true}')
    (tmp_path / "cassettes" / "stray.json").write_text('{"no_key": 1}', encoding="utf-8")
    broken = store._path(good)
    with open(broken, "w", encoding="utf-8") as f:
        f.write('{"key": "x", "entries": [')  # 截断

    transport = CassetteTransport(store=store, mode=REPLAY)

    async def _run():
        async with _client(transport) as c:
            return await c.post(URL, content=_body())
    with caplog.at_level("WARNING", logger="chemai.model_egress"), pytest.raises(CassetteMiss):
        asyncio.run(_run())
    assert any("cassette_unreadable" in r.getMessage() for r in caplog.records)


def test_concurrent_recording_keeps_both_entries(tmp_path):
    """两个出口并发往同一键录带：读-改-写没有锁的话后写会把先写的整盘盖掉。"""
    from concurrent.futures import ThreadPoolExecutor
    store = _store(tmp_path)
    key = request_key(URL, _body())

    def _one(seq):
        store.record(key, URL, 429 if seq == 0 else 200, {}, f'{{"seq":{seq}}}'.encode())
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(_one, range(8)))
    tape = store.tape(key)
    assert len(tape["entries"]) == 8, f"丢了 {8 - len(tape['entries'])} 条录制"
    assert not [f for f in os.listdir(store.root) if f.endswith(".tmp")], "临时文件没清干净"


@pytest.mark.parametrize("evil", ["../../../evil", "..\..\..\evil", "a/../b", "v1:chat:completions"])
def test_the_tape_name_stays_inside_the_cassette_dir(tmp_path, evil):
    """模型名来自请求体，落盘前逐段净化：反斜杠在 Windows 上是分隔符，不净化就能写出盘外。

    （改前的实测形状：`{"model": "..\..\evil"}` 经 `os.path.join` 逃到 root 之外，
    还把目标 .json 整个覆盖。现在两段都过不了净化。）
    """
    store = _store(tmp_path)
    path = store._path(request_key(URL, _body(model=evil)))
    assert os.path.realpath(path).startswith(os.path.realpath(store.root) + os.sep), path
    assert not set(os.listdir(store.root)) - {os.path.basename(path), os.path.basename(path) + ".tmp"}


# ── 模式守卫：非 live 一定要一个能用的目录 ────────────────────────────────

@pytest.mark.parametrize("mode", [RECORD, REPLAY])
def test_non_live_modes_refuse_to_start_without_a_dir(monkeypatch, mode):
    """`record` 曾经给的是 `KeyError: 'CHEMAI_CASSETTE_DIR'`，抛在一个名字不提目录的函数里。"""
    monkeypatch.setenv("CHEMAI_LLM_MODE", mode)
    monkeypatch.delenv("CHEMAI_CASSETTE_DIR", raising=False)
    with pytest.raises(RuntimeError, match="CHEMAI_CASSETTE_DIR"):
        model_transport()


def test_replay_refuses_a_dir_that_does_not_exist(monkeypatch, tmp_path):
    """replay 是只读路径：目录打错字要当场说"不存在"，不能建个空的然后每盘都 miss。"""
    monkeypatch.setenv("CHEMAI_LLM_MODE", REPLAY)
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", str(tmp_path / "typo-dir"))
    with pytest.raises(RuntimeError, match="不存在"):
        model_transport()


def test_an_unknown_mode_is_an_error_not_a_silent_live(monkeypatch):
    """`CHEMAI_LLM_MODE=recrod` 若当成 live 跑，评估会以为自己在重放，实际在花钱。"""
    monkeypatch.setenv("CHEMAI_LLM_MODE", "recrod")
    with pytest.raises(RuntimeError, match="CHEMAI_LLM_MODE"):
        resolve_mode()


def test_the_replay_cursor_is_shared_by_the_sync_and_async_egress(tmp_path, monkeypatch):
    """两个出口共用一条游标：异步取完 429，同步那一次就该拿到 200。

    ADR 说"同一盘磁带在两个出口上表现一字不差"，游标若各存一份就只是嘴上说一样。
    """
    d = str(tmp_path / "cassettes")
    store = CassetteStore(d)
    payload = _body()
    key = request_key(URL, payload)
    store.record(key, URL, 429, {}, b'{"error":"rate"}')
    store.record(key, URL, 200, {}, b'{"ok":true}')
    monkeypatch.setenv("CHEMAI_LLM_MODE", REPLAY)
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", d)

    async def _async_once():
        async with httpx.AsyncClient(transport=model_transport()) as c:
            return (await c.post(URL, content=payload)).status_code

    def _sync_once():
        with httpx.Client(transport=model_sync_transport()) as c:
            return c.post(URL, content=payload).status_code

    assert asyncio.run(_async_once()) == 429
    assert _sync_once() == 200
    with pytest.raises(CassetteMiss, match="exhausted"):
        asyncio.run(_async_once())


# ── 运行边界与两个"着力点"（D1–D4 的决定落到代码上）────────────────────────

def test_replay_scope_rewinds_the_cursor(tmp_path, monkeypatch):
    """同一份评估跑第二遍不该被当成"行为变了"（决策 10）。

    不带边界时保持严格：同一个键多调一次就是 exhausted。
    """
    d = str(tmp_path / "cassettes")
    store = CassetteStore(d)
    payload = _body()
    key = request_key(URL, payload)
    store.record(key, URL, 200, {}, b'{"ok":1}')
    monkeypatch.setenv("CHEMAI_LLM_MODE", REPLAY)
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", d)

    async def _once():
        async with httpx.AsyncClient(transport=model_transport()) as c:
            return (await c.post(URL, content=payload)).content
    assert asyncio.run(_once()) == b'{"ok":1}'
    with pytest.raises(CassetteMiss, match="exhausted"):
        asyncio.run(_once())
    with replay_scope("case-1"):
        assert asyncio.run(_once()) == b'{"ok":1}'
    with replay_scope("case-2"):
        assert asyncio.run(_once()) == b'{"ok":1}'


def test_the_environment_can_name_a_process_wide_scope(tmp_path, monkeypatch):
    """`CHEMAI_CASSETTE_SCOPE` = "这个进程就是这一轮评估"（评估 runner 用它）。

    为什么不是 runner 在调用处包一层 `replay_scope`：一条评估里既有序事件循环也有线程池调用，
    而 `threading.Thread` 起始拿到的是 contextvar 的默认值（不继承主线程那份），包在主线程上
    等于只给一半调用起了边界。
    """
    d = str(tmp_path / "cassettes")
    store = CassetteStore(d)
    payload = _body()
    store.record(request_key(URL, payload), URL, 200, {}, b'{"ok":1}')
    monkeypatch.setenv("CHEMAI_LLM_MODE", REPLAY)
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", d)

    async def _once():
        async with httpx.AsyncClient(transport=model_transport()) as c:
            return (await c.post(URL, content=payload)).content

    monkeypatch.delenv("CHEMAI_CASSETTE_SCOPE", raising=False)
    assert asyncio.run(_once()) == b'{"ok":1}'
    with pytest.raises(CassetteMiss, match="exhausted"):
        asyncio.run(_once())

    monkeypatch.setenv("CHEMAI_CASSETTE_SCOPE", "classifier")
    assert asyncio.run(_once()) == b'{"ok":1}', "环境命名的边界没起作用"
    with pytest.raises(CassetteMiss, match="exhausted"):
        asyncio.run(_once())

    # 换标签就是换一本账：同一进程里连跑两条评估，第二条不该接着第一条的序号往下放
    monkeypatch.setenv("CHEMAI_CASSETTE_SCOPE", "trajectory_golden")
    assert asyncio.run(_once()) == b'{"ok":1}'


def test_the_seam_itself_refuses_a_model_call_inside_a_work_unit(tmp_path, db_session):
    """ADR-0021 那条断言挂在缝上：五处构造在 record/replay 下都过这一点。

    live 下 ChatOpenAI 用 langchain 自建客户端，transport 层够不到 —— 那两处业务出口
    （`LLMService._call_with_retry`、`DeepSeekProvider.chat/chat_stream`）与 exam_bank 的
    客户端工厂继续各自判一次，别把"所有真实网络往返"当成已全覆盖。
    """
    from app.services.work_unit import ModelCallInsideWorkUnit, work_unit
    transport = CassetteTransport(store=_store(tmp_path), mode=REPLAY)

    async def _go():
        async with httpx.AsyncClient(transport=transport) as c:
            return await c.post(URL, content=_body())
    with work_unit(db_session), pytest.raises(ModelCallInsideWorkUnit):
        asyncio.run(_go())


def test_a_server_process_refuses_to_boot_while_recording(monkeypatch, tmp_path):
    """决策 7 的着力点：.env 里一行 record 就能让生产逐请求写盘，这里拦住。"""
    monkeypatch.setenv("CHEMAI_LLM_MODE", RECORD)
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", str(tmp_path / "cassettes"))
    with pytest.raises(RuntimeError, match="record"):
        refuse_recording_in_server()


def test_replay_boots_fine_in_a_server_process(monkeypatch, tmp_path):
    """replay 只读磁带，离线复现照走。"""
    d = tmp_path / "cassettes"
    d.mkdir()
    monkeypatch.setenv("CHEMAI_LLM_MODE", REPLAY)
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", str(d))
    refuse_recording_in_server()


def test_main_wires_that_refusal():
    """绊线：`app/main.py` 必须在建 app 之后立即调用它，否则着力点又只剩纪律。"""
    src = open(pathlib.Path(__file__).resolve().parents[1] / "app/main.py",
               encoding="utf-8").read()
    assert "refuse_recording_in_server()" in src.split("app = FastAPI(")[1][:600]


def test_the_boot_guard_reads_the_env_file_itself(monkeypatch, tmp_path):
    """守卫不能指望别人先把 `.env` 读进来：`import app.main` 压根不拉起 `app/config.py`
    （实测它的 sys.modules 里没有 app.config），挂在 main.py 上却看不见 `.env`，挡住的
    就只有"命令行显式 export"那一半 —— 而会被忘在仓库里的正是 `.env` 里那一行。

    不去造一个真 `.env`：本机的 `chemai-backend/.env` 是 gitignored 的开发配置，测试往里写
    会污染真环境，读它又因机器而异。换成替身，钉两件事：加载发生在判模式之前（去掉那句
    `load_dotenv` 这条就红），以及路径按仓库锚定而不是按启动 cwd。
    """
    import dotenv
    seen = []

    def _fake_load(path=None):
        seen.append(str(path))
        monkeypatch.setenv("CHEMAI_LLM_MODE", RECORD)   # 模拟 .env 里那一行
        monkeypatch.setenv("CHEMAI_CASSETTE_DIR", str(tmp_path / "cassettes"))

    monkeypatch.delenv("CHEMAI_LLM_MODE", raising=False)
    monkeypatch.setattr(dotenv, "load_dotenv", _fake_load)
    with pytest.raises(RuntimeError, match="record"):
        refuse_recording_in_server()
    assert len(seen) == 1, "判模式之前没读 .env，守卫只看得到进程环境"
    assert seen[0].endswith(os.path.join("chemai-backend", ".env")), seen[0]


def test_two_replay_scopes_do_not_wipe_each_others_cursor(tmp_path, monkeypatch):
    """一轮评估一个命名空间：另一轮进来不许把这一轮的记账抹掉（决策 10 的后半）。

    上一版进边界时 `clear()` 掉所有引擎的游标，于是并发跑的两轮会互相抹平 —— A 跑到一半，
    B 一进门把 A 的游标归零，A 的第二次调用又拿到第一条响应，"这轮多调了一次"这个信号
    就永远响了不了。这里用事件把交错钉死：A 放过第一条后等 B 真的进了边界，再放第二条。
    """
    import threading

    d = str(tmp_path / "cassettes")
    store = CassetteStore(d)
    payload = _body()
    key = request_key(URL, payload)
    store.record(key, URL, 200, {}, b'{"seq":0}')
    store.record(key, URL, 200, {}, b'{"seq":1}')
    monkeypatch.setenv("CHEMAI_LLM_MODE", REPLAY)
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", d)

    def _once():
        async def _go():
            async with httpx.AsyncClient(transport=model_transport()) as c:
                return (await c.post(URL, content=payload)).content
        return asyncio.run(_go())

    b_inside = threading.Event()
    a_first_done = threading.Event()
    a_got = []

    def _run_a():
        with replay_scope("A"):
            a_got.append(_once())
            a_first_done.set()
            assert b_inside.wait(5), "B 没进边界，这条就没有把交错钉住"
            a_got.append(_once())

    def _run_b():
        assert a_first_done.wait(5), "A 没放过第一条，交错没钉住"
        with replay_scope("B"):
            b_inside.set()      # 信号发在进边界之后：A 的下一次调用必然落在 B 的动作之后
            _once()

    ta, tb = threading.Thread(target=_run_a), threading.Thread(target=_run_b)
    ta.start(), tb.start(), ta.join(15), tb.join(15)
    assert a_got == [b'{"seq":0}', b'{"seq":1}'], \
        f"B 进边界把 A 的游标抹平了：{a_got}"


def test_a_finished_scope_leaves_no_cursor_state_behind(tmp_path, monkeypatch):
    """边界跑完要把它那份游标丢掉：游标活在进程级引擎上，不清就是每个边界攒一份记账。"""
    d = str(tmp_path / "cassettes")
    store = CassetteStore(d)
    payload = _body()
    store.record(request_key(URL, payload), URL, 200, {}, b'{"ok":1}')
    monkeypatch.setenv("CHEMAI_LLM_MODE", REPLAY)
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", d)

    async def _once():
        async with httpx.AsyncClient(transport=model_transport()) as c:
            await c.post(URL, content=payload)

    for _ in range(5):
        with replay_scope("run"):
            asyncio.run(_once())
    engine = _engine_for(REPLAY)
    left = {ns for ns, _ in engine._cursor}
    assert left == set(), f"跑完的边界还留着游标：{left}"
