"""web_search AnySearch 集成测试（2026-08）。

主通道 AnySearch（独立搜索 API，结构化来源可验证）→ 兜底 mimo 内置搜索 → 兜底 Bing 抓取。
锁定：请求格式（URL/Bearer/zone=cn）、结果解析、以及两级回退顺序。
"""
import asyncio
import json

import httpx

from agent.tools.exam_bank import _anysearch_search, web_search

ANYSEARCH_URL = "https://api.anysearch.com/v1/search"
MIMO_URL = "https://api.xiaomimimo.com/v1/chat/completions"


def _run(coro):
    return asyncio.run(coro)


class _FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self._text = text

    def json(self):
        return self._payload

    @property
    def text(self):
        return self._text


class _FakeClient:
    """Scripted httpx.AsyncClient double：按 URL 返回响应，None 键作任意 URL 通配。"""

    def __init__(self, responses=None):
        self.responses = responses or {}
        self.calls = []  # (method, url, kwargs)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    def _pick(self, url):
        resp = self.responses.get(url)
        if resp is None:
            resp = self.responses.get(None)
        if callable(resp):
            resp = resp(url)
        return resp or _FakeResponse(500)

    async def post(self, url, **kwargs):
        self.calls.append(("post", url, kwargs))
        return self._pick(url)

    async def get(self, url, **kwargs):
        self.calls.append(("get", url, kwargs))
        return self._pick(url)


def _anysearch_payload(results=None):
    return {
        "request_id": "r1",
        "code": 0,
        "message": "ok",
        "data": {
            "results": results if results is not None else [
                {"title": "2024年高考化学全国卷真题及答案",
                 "url": "https://example.com/2024-chem",
                 "snippet": "包含第8题物质的量相关计算与解析"},
                {"title": "物质的量知识点总结",
                 "url": "https://example.com/kp",
                 "snippet": "n=m/M=V/Vm=cV 公式梳理"},
            ],
            "metadata": {"total_results": 2, "search_time_ms": 120},
        },
    }


def _no_keys(monkeypatch):
    """隔离环境：清掉所有可能触发额外通道的 key，让测试只走目标通道。"""
    monkeypatch.delenv("ANYSEARCH_API_KEY", raising=False)
    monkeypatch.delenv("XIAOMI_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)


def test_anysearch_search_builds_request_and_parses(monkeypatch):
    """请求格式正确（URL / Bearer / query / max_results / zone=cn / language=zh），
    响应解析为「编号文本 + 来源列表」。"""
    client = _FakeClient({ANYSEARCH_URL: _FakeResponse(200, _anysearch_payload())})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setenv("ANYSEARCH_API_KEY", "as_sk_test")

    text, sources = _run(_anysearch_search("2024 高考化学真题"))

    assert "2024年高考化学全国卷真题及答案" in text
    assert len(sources) == 2
    assert sources[0]["url"] == "https://example.com/2024-chem"

    assert len(client.calls) == 1
    method, url, kwargs = client.calls[0]
    assert method == "post"
    assert url == ANYSEARCH_URL
    assert kwargs["headers"]["Authorization"] == "Bearer as_sk_test"
    body = kwargs["json"]
    assert body["query"] == "2024 高考化学真题"
    assert body["max_results"] == 5
    assert body["zone"] == "cn"  # 中文分区：国内化学教学资料/真题站
    assert body["language"] == "zh"


def test_anysearch_search_missing_key_returns_empty(monkeypatch):
    """无 key：不发起请求，返回空（交给调用方回退）。"""
    client = _FakeClient()
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client)
    _no_keys(monkeypatch)

    text, sources = _run(_anysearch_search("化学"))
    assert text == "" and sources == []
    assert client.calls == []


def test_anysearch_search_http_error_returns_empty(monkeypatch):
    """HTTP 失败（如 key 失效 401）：返回空不抛异常（回退链继续）。"""
    client = _FakeClient({ANYSEARCH_URL: _FakeResponse(401, {"error": {"message": "Invalid API Key"}})})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setenv("ANYSEARCH_API_KEY", "as_sk_bad")

    text, sources = _run(_anysearch_search("化学"))
    assert text == "" and sources == []


def test_web_search_prefers_anysearch_and_skips_mimo(monkeypatch):
    """AnySearch 有结果 → mimo 不调用（mimo key 存在也不白调）。"""
    client = _FakeClient({
        ANYSEARCH_URL: _FakeResponse(200, _anysearch_payload()),
        MIMO_URL: _FakeResponse(200, {"choices": [{"message": {"content": "mimo 不应被调用"}}]}),
    })
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setenv("ANYSEARCH_API_KEY", "as_sk_test")
    monkeypatch.setenv("XIAOMI_API_KEY", "x")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    out = json.loads(_run(web_search("2024 高考化学真题")))

    assert "2024年高考化学全国卷真题及答案" in out["result"]
    assert "example.com" in out["result"]  # 结构化来源 footer
    called_urls = [c[1] for c in client.calls]
    assert MIMO_URL not in called_urls


def test_web_search_falls_back_to_mimo_when_anysearch_empty(monkeypatch):
    """AnySearch 无结果 → mimo 内置搜索兜底。"""
    client = _FakeClient({
        ANYSEARCH_URL: _FakeResponse(200, _anysearch_payload(results=[])),
        MIMO_URL: _FakeResponse(200, {"choices": [{"message": {"content":
            "mimo 搜索到的结果：物质的量是化学计算的核心概念，n=m/M=V/Vm=cV 等公式需要熟练掌握，"
            "配平与单位换算常考，建议结合真题练习。"}}]}),
    })
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setenv("ANYSEARCH_API_KEY", "as_sk_test")
    monkeypatch.setenv("XIAOMI_API_KEY", "x")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    out = json.loads(_run(web_search("物质的量")))
    assert "mimo 搜索到的结果" in out["result"]


def test_web_search_falls_back_to_bing_when_anysearch_and_mimo_fail(monkeypatch):
    """AnySearch 与 mimo 都失败（无 key/失败）→ Bing HTML 抓取兜底。"""
    bing_html = ('<li class="b_algo"><h2><a href="https://chem.example/2024">2024化学真题</a></h2>'
                 '<p>物质的量真题详解：包含摩尔质量、阿伏伽德罗常数等核心考点的计算题解析</p></li>')
    client = _FakeClient({
        ANYSEARCH_URL: _FakeResponse(401, {"error": {"message": "Invalid API Key"}}),
        None: _FakeResponse(200, text=bing_html),  # bing 动态 URL → 通配
    })
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client)
    _no_keys(monkeypatch)
    monkeypatch.setenv("ANYSEARCH_API_KEY", "as_sk_bad")  # AnySearch 401

    out = json.loads(_run(web_search("物质的量")))
    assert "2024化学真题" in out["result"]
    assert any(c[0] == "get" for c in client.calls)  # Bing 兜底确实被调用


def test_web_search_all_channels_fail_returns_error(monkeypatch):
    """三通道全失败 → 返回友好错误（不抛异常）。"""
    client = _FakeClient({
        ANYSEARCH_URL: _FakeResponse(401, {"error": {"message": "Invalid API Key"}}),
        None: _FakeResponse(200, text="<html>no results</html>"),  # bing 无 b_algo
    })
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client)
    _no_keys(monkeypatch)
    monkeypatch.setenv("ANYSEARCH_API_KEY", "as_sk_bad")

    out = json.loads(_run(web_search("不存在的特殊查询")))
    assert "未获取到搜索结果" in out["error"]


# ── _do_web_search（search_exam_bank 真题搜索兜底）：AnySearch 优先，mimo 兜底 ──

async def _fake_anysearch(text, sources):
    return text, sources


def test_do_web_search_prefers_anysearch(monkeypatch):
    """真题搜索兜底：AnySearch 有结果 → 直接用，mimo 不被调用。"""
    from agent.tools import exam_bank
    from agent.tools.exam_bank import _do_web_search

    monkeypatch.setattr(
        exam_bank, "_anysearch_search",
        lambda query, max_results=5: _fake_anysearch(
            "1. 2024年高考化学全国甲卷真题\n包含物质的量计算题详解与答案解析，"
            "来源可验证，可直接点击来源链接查看原题内容与配图",
            [{"title": "2024化学真题", "url": "https://example.com/2024-chem"}],
        ),
    )
    client = _FakeClient({MIMO_URL: _FakeResponse(200, {"choices": [{"message": {"content": "mimo 不应被调用"}}]})})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client)

    out = json.loads(_run(_do_web_search("2024 高考化学真题")))
    assert "2024年高考化学全国甲卷真题" in out["result"]
    assert out["sources"][0]["url"] == "https://example.com/2024-chem"
    assert client.calls == []  # mimo 未调用


def test_do_web_search_falls_back_to_mimo(monkeypatch):
    """真题搜索兜底：AnySearch 无结果 → mimo 内置搜索。"""
    from agent.tools import exam_bank
    from agent.tools.exam_bank import _do_web_search

    monkeypatch.setattr(exam_bank, "_anysearch_search",
                        lambda query, max_results=5: _fake_anysearch("", []))
    client = _FakeClient({MIMO_URL: _FakeResponse(200, {"choices": [{"message": {"content":
        "2024年高考化学真题：物质的量相关题目如下，含完整解析与来源链接，长度超过五十字符要求。"}}]})})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client)
    monkeypatch.setenv("XIAOMI_API_KEY", "x")

    out = json.loads(_run(_do_web_search("物质的量真题")))
    assert "2024年高考化学真题" in out["result"]
    assert any(c[0] == "post" and c[1] == MIMO_URL for c in client.calls)


def test_do_web_search_no_channels_returns_empty(monkeypatch):
    """真题搜索兜底：AnySearch 与 mimo 都不可用 → 空结果（不抛异常）。"""
    from agent.tools import exam_bank
    from agent.tools.exam_bank import _do_web_search

    monkeypatch.setattr(exam_bank, "_anysearch_search",
                        lambda query, max_results=5: _fake_anysearch("", []))
    _no_keys(monkeypatch)

    out = json.loads(_run(_do_web_search("真题")))
    assert out == {"result": "", "sources": []}
