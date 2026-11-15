"""另外两个文本生成出口接到同一条接缝上（架构报告候选 2 · Q2「先进三个文本生成出口」）。

agent 回路那条由 `test_llm_transport_agent_spike.py` 罩着；这里罩剩下两条：
`LLMService`（同步 httpx，出题/批改/家长报告那一整片都从它走）与 `DeepSeekProvider`
（异步 httpx，意图分类与几个 skill 用）。要证的三件事，按共识写死：

· 默认 live 时这层是透明的，两个出口的请求形状与字节都没变；
· record → replay 闭环，replay 全程不碰网络；
· 磁带 miss 必须**炸出来**，不能被 `LLMService` 那条 `except Exception` 的重试循环
  吞成 `{"success": False}` —— 否则评估会把一次配置错误当成"这道题跑坏了"（Q3）。
"""
import asyncio
import json
import os

import pytest

from app.services import llm_transport as lt
from cassette_fakes import ScriptedAsync, ScriptedSync

JSON_CT = "application/json"


def _completion(text="盐类水解的实质是弱离子结合水电离出的 H+ 或 OH-。",
                prompt_tokens=21, completion_tokens=15):
    body = {"id": "chatcmpl-eg", "object": "chat.completion", "created": 1,
            "model": "deepseek-chat",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                         "finish_reason": "stop"}],
            "usage": {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
                      "total_tokens": prompt_tokens + completion_tokens}}
    return json.dumps(body, ensure_ascii=False).encode()


def _chunk_line(text):
    body = {"id": "chatcmpl-eg", "object": "chat.completion.chunk", "created": 1,
            "model": "deepseek-chat",
            "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}]}
    return "data: " + json.dumps(body, ensure_ascii=False) + "\n\n"


def _llm_module():
    """`app/services/__init__.py` 把单例重导出成包属性，模块只能从 sys.modules 拿。"""
    import sys
    return sys.modules["app.services.llm_service"]


# ── LLMService（同步出口）──────────────────────────────────────────────────

def _llm_service(monkeypatch, transport):
    """拿到 LLMService 单例，并把它的 transport 换成磁带回放（键给到实例上，绕开限频）。"""
    from app.services.llm_service import llm_service
    mod = _llm_module()
    monkeypatch.setattr(llm_service, "deepseek_key", "sk-test", raising=False)
    monkeypatch.setattr(llm_service, "enabled", True, raising=False)
    monkeypatch.setattr(mod, "_check_rate_limit", lambda *a, **k: True)
    monkeypatch.setattr(mod, "model_sync_transport", lambda: transport)
    return llm_service


def _sync_transport(store_dir, mode, inner=None):
    return lt.CassetteSyncTransport(store=lt.CassetteStore(store_dir), mode=mode, inner=inner)


def test_llmservice_records_then_replays_without_the_network(tmp_path, monkeypatch):
    d = str(tmp_path / "cassettes")
    inner = ScriptedSync([(_completion(), JSON_CT)])
    svc = _llm_service(monkeypatch, _sync_transport(d, lt.RECORD, inner=inner))
    first = svc.generate_text("盐类水解怎么讲", provider="deepseek")
    assert first["success"] and inner.calls == 1, "record 阶段必须真的走一次网络"

    # replay：不给内层 transport，任何真网络企图都会当场炸
    replay = _sync_transport(d, lt.REPLAY)
    monkeypatch.setattr(_llm_module(), "model_sync_transport", lambda: replay)
    second = svc.generate_text("盐类水解怎么讲", provider="deepseek")
    assert second["content"] == first["content"]
    assert second["usage"] == first["usage"], "磁带放的是原始字节，usage 不能丢"


def test_llmservice_miss_escapes_the_retry_loop_instead_of_looking_like_a_bad_question(
        tmp_path, monkeypatch):
    """Q3 在这条路上的形状：prompt 变了 → 抛 CassetteMiss，不是 success:False。"""
    d = str(tmp_path / "cassettes")
    inner = ScriptedSync([(_completion(), JSON_CT)])
    svc = _llm_service(monkeypatch, _sync_transport(d, lt.RECORD, inner=inner))
    svc.generate_text("盐类水解怎么讲", provider="deepseek")

    replay = _sync_transport(d, lt.REPLAY)
    monkeypatch.setattr(_llm_module(), "model_sync_transport", lambda: replay)
    with pytest.raises(lt.CassetteMiss):
        svc.generate_text("换成一个没录过的问题", provider="deepseek")


def test_llmservice_live_mode_leaves_the_egress_untouched(monkeypatch):
    """没设模式时 `model_sync_transport()` 必须是 None —— 生产路径上这层不存在。"""
    monkeypatch.delenv("CHEMAI_LLM_MODE", raising=False)
    assert lt.model_sync_transport() is None
    assert lt.model_transport() is None


# ── DeepSeekProvider（异步出口）────────────────────────────────────────────

def _provider_transport(store_dir, mode, inner=None):
    return lt.CassetteTransport(store=lt.CassetteStore(store_dir), mode=mode, inner=inner)


@pytest.mark.parametrize("mode", [lt.LIVE, lt.RECORD, lt.REPLAY])
def test_deepseekprovider_builds_on_the_seam(tmp_path, monkeypatch, mode):
    """工厂级接线断言：三种模式下 provider 的客户端 transport 各自该是什么。

    这条不碰网络也不花钱，所以敢写；而"摘掉 `transport=model_transport()` 会怎样"
    只能靠真发一次请求来证 —— 那要动真密钥，不如把断言摆在这儿。
    """
    from agent.provider.deepseek import DeepSeekProvider
    os.makedirs(tmp_path / "cassettes", exist_ok=True)  # replay 不建目录，见 `_engine_for`
    monkeypatch.setenv("CHEMAI_LLM_MODE", mode)
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", str(tmp_path / "cassettes"))
    transport = DeepSeekProvider()._client._transport
    if mode == lt.LIVE:
        assert not isinstance(transport, lt.CassetteTransport), "live 下这层必须是透明的"
    else:
        assert isinstance(transport, lt.CassetteTransport), \
            f"provider 没接上接缝，replay 会静默走网络：{transport}"
        assert transport.mode == mode


def test_deepseekprovider_records_then_replays(tmp_path, monkeypatch):
    from agent.provider.deepseek import DeepSeekProvider

    d = str(tmp_path / "cassettes")
    inner = ScriptedAsync([(_completion(), JSON_CT)])
    monkeypatch.setattr(lt, "model_transport",
                        lambda: _provider_transport(d, lt.RECORD, inner=inner))
    got = asyncio.run(DeepSeekProvider().chat([{"role": "user", "content": "什么是水解"}]))
    assert inner.calls == 1 and got.content

    replay = _provider_transport(d, lt.REPLAY)
    monkeypatch.setattr(lt, "model_transport", lambda: replay)
    again = asyncio.run(DeepSeekProvider().chat([{"role": "user", "content": "什么是水解"}]))
    assert again.content == got.content
    assert again.usage == got.usage


def test_deepseekprovider_stream_replays_line_by_line(tmp_path, monkeypatch):
    """流式出口录的是一整串 SSE；`aiter_lines` 要能按原来的行序吐出来。"""
    from agent.provider.deepseek import DeepSeekProvider

    d = str(tmp_path / "cassettes")
    sse = (_chunk_line("盐类") + _chunk_line("水解") + "data: [DONE]\n\n").encode()
    inner = ScriptedAsync([(sse, "text/event-stream")])
    monkeypatch.setattr(lt, "model_transport",
                        lambda: _provider_transport(d, lt.RECORD, inner=inner))

    async def _drain():
        return [line async for line in DeepSeekProvider().chat_stream(
            [{"role": "user", "content": "讲一下水解"}])]
    recorded = asyncio.run(_drain())
    assert inner.calls == 1

    replay = _provider_transport(d, lt.REPLAY)
    monkeypatch.setattr(lt, "model_transport", lambda: replay)
    assert asyncio.run(_drain()) == recorded


def test_deepseekprovider_miss_raises_on_the_first_attempt(tmp_path, monkeypatch):
    """miss 不能被 provider 自己的重试循环拖成三遍 —— 它不是 TransportError。"""
    from agent.provider.deepseek import DeepSeekProvider

    d = str(tmp_path / "cassettes")
    inner = ScriptedAsync([(_completion(), JSON_CT)])
    monkeypatch.setattr(lt, "model_transport",
                        lambda: _provider_transport(d, lt.RECORD, inner=inner))
    asyncio.run(DeepSeekProvider().chat([{"role": "user", "content": "什么是水解"}]))

    replay = _provider_transport(d, lt.REPLAY)
    store = replay.store
    looks = []
    real_tape = store.tape

    def spy(key):
        looks.append(key)
        return real_tape(key)
    monkeypatch.setattr(store, "tape", spy)

    monkeypatch.setattr(lt, "model_transport", lambda: replay)
    with pytest.raises(lt.CassetteMiss):
        asyncio.run(DeepSeekProvider().chat([{"role": "user", "content": "换一个没录过的"}]))
    assert len(looks) == 1, f"miss 被重试了 {len(looks)} 次"


# ── 第三个文本出口之外的两处（code review 抓到）────────────────────────────

@pytest.mark.parametrize("mode", [lt.LIVE, lt.RECORD, lt.REPLAY])
def test_exam_bank_model_client_is_built_on_the_seam(tmp_path, monkeypatch, mode):
    """出题联网检索的三个模型调用点也在缝上。

    双轴审查抓到的旁路：共识说"三个文本生成出口"，实际 `agent/tools/exam_bank.py`
    还自己建了三次 `httpx.AsyncClient` 打 mimo/deepseek 的 chat/completions ——
    replay 下它是全仓唯一仍会真联网的地方，Q9 的"全程不碰网络"就成了假话。
    """
    from agent.tools.exam_bank import _seam_http_client
    os.makedirs(tmp_path / "cassettes", exist_ok=True)  # replay 不建目录
    monkeypatch.setenv("CHEMAI_LLM_MODE", mode)
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", str(tmp_path / "cassettes"))
    client = _seam_http_client(timeout=60.0)
    try:
        transport = client._transport
        if mode == lt.LIVE:
            assert not isinstance(transport, lt.CassetteTransport), "live 下这层必须是透明的"
        else:
            assert isinstance(transport, lt.CassetteTransport), \
                f"exam_bank 绕过了接缝：{transport}"
            assert transport.mode == mode
    finally:
        asyncio.run(client.aclose())





def test_recording_logs_the_usage_with_otel_field_names(tmp_path, monkeypatch, caplog):
    """录制时留一行结构化日志，字段名照 OTel GenAI 取（共识 Q13）。

    这也是 `normalize_usage` 在生产里的用处：日志的 token 数就来自它。没有这一行，
    一次评估跑完只知道自己"跑过了"，不知道跑的是哪个模型、花了多少 token。
    """
    d = str(tmp_path / "cassettes")
    inner = ScriptedSync([(_completion(prompt_tokens=137, completion_tokens=8), JSON_CT)])
    svc = _llm_service(monkeypatch, _sync_transport(d, lt.RECORD, inner=inner))
    with caplog.at_level("INFO", logger="chemai.model_egress"):
        svc.generate_text("盐类水解怎么讲", provider="deepseek")
    line = next((r.getMessage() for r in caplog.records if "cassette_recorded" in r.getMessage()), "")
    assert "gen_ai.request.model=deepseek-chat" in line, line
    assert "gen_ai.usage.input_tokens=137" in line, line
    assert "gen_ai.provider.name=deepseek" in line, line
    assert "http.response.status_code=200" in line, line


def test_a_replay_miss_leaves_a_warning_line(tmp_path, monkeypatch, caplog):
    """异常给调用方，日志给运维：miss 的键与磁带总数落一行 warning。"""
    d = str(tmp_path / "cassettes")
    os.makedirs(d, exist_ok=True)
    svc = _llm_service(monkeypatch, _sync_transport(d, lt.REPLAY))
    with caplog.at_level("WARNING", logger="chemai.model_egress"), pytest.raises(lt.CassetteMiss):
        svc.generate_text("盐类水解怎么讲", provider="deepseek")
    assert any("cassette_miss" in r.getMessage() and "tapes=0" in r.getMessage()
               for r in caplog.records), [r.getMessage() for r in caplog.records]


def _httpx_constructions(src):
    """AST 里所有 `httpx.Client(...)` / `httpx.AsyncClient(...)` 构造：(行号, 是否带 transport=)。

    按调用点判定，不看文件级的注释或字符串 —— 上一版按"文件里出现过缝的标记"免检，于是
    `exam_bank` 只要有一个 helper 提到缝，同文件里另外两个裸客户端就统统放行。
    """
    import ast
    out = []
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if not isinstance(fn, ast.Attribute) or fn.attr not in ("Client", "AsyncClient"):
            continue
        holder = fn.value
        # `httpx` 与 `_httpx` 都认：本仓的惯例是给被遮蔽的模块起下划线别名（`exam_bank` 里
        # 就有 `_os` / `_re` / `_urlparse`），哪天有人写 `import httpx as _httpx`，只认原名
        # 的绊线会安静地扫不到任何构造 —— 那正是"扫不到"与"没有违规"混为一谈的形状。
        if not (isinstance(holder, ast.Name) and holder.id in ("httpx", "_httpx")):
            continue
        out.append((node.lineno, any(k.arg == "transport" for k in node.keywords)))
    return out


def test_no_upstream_http_client_bypasses_the_seam():
    """生产源码里，凡是打模型或喂模型内容的上游，httpx 客户端都必须带 `transport=`。

    这是"共识数出三个出口、实际四处建模五处构造"那次的通用防线，也覆盖搜索通道：
    AnySearch / Bing 的结果原文会被拼进下一次模型请求，不进缝的话 replay 下仍在真联网，
    而且键取决于当天的网页字节。判定式是"同文件里出现过这些上游主机"而不是整仓扫描，
    免得把百度 OCR（明确留在缝外，ADR-0023 决策 8）误纳进来。
    """
    import pathlib as _p
    root = _p.Path(__file__).resolve().parents[1]
    upstream = ("chat/completions", "anysearch.com", "bing.com/search", "api.deepseek.com",
                "xiaomimimo.com", "dashscope.aliyuncs.com")
    offenders, checked_files = [], []
    for sub in ("agent", "app", "evals"):
        for path in sorted((root / sub).rglob("*.py")):
            src = path.read_text(encoding="utf-8", errors="ignore")
            if not any(host in src for host in upstream):
                continue
            sites = _httpx_constructions(src)
            if not sites:
                continue
            checked_files.append(path.relative_to(root).as_posix())
            offenders += [f"{path.relative_to(root).as_posix()}:{line}"
                          for line, has_transport in sites if not has_transport]
    assert not offenders, f"这些构造绕过接缝：{offenders}"
    # 正向对照：扫不到文件与"没有违规"是两种绿，必须能区分
    assert len(checked_files) >= 4, f"扫描本身失效了，只看到 {checked_files}"


def test_the_vision_egress_lets_a_miss_through(tmp_path, monkeypatch):
    """视觉链上的 miss 必须炸出来，不能被降级成"换下一个模型再试"（ADR-0023 决策 11）。

    上一版这里证的是 mimo 与智谱两个后端各一次；两家现在都不在链上（智谱整条按裁定删除，
    mimo 因为只收 http 图片链接而用不了本地答卷），所以证的是链上唯一那个后端。
    """
    d = str(tmp_path / "cassettes")
    os.makedirs(d, exist_ok=True)
    svc = _llm_service(monkeypatch, _sync_transport(d, lt.REPLAY))
    monkeypatch.setattr(svc, "deepseek_key", "sk-ds", raising=False)
    monkeypatch.setattr(svc, "deepseek_model", "deepseek-flash", raising=False)
    with pytest.raises(lt.CassetteMiss):
        svc.analyze_paper_with_vision("aGVsbG8=", "printed")


def test_no_vision_backend_is_reported_instead_of_pretending_to_retry(tmp_path, monkeypatch):
    """链上空了要说"没有能看图的后端"，而不是打一发然后报"多模态分析失败"。

    配错与跑挂是两件事：前者要让看日志的人一眼知道该去设 `DEEPSEEK_MODEL`。
    """
    d = str(tmp_path / "cassettes")
    os.makedirs(d, exist_ok=True)
    svc = _llm_service(monkeypatch, _sync_transport(d, lt.REPLAY))
    monkeypatch.setattr(svc, "deepseek_key", "", raising=False)
    got = svc.analyze_paper_with_vision("aGVsbG8=", "answer_sheet")
    assert got["success"] is False and "DEEPSEEK" in got["message"] \
        and "XIAOMI" in got["message"], got


def test_the_vision_egress_records_flash_with_thinking_off(tmp_path, monkeypatch):
    """视觉复核换到 deepseek-flash：录下来的请求体要证明"带图 + 关思考 + 跟着配置走"。

    这三样各对应一次真实踩坑：图片走 `messages` 里的 `image_url`；思考模型会把
    `max_tokens` 吃满后返回空正文；模型名写死的话 `.env` 换了它还在打旧型号。
    """
    d = str(tmp_path / "cassettes")
    inner = ScriptedSync([(json.dumps({"choices": [{"message": {"content": '{"questions":[]}'}}]}).encode(),
                          JSON_CT)])
    svc = _llm_service(monkeypatch, _sync_transport(d, lt.RECORD, inner=inner))
    monkeypatch.setattr(svc, "mimo_key", "", raising=False)
    monkeypatch.delenv("ZHIPU_API_KEY", raising=False)
    monkeypatch.setattr(svc, "deepseek_key", "sk-ds", raising=False)
    monkeypatch.setattr(svc, "deepseek_model", "deepseek-flash", raising=False)
    got = svc.analyze_paper_with_vision("aGVsbG8=", "answer_sheet")
    assert got["success"] and got["provider"] == "deepseek", got
    body = _only_tape(d)["normalized"]
    assert body["model"] == "deepseek-flash", body
    assert body.get("thinking") == {"type": "disabled"}, "视觉复核也是固定格式输出，得关思考"
    assert body["messages"][0]["content"][0]["type"] == "image_url", "图片没带上，复核的是空气"


def test_exam_bank_web_search_miss_raises_instead_of_returning_no_results(tmp_path, monkeypatch):
    """exam_bank 三个通道各自 `except Exception` 兜底：miss 不能被降级成"这条通道没结果"。

    那条路上线时刚被接进接缝，是本轮审查抓到的第四处同类漏放。
    """
    import asyncio as _aio

    from agent.tools.exam_bank import web_search
    d = str(tmp_path / "cassettes")
    os.makedirs(d, exist_ok=True)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-ds")
    monkeypatch.setenv("XIAOMI_API_KEY", "")
    monkeypatch.setenv("ANYSEARCH_API_KEY", "")
    monkeypatch.setattr(lt, "model_transport",
                        lambda: _provider_transport(d, lt.REPLAY))
    with pytest.raises(lt.CassetteMiss):
        _aio.run(web_search("2025 年高考化学盐类水解真题"))


# ── 思考模式与模型名：deepseek-flash 是思考模型，这两件都是实测钉住的 ──────────

def _only_tape(store_dir):
    """录完一个请求之后磁带目录里该只有一盘，直接把它读出来看请求体形状。"""
    names = [f for f in os.listdir(store_dir) if f.endswith(".json")]
    assert len(names) == 1, f"预期一盘磁带，实际 {names}"
    with open(os.path.join(store_dir, names[0]), encoding="utf-8") as fh:
        return json.load(fh)


def test_fixed_format_egress_records_thinking_off(tmp_path, monkeypatch):
    """出题/诊断/家长报告这一片固定格式通路必须显式关思考。

    flash 的 reasoning token 算在 max_tokens 之内：实测同一句出题请求在 2048 档被思考吃满，
    `finish_reason=length` 且 content 为空 —— 老师看到的是一条空白回答。断言落在录下来的
    请求体上，不是落在源码字符串上，这样换调用形状（改 payload 构造方式）也照样管得住。
    """
    d = str(tmp_path / "cassettes")
    inner = ScriptedSync([(_completion(), JSON_CT)])
    svc = _llm_service(monkeypatch, _sync_transport(d, lt.RECORD, inner=inner))
    svc.generate_text("盐类水解怎么讲", provider="deepseek")
    body = _only_tape(d)["normalized"]
    assert body.get("thinking") == {"type": "disabled"}, body
    assert body["model"] == "deepseek-chat", "模型名要跟着配置走，见下一条绊线"


_MODEL_LITERALS = ("deepseek-chat", "mimo-v2.5", "mimo-v2.6-flash",
                     "mimo-v2.6-pro")
# 配置的默认值是该出现这些名字的唯一地方；别处写死，`.env` 换了型号就只对一部分通路生效
_MODEL_NAME_ALLOW = {"app/config.py", "app/core/config.py"}


def _prod_sources():
    """一方源码：app/、agent/、chem_skills/（被 app 直接 import 的技能包）、evals/。

    扫描根表现在也罩住 evals/：`--tapes replay` 之后"评估偷偷打网络"就是这条绊线该抓的事
    （ADR-0023 决策 20），只看 app/agent 会漏掉一整批直接构造客户端的评估脚本。
    """
    import pathlib as _p
    root = _p.Path(__file__).resolve().parents[1]
    dirs = ("app", "agent", "chem_skills", "evals")
    return [(q.relative_to(root).as_posix(), q)
            for d in dirs for q in (root / d).rglob("*.py")]


def _value_string_literals(path):
    """文件里**当值用**的字符串字面量：注释与 docstring 都不算。

    为什么不用行文本匹配：上一版判的是"这一行同时有型号名和 model 键"，于是
    `m = "deepseek-chat"` 配 `{"model": m}` 就绕过去了 —— 而那正是这次要抓的形状。
    用 ast 列出真正的字符串常量、再剔掉各层 docstring，散文（注释与文档字符串里讲
    "flash 回 400、pro 读得对"）依然自由，写死的值一个都藏不住。
    """
    import ast

    src = path.read_text(encoding="utf-8", errors="ignore")
    tree = ast.parse(src)
    doc_nodes = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            body = getattr(node, "body", None)
            first = body[0] if body else None
            value = getattr(first, "value", None) if isinstance(first, ast.Expr) else None
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                doc_nodes.add(id(value))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str)                 and id(node) not in doc_nodes:
            out.append((node.lineno, node.value))
    return out


def _hardcoded_model_lines(sources, skip_allow=True):
    hits = []
    for rel, path in sources:
        if skip_allow and rel in _MODEL_NAME_ALLOW:
            continue
        for n, value in _value_string_literals(path):
            if value in _MODEL_LITERALS:
                hits.append(f"{rel}:{n}")
    return hits


def test_no_egress_hardcodes_a_provider_model_name():
    """绊线：型号名只许出现在配置里，别处把它**当值用**就是写死。

    为什么要钉：`.env` 换成 deepseek-flash 之后，四处写死 `deepseek-chat` 的出口仍在打旧
    模型，"我换模型了"只对 agent 回路成立；换 mimo 型号是同一件事的第二回。
    """
    hits = _hardcoded_model_lines(_prod_sources())
    assert not hits, f"这些地方把模型名当值写死了：{hits}"

    # 正向对照：配置的默认值里必然有这种写法。扫不到任何命中 = 判据本身失效，那不是绿。
    everywhere = _hardcoded_model_lines(_prod_sources(), skip_allow=False)
    assert everywhere, "判据一个都没匹配到，这条绊线已经空转了"


def test_the_zhipu_path_stays_deleted():
    """绊线：智谱那条路按裁定整条拆了，别再默默长回来。

    只在文档里写一句"已删除"挡不住 —— 它当初是作为视觉兜底长在链上的，回来时还会连带把
    `image_url` 的形状差异带进 `vision_backends`。所以扫源码：`app/` 与 `agent/` 里不许再
    出现 ZHIPU / bigmodel / glm-4v / ChatGLM。唯一豁免是那行写着"智谱…不要再加回来"的说明。
    """
    banned = ("ZHIPU", "zhipu", "bigmodel", "glm-4v", "ChatGLM", "chatglm")
    hits = []
    for rel, path in _prod_sources():
        for n, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            if any(b in line for b in banned) and "智谱" not in line:
                hits.append(f"{rel}:{n}")
    assert not hits, f"智谱的引用又回来了：{hits}"


def test_agent_factory_caps_output_with_the_field_the_endpoint_honors(fake_deepseek_key):
    """agent 对话那条的封顶必须走 `extra_body["max_tokens"]`，不能用 max_tokens 属性。

    langchain 1.4.2 把属性映射成 `max_completion_tokens`，而 flash 不认那个字段：实测设了
    4096 那一轮仍然吐出 8941 输出 token —— 这道封顶从来没生效过。断言两个方向都钉：
    extra_body 里有真封顶，属性上不留那个失效的字段。
    """
    from agent.guard import get_langchain_model
    model = get_langchain_model("deepseek")
    assert (model.extra_body or {}).get("max_tokens", 0) >= 8192, \
        f"没有真封顶，单轮输出无人管：{model.extra_body}"
    assert model.max_tokens is None, \
        "属性写法会被 langchain 换成 max_completion_tokens，flash 不认它"


def test_the_agent_loop_is_the_one_that_keeps_thinking(fake_deepseek_key):
    """留思考的只有对话讲评那一条：它不往请求体里塞 thinking=disabled。

    这是 D1「分通路」那侧的半边证据 —— 出题那边关了就完了不够，还得证明对话这条没被顺手关掉。
    """
    from agent.guard import get_langchain_model
    extra = get_langchain_model("deepseek").extra_body or {}
    assert "thinking" not in extra, f"对话讲评那条被关思考了：{extra}"


def test_anysearch_channel_lets_a_miss_through_too(tmp_path, monkeypatch):
    """主搜索通道（AnySearch）也在缝上，它的 miss 不许降级成"这条通道没结果"。

    上一版这条是免检的：用例把 `ANYSEARCH_API_KEY` 设成空串，于是 `_anysearch_search` 在第一
    行 `if not api_key` 就返回，那个吞掉一切的 `except Exception` 根本没被走到 —— 缺陷就这么
    躲过了测试。这里给一个真 key，走的正是那条路。
    """
    import asyncio as _aio

    from agent.tools.exam_bank import _anysearch_search
    d = str(tmp_path / "cassettes")
    os.makedirs(d, exist_ok=True)
    monkeypatch.setenv("ANYSEARCH_API_KEY", "as_test_key")
    monkeypatch.setattr(lt, "model_transport", lambda: _provider_transport(d, lt.REPLAY))
    with pytest.raises(lt.CassetteMiss):
        _aio.run(_anysearch_search("盐类水解 高考真题", max_results=3))


def test_an_unknown_provider_fails_instead_of_quietly_becoming_deepseek(monkeypatch,
                                                                        fake_deepseek_key):
    """`get_langchain_model("zhipu")` 现在当场报错，不再静默按 deepseek 建模型。

    这跟本次删除是一对：删掉一个 provider 之后，那个 `configs.get(provider, deepseek)`
    的默认值就成了陷阱 —— `.env` 里还写着 `LLM_PROVIDER=zhipu` 的人会以为自己用的是智谱，
    实际在打 deepseek，而且一声不响。
    """
    import pytest as _p

    from agent.guard import get_langchain_model
    monkeypatch.setenv("CHEMAI_LLM_MODE", lt.LIVE)
    with _p.raises(ValueError, match="zhipu"):
        get_langchain_model("zhipu")


def test_the_agent_model_builds_offline_without_a_credential(monkeypatch, tmp_path):
    """离线重放要说"零凭据"，就得处理 langchain 在**构造**时查 key 这件事（它比这条缝早）。

    本机实测：`--tapes replay` 清掉 key 跑 `trajectory_planning`，每条用例都红在
    `Missing credentials ... OPENAI_API_KEY`，而磁带就在旁边没人去问。占位值只在
    mode != live 时生效，live 下缺 key 仍然是错。
    """
    import pytest

    from app.config import config as _cfg
    from app.services.llm_transport import OFFLINE_KEY_PLACEHOLDER

    def _build():
        from agent.guard import get_langchain_model
        return get_langchain_model("deepseek")

    monkeypatch.setattr(type(_cfg), "provider_keys", property(lambda self: {}), raising=False)
    monkeypatch.delenv("CHEMAI_LLM_MODE", raising=False)
    monkeypatch.delenv("CHEMAI_CASSETTE_DIR", raising=False)
    with pytest.raises(Exception, match="credentials|api_key"):
        _build()

    monkeypatch.setenv("CHEMAI_LLM_MODE", "replay")
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", str(tmp_path))
    model = _build()
    assert model.openai_api_key.get_secret_value() == OFFLINE_KEY_PLACEHOLDER


def test_record_mode_still_demands_a_real_credential(monkeypatch, tmp_path):
    """占位值只给 replay。record 是真要出门的：假 Bearer 换来的 401 会被当成一次正常应答录进磁带。

    那种带子看起来是绿的，实际记录的是一次鉴权失败 —— 一批"离线可重复"的评估就这么被污染了，
    而且没有任何东西会报错。
    """
    import pytest

    from app.config import config as _cfg

    monkeypatch.setattr(type(_cfg), "provider_keys", property(lambda self: {}), raising=False)
    monkeypatch.setenv("CHEMAI_LLM_MODE", "record")
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", str(tmp_path))
    from agent.guard import get_langchain_model

    with pytest.raises(Exception, match="credentials|api_key"):
        get_langchain_model("deepseek")


def test_the_classifier_singleton_is_keyed_by_the_egress_binding(monkeypatch, tmp_path):
    """transport 在建客户端那一刻绑死，所以纯单例会把第一次调用的磁带目录一直用下去。"""
    import agent.provider.deepseek as dp

    for k in ("DEEPSEEK_API_KEY", "CHEMAI_LLM_MODE", "CHEMAI_CASSETTE_DIR"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-x")
    monkeypatch.setattr(dp, "classifier_provider", None, raising=False)
    monkeypatch.setattr(dp, "_classifier_key", None, raising=False)

    first = dp.get_classifier_provider()
    assert dp.get_classifier_provider() is first, "live 下就该是一个共享连接池"

    monkeypatch.setenv("CHEMAI_LLM_MODE", "replay")
    (tmp_path / "entry_b").mkdir()   # replay 不建目录（打错路径要当场报，见 `_engine_for`）
    monkeypatch.setenv("CHEMAI_CASSETTE_DIR", str(tmp_path / "entry_b"))
    second = dp.get_classifier_provider()
    assert second is not first, "换了磁带目录还复用旧客户端，第二条评估放的是第一条的带"
