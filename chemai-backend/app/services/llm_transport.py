"""模型出口（Model egress）的录放接缝 —— 一条 httpx transport，三种模式。

这一层往下是真实网络，往上是所有会喂给模型的 HTTP 依赖（模型端点本身，以及 `exam_bank`
的联网搜索通道），它们都以为自己还在跟一个普通端点说话。完整出口清单见
`docs/adr/0023-*.md` 决策 8 与 `CONTEXT.md` 的 Model Egress 词条 —— 这里刻意不数数，
数出来一次就漂一次。

三条设计约束值得单独说明，因为它们决定了磁带好不好用：

1. **命中键含 provider、model 与端点。** 换模型即整批 miss，逼一次显式重录 —— 拿旧模型的
   响应测新模型不叫回归。工具定义与 messages 一定进键：`CONTEXT.md` 写着「Tool
   descriptions are the sole interface for tool selection」，改了工具描述就是改了模型输入。
2. **重放交回的是这一层收到的响应体字节**，不改写、不重组。流式（SSE）录的就是一整串
   `data:` 行，重放时按这份字节交给上层解析器。chunk 切法与时间间隔不是 contract 的一部分
   —— 断言要落在解析后的事件序列上。传输层压缩由 httpx 处理，见 `wire_headers`。
3. **miss 硬失败。** 不静默回落到 live：CI 里悄悄联网意味着花真钱、还把带真实学生数据的新
   磁带写进仓库。报错时列出最接近的几盘磁带与它们差在哪几个字段，否则一次键漂移能查半天。

模式由进程环境决定（`CHEMAI_LLM_MODE`），默认 `live` —— 生产与误配置都不会意外进入录放。
非 live 必须同时给 `CHEMAI_CASSETTE_DIR`，缺了当场报错，不猜目录。
"""
from __future__ import annotations

import base64
import contextlib
import hashlib
import itertools
import json
import logging
import os
import re
import tempfile
import threading
import time
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import httpx

LIVE = "live"
RECORD = "record"
REPLAY = "replay"
_MODES = (LIVE, RECORD, REPLAY)

MODE_ENV = "CHEMAI_LLM_MODE"
# 录放带模式下的占位凭据。langchain 在**构造**客户端时就查 key（比这条缝早），所以"离线重放
# 不需要凭据"这句话要有个写法上的落点：给一个永远出不了本机的假值，让缝去答话。
# 真凭据缺失在 live 下仍然是错，不会被这个常量掩盖 —— 只有 mode != live 才会用到它。
OFFLINE_KEY_PLACEHOLDER = "cassette-offline-not-a-real-key"
CASSETTE_DIR_ENV = "CHEMAI_CASSETTE_DIR"

# 顶层里这些字段不进键：它们不改变模型的行为，却会让磁带一夜之间全 miss。
_VOLATILE_BODY_KEYS = frozenset({"user", "timestamp", "seed", "request_id", "id", "metadata"})
# 这些响应头不进磁带也不回给客户端：由 httpx 现场重算，照抄会与 body 实际长度/编码打架。
_SKIP_RESPONSE_HEADERS = frozenset({
    "content-length", "content-encoding", "transfer-encoding", "connection", "date",
})
_NEAR_LIMIT = 3
logger = logging.getLogger("chemai.model_egress")

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")  # 冒号留在键里，但 Windows 文件名不许有它
_MAX_KEY_SEG = 64
_DATA_URI = re.compile(r"data:[^;,]+;base64,[A-Za-z0-9+/=\s]+")


# DeepSeek V4.1 Flash（`DEEPSEEK_MODEL=deepseek-flash`）是思考模型，两件事是实测出来的：
# ① reasoning token 算在 `max_tokens` 之内 —— 同一句出题请求在 2048 档被思考吃满，
#    `finish_reason=length` 且 content 为空；带上下面这个字段关掉思考才正常出正文。
# ② langchain 1.4.2 把 `ChatOpenAI(max_tokens=…)` 映射成 `max_completion_tokens`，而 flash
#    不认那个字段（缝上录到的真实请求体就是 `max_completion_tokens: 4096`，实测单轮吐了
#    8941 输出 token）—— 所以 agent 那条的封顶必须从 `extra_body` 传 `max_tokens`，
#    见 `agent/guard.py`。
# 固定格式的那些通路（出题/诊断/家长报告/搜索总结/意图分类/判官）一律关思考；只有对话讲评
# 那条留着，因为它要的是解析质量，而且它走流式（实测首字 0.3s，思考阶段不挡首字）。
THINKING_OFF: Dict[str, Any] = {"thinking": {"type": "disabled"}}


def wire_headers(headers: Optional[Dict[str, str]]) -> Dict[str, str]:
    """磁带与重建响应共用的头过滤。

    带 `content-encoding: gzip` 回一份已被 httpx 解压过的字节，客户端会再解一次并当场
    `DecodingError`；`content-length` 同理会对不上。
    """
    return {k.lower(): v for k, v in (headers or {}).items()
            if k.lower() not in _SKIP_RESPONSE_HEADERS}


class CassetteMiss(RuntimeError):
    """replay 模式下没有命中的磁带。绝不静默走网络。"""


def refuse_recording_in_server() -> None:
    """服务器进程不许开 record：评估入口才该录像（ADR-0023 决策 7）。

    一行 `CHEMAI_LLM_MODE=record` 加一个目录，就会让开发/生产进程逐请求把对话原文写盘，
    所以模式为 record 时当场拒绝启动。replay 只读磁带，不在此列。

    这里自己读一次 `.env`：模式的真值来自进程环境，而 `import app.main` 并不会拉起
    `app/config.py`（实测 `sys.modules` 里没有它）。把守卫挂在 main.py 上却看不见 `.env`，
    就等于只挡得住"命令行显式 export"那一半 —— 而恰恰是 `.env` 那行才会被人忘在仓库里。
    路径按本文件位置锚定，与启动时的 cwd 无关（同 `app/core/config.py:11`）。
    """
    from dotenv import load_dotenv
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")
    if resolve_mode() == RECORD:
        raise RuntimeError(
            f"{MODE_ENV}=record 只该由评估入口显式开启，服务器进程拒绝启动；"
            f"要离线复现请用 {REPLAY} + 已录好的磁带目录（{CASSETTE_DIR_ENV}）。")


def refuse_model_call_in_work_unit(what: str) -> None:
    """ADR-0021 规则 2 的执法点：决定单元内不许等一次网络往返。

    SQLite WAL 单写事务下，单元里等几秒 = 让全库写者排队到 `database is locked`（引擎没设
    busy_timeout）。原来这条只挂在两个业务出口上，而"每一次真实模型往返"实际是五处构造
    （ADR-0023 决策 8），所以判定搬到这里：缝上的 `plan` 是所有出口在 record/replay 下唯一
    都会经过的一点，业务层那两处继续留着当早失败（live 模式下 ChatOpenAI 走 langchain 自建
    客户端，够不到 transport 层，这是已知残余）。
    """
    from app.services.work_unit import ModelCallInsideWorkUnit, in_work_unit
    if in_work_unit():
        raise ModelCallInsideWorkUnit(
            f"确认门决定单元内不得调用模型出口（{what}）：先在单元外算完，再进单元写")


def resolve_mode() -> str:
    """进程级模式。默认 live；非 live 必须给磁带目录，缺了当场报错，不猜。"""
    mode = os.getenv(MODE_ENV, LIVE).strip().lower()
    if mode not in _MODES:
        raise RuntimeError(f"{MODE_ENV} 只接受 {'|'.join(_MODES)}，当前为 {mode!r}")
    if mode != LIVE and not os.getenv(CASSETTE_DIR_ENV):
        raise RuntimeError(f"{MODE_ENV}={mode} 必须同时设 {CASSETTE_DIR_ENV}")
    return mode


def _provider_of(url: str) -> str:
    """host 的倒数第二段才是供应商标签名：`api.deepseek.com`→deepseek、
    `www.bing.com`→bing。取第一段会把它们认成 "api" 和 "www" —— 而这个名字同时出现在
    磁带文件名、`near()` 的分组和日志字段里，认错了等于两家的磁带混在一堆。"""
    parts = [p for p in (httpx.URL(url).host or "").split(".") if p]
    if len(parts) >= 2:
        return parts[-2]
    return parts[0] if parts else "unknown"


def _endpoint_of(url: str) -> str:
    """URL 路径进键。同一台 host 挂着多个端点（`/v1/chat/completions` 与
    `DeepSeekProvider` 的 `/beta/chat/completions`），只按 body 算键会让两个端点互相吃掉
    对方的重放序号。"""
    path = httpx.URL(url).path or "/"
    return path.strip("/").replace("/", ":") or "root"


def _without_query(url: str) -> str:
    """磁带里的 URL 只记到 path 为止：本仓已有把令牌放查询串的先例（百度那条通路，
    `grading_client.py:58`），哪天有端点这么接进来，整条 url 落盘就等于把活令牌写进 git。
    查询串本身由 `_query_for_key` 负责进键与留档 —— 剥的是这一份落盘的原文，不是身份。"""
    u = httpx.URL(url)
    return f"{u.scheme}://{u.host}{u.path}"


# 这些名字的查询参数是凭证，不是模型行为的输入：与"请求头不进键"同一个理由（Q4）。
_CREDENTIAL_QUERY = re.compile(
    r"(?i)^(access_?token|api[_-]?key|token|key|secret|password|sig|signature|authorization)$")


def _query_for_key(url: str) -> str:
    """排序后的查询串，凭证类的值换成 `***`。

    键必须含查询串：Bing 兜底那条是 `GET /search?q=<检索词>`，请求体为空 —— 只按 body 算键
    的话任意两个检索词撞成同一盘带，重放会拿"氧气"的网页回答"钠"的问题，而且一声不响。
    排序是为了同一组参数换个写法仍命中同一盘；凭证打码是因为 `access_token` 每两小时轮换，
    照原值算键会让磁带两小时全 miss（那正是 Q4 要避免的假 miss）。
    """
    return "&".join(f"{k}={'***' if _CREDENTIAL_QUERY.match(k) else v}"
                    for k, v in sorted(httpx.URL(url).params.multi_items()))


def _home_strings() -> Tuple[str, ...]:
    """本机主目录的几种写法。系统提示会把 `Path.home()` 原文拼进去（agent 要靠它构造绝对
    路径），于是磁带键绑死在录制那台机器的用户名上：评估在 Administrator 下录的带，拿到 CI
    或第二台机器上必然 miss —— 而 miss 报告只会说"请求体不同"，看不出是家目录差了一截。"""
    try:
        home = Path.home()
    except (RuntimeError, OSError):
        return ()  # HOME/USERPROFILE 查不到
    return tuple(sorted({c for c in (str(home), home.as_posix(), str(home).lower())
                         if c and len(c) > 2}, key=len, reverse=True))


def _redact_text(text: str, homes: Optional[Tuple[str, ...]] = None) -> str:
    """进键与留档用的文本：主目录归一成 `{HOME}`，data URI 换成「sha256 + 字节数」。

    图片字节换成摘要而不是直接删，是为了不同图片仍算不同请求（否则两张答卷会放中同一盘带）；
    同时学生手写的整张答卷不以可还原的形式躺在磁带里。
    """
    out = text
    for raw in _DATA_URI.findall(text):
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        out = out.replace(raw, f"<data-uri sha256={digest} bytes={len(raw)}>")
    for cand in (_home_strings() if homes is None else homes):
        out = out.replace(cand, "{HOME}")
    return out


def _redact_value(value: Any, homes: Tuple[str, ...]) -> Any:
    if isinstance(value, str):
        return _redact_text(value, homes)
    if isinstance(value, list):
        return [_redact_value(v, homes) for v in value]
    if isinstance(value, dict):
        return {k: _redact_value(v, homes) for k, v in value.items()}
    return value


def normalize_request(raw: Dict[str, Any]) -> Dict[str, Any]:
    """进键并留档的请求体形状：剥掉易变顶层字段，其余结构一字不改（只把机器串与图片字节换成
    摘要，见 `_redact_value`）。messages/tools 的内容本身不动 —— 改了工具描述就是改了模型
    输入，必须 miss。"""
    # 主目录只查一次：一条长对话有几百个字符串，每个都去 stat 一次 HOME 是白烧
    return _redact_value({k: v for k, v in raw.items() if k not in _VOLATILE_BODY_KEYS},
                          _home_strings())


def parse_body(body: bytes) -> Optional[Dict[str, Any]]:
    """请求体解析成 dict；不是 JSON 对象（GET、表单、坏字节）就是 None。"""
    if not body:
        return {}
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return parsed if isinstance(parsed, dict) else None


def request_key(url: str, body: bytes, *, parsed: Optional[Dict[str, Any]] = None) -> str:
    """命中键 = provider / model / 端点 / sha1(查询串 + 归一化请求体)。

    请求头一个都不进键：Authorization 会随密钥轮换变，那是最常见的假 miss 来源（Q4）。
    查询串进键（见 `_query_for_key`），凭证类的值同样先打码 —— 键里落的是 sha1，磁带文件名
    因而既认得出"换了检索词"又不含活令牌。
    `parsed` 让调用方把已解析的那份传进来 —— 一次录/放只该解析请求体一遍。
    """
    if parsed is None:
        parsed = parse_body(body)
    if isinstance(parsed, dict):
        model = str(parsed.get("model") or "unknown")
        canonical = json.dumps(normalize_request(parsed), sort_keys=True,
                               ensure_ascii=False, separators=(",", ":"))
    else:
        # 非 JSON 请求体：键按（脱敏后的）字节算，模型位记 unknown
        model = "unknown"
        canonical = _redact_text(body.decode("utf-8", "replace"))
    query = _query_for_key(url)
    if query:
        canonical = f"?{query}\n{canonical}"
    digest = hashlib.sha1(canonical.encode("utf-8")).hexdigest()[:16]
    return f"{_provider_of(url)}/{model}/{_endpoint_of(url)}/{digest}"


def _diff_against(tape: Dict[str, Any], sent: Dict[str, Any]) -> List[str]:
    """说清"最接近的那盘磁带"与当前请求差在哪几个字段 —— 只说"miss"查不出键漂移。"""
    out: List[str] = []
    stored = tape.get("normalized") or {}
    if not isinstance(stored, dict) or not isinstance(sent, dict):
        return ["请求体形状不同（一边不是 JSON 对象）"]
    if stored.get("model") != sent.get("model"):
        out.append(f"model: {stored.get('model')} → {sent.get('model')}")
    for field in sorted(set(stored) ^ set(sent)):
        out.append(f"字段 {field} 只在一边出现")
    for field in sorted(set(stored) & set(sent)):
        if json.dumps(stored[field], sort_keys=True, ensure_ascii=False) != \
           json.dumps(sent[field], sort_keys=True, ensure_ascii=False):
            if field == "messages":
                n_e, n_s = len(stored["messages"]), len(sent["messages"])
                out.append("messages 内容不同" if n_e == n_s else f"messages 条数 {n_e} → {n_s}")
            else:
                out.append(f"{field} 不同")
    return out or ["请求体相同但键不同（provider、端点或查询串变了）"]


class CassetteStore:
    """一个键一个 JSON 文件：`{key,url,model,normalized,entries[]}`，entries 按录制顺序。

    字段就这一套，几个读点都按它取值。磁带要进 git 靠人 review，所以缺 entries 的、坏掉的、
    读不动的一律当"没有磁带"并留一行 warning —— 绝不让 miss 诊断炸成 KeyError，那会被上层
    的 `except Exception` 认成供应商故障。
    """

    def __init__(self, root: str, create: bool = True):
        self.root = root
        if create:
            os.makedirs(root, exist_ok=True)
        # 一盘带一个锁，而不是一把总锁：`_write_atomic` 撞上 WinError 5 会睡几百毫秒重试，
        # 挂在总锁上就是"一个键的杀毒软件拖慢整批录制"。锁的粒度按文件，因为互斥的正是文件。
        self._locks: Dict[str, threading.Lock] = {}
        self._locks_lock = threading.Lock()

    def _lock_for(self, path: str) -> threading.Lock:
        with self._locks_lock:
            lock = self._locks.get(path)
            if lock is None:
                lock = self._locks[path] = threading.Lock()
            return lock

    def _path(self, key: str) -> str:
        """键里有来自请求体的 model 段：逐段净化，免得一个构造出来的名字写到盘外。

        下面的越界检查在净化之下今天走不到，留着是因为它是这道净化的兜底 —— 将来谁改了
        `_UNSAFE_NAME`（比如为了可读性放掉反斜杠），磁带会直接写到仓库别处而不是报错。
        """
        segs = [_UNSAFE_NAME.sub("_", seg)[:_MAX_KEY_SEG] for seg in key.split("/")]
        path = os.path.join(self.root, "__".join(segs) + ".json")
        root_real = os.path.realpath(self.root)
        if not os.path.realpath(path).startswith(root_real + os.sep):
            raise RuntimeError(f"磁带路径越界：{path} 不在 {root_real} 内")
        return path

    def record(self, key: str, url: str, status: int, headers: Dict[str, str],
               body: bytes, model: str = "",
               normalized: Optional[Dict[str, Any]] = None) -> None:
        """落一盘磁带。`normalized` 是进键的那份请求体 —— miss 报告与近邻比对都要用它，
        所以必须一起存，不能事后从 key 反推（sha1 不可逆）。"""
        path = self._path(key)
        entry = {
            "status": status,
            "headers": wire_headers(headers),
            "body_b64": base64.b64encode(body).decode("ascii"),
            "recorded_at": datetime.utcnow().isoformat(timespec="seconds"),
        }
        # 读-改-写整段要互斥：两个出口并发录同一键时，后写的会把先写的那条整盘盖掉。
        with self._lock_for(path):
            data = self._read(path) or {"key": key, "url": _without_query(url),
                                        "model": model, "entries": []}
            data.setdefault("entries", []).append(entry)
            if normalized is not None:
                data["normalized"] = normalized
            data.setdefault("normalized", {})
            self._write_atomic(path, data)

    @staticmethod
    def _write_atomic(path: str, data: Dict[str, Any]) -> None:
        """先写临时文件再 `os.replace`：`open(path,'w')` 会先截断，此刻进程退出或磁盘满就留
        下一份截断 JSON。

        Windows 上 `os.replace` 会因目标文件刚被读过（防病毒/索引器还攥着句柄）瞬时抛
        PermissionError[WinError 5]，重试几百毫秒即通 —— 这不是测试现象，录一批带就会撞上。
        """
        fd, tmp = tempfile.mkstemp(dir=os.path.dirname(path), suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1, sort_keys=True)
            for attempt in range(20):
                try:
                    os.replace(tmp, path)
                    return
                except PermissionError:
                    if attempt == 19:
                        raise
                    time.sleep(0.02 * (attempt + 1))
        except BaseException:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def tape(self, key: str) -> Optional[Dict[str, Any]]:
        """整盘磁带（含 entries/normalized）；没有 entries 的盘当"没有磁带"。"""
        data = self._read(self._path(key))
        if not isinstance(data, dict) or not data.get("entries"):
            return None
        return data

    def count(self) -> int:
        try:
            return len([f for f in os.listdir(self.root) if f.endswith(".json")])
        except OSError:
            return 0

    def near(self, key: str, sent: Dict[str, Any],
             limit: int = _NEAR_LIMIT) -> List[Tuple[str, List[str]]]:
        """同 provider、最接近的几盘 —— 按 body 长度差粗排，只给要展示的那几盘算字段差。

        miss 是批量评估最常见的路径，全目录逐盘做字段 diff 会把一次键漂移的排查拖成分钟级。
        """
        provider = key.split("/")[0]
        try:
            names = sorted(os.listdir(self.root))
        except OSError:
            return []
        target_len = len(json.dumps(sent, sort_keys=True, ensure_ascii=False))
        gaps: List[Tuple[int, str, Dict[str, Any]]] = []
        for fname in names:
            if not fname.endswith(".json"):
                continue
            data = self._read(os.path.join(self.root, fname))
            if not isinstance(data, dict) or not str(data.get("key", "")).startswith(provider + "/"):
                continue
            stored = data.get("normalized") or {}
            gaps.append((abs(len(json.dumps(stored, sort_keys=True, ensure_ascii=False))
                            - target_len), data["key"], data))
        gaps.sort(key=lambda r: r[0])
        return [(k, _diff_against(data, sent)) for _gap, k, data in gaps[:limit]]

    def _read(self, path: str) -> Optional[Dict[str, Any]]:
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            return None
        except (OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            logger.warning("cassette_unreadable path=%s error=%s", path, exc)
            return None
        return data if isinstance(data, dict) else None


class _CassetteEngine:
    """录放的全部判断逻辑，两种 transport（异步 / 同步）共用这一份。

    为什么共用：`LLMService` 走同步 `httpx.Client`，agent 与 `DeepSeekProvider` 走异步的
    —— transport 基类是分开的，但"键怎么算、命中第几条、miss 报什么"必须一字不差，否则
    同一盘磁带在两个出口上表现不一致，那是最难查的那种不一致。

    游标是**进程级**的（`_engine_for` 按"模式 + 目录"复用同一个引擎）。真实通路里没有任何
    对象活得比一次调用更长：`LLMService` 每次新建客户端、`get_langchain_model` 每轮新建
    模型。游标若挂在 transport 实例上，429→200 的磁带会永远放回 429 —— 那是 Q10 的反面。
    """

    def __init__(self, store: CassetteStore, mode: str):
        self.store = store
        self.mode = mode
        # 游标按 (运行边界, 键) 记：`None` 表示"不在任何边界里"，保持严格语义。
        # 为什么键的命名空间要带上运行号：游标本身必须是进程级的（见 `_engine_for`），
        # 但一次评估跑到一半另一个评估进来时，清掉别人的游标就是把它的"多调了一次"抹平。
        self._cursor: Dict[Tuple[Optional[int], str], int] = {}
        self._cursor_lock = threading.Lock()

    def plan(self, url: str, body: bytes) -> Tuple[str, Dict[str, Any], str]:
        """(命中键, 进键并留档的请求体, 模型名)。一次解析，三样都从它出。

        非 JSON 请求（GET 抓取、表单）没有 body 可留档，它的身份全在查询串上：把打码后的
        查询串当作 `query` 字段留进磁带，否则 miss 报告只能说"两边都是空请求体"，而一次
        Bing 兜底的漏放十个里有九个是检索词变了。
        """
        refuse_model_call_in_work_unit(_provider_of(url))
        parsed = parse_body(body)
        key = request_key(url, body, parsed=parsed)
        # `parse_body(b"")` 给的是 `{}` 而不是 None：GET 空请求体也算"一个 JSON 对象"。
        # 所以这里判的是"有没有内容"，光判 isinstance 会让抓取型请求走进键的留档分支，
        # 磁带里留下一份空 normalized，miss 报告只能说"两边都是空请求体"。
        if isinstance(parsed, dict) and parsed:
            return key, normalize_request(parsed), str(parsed.get("model", ""))
        query = _query_for_key(url)
        return key, ({"query": query} if query else {}), ""

    def replay(self, key: str, sent: Dict[str, Any]) -> httpx.Response:
        tape = self.store.tape(key)
        if not tape:
            hints = self.store.near(key, sent)
            detail = ("; ".join(f"{k} → {'; '.join(d)}" for k, d in hints)
                      or "该 provider 下没有任何磁带")
            logger.warning("cassette_miss cassette=%s provider=%s tapes=%d",
                           key, key.split("/")[0], self.store.count())
            raise CassetteMiss(
                f"cassette miss: 键 {key} 没有录制。最接近的：{detail}。"
                f" 需要新磁带请显式用 {MODE_ENV}={RECORD} 录制，replay 不放行网络。")
        entries = tape["entries"]
        scope = _SCOPE.get() or _ambient_scope()
        with self._cursor_lock:
            idx = self._cursor.get((scope, key), 0)
            if idx >= len(entries):
                raise CassetteMiss(
                    f"cassette exhausted: 键 {key} 只录了 {len(entries)} 次响应，"
                    f"第 {idx + 1} 次调用没有对应录制（调用数变了就是行为变了）")
            self._cursor[(scope, key)] = idx + 1
        entry = entries[idx]
        logger.debug("cassette_replayed cassette=%s seq=%d/%d scope=%s "
                     "http.response.status_code=%s",
                     key, idx + 1, len(entries), _SCOPE_LABELS.get(scope, ""),
                     entry["status"])
        return httpx.Response(entry["status"], headers=entry.get("headers") or {},
                              content=base64.b64decode(entry["body_b64"]))

    def record(self, key: str, sent: Dict[str, Any], model: str, url: str,
               status: int, headers: Dict[str, str], payload: bytes) -> httpx.Response:
        """落一盘磁带，并把响应重建成可以交给 httpx 的新对象（原 response 已被读干）。

        重建必须过 `wire_headers`，理由见那个函数。
        """
        self.store.record(key, url, status, headers, payload, model=model, normalized=sent)
        prompt, completion = _usage_log_fields(payload)
        logger.info("cassette_recorded cassette=%s gen_ai.provider.name=%s "
                    "gen_ai.request.model=%s http.response.status_code=%s "
                    "gen_ai.usage.input_tokens=%s gen_ai.usage.output_tokens=%s",
                    key, _provider_of(url), model or "unknown", status, prompt, completion)
        return httpx.Response(status, headers=wire_headers(headers), content=payload)


_ENGINES: Dict[Tuple[str, str], _CassetteEngine] = {}
_ENGINES_LOCK = threading.Lock()
# 当前运行边界：contextvar 而不是全局变量，一次评估跑在 asyncio.Task 里时不串到兄弟任务；
# 值为 None 表示"不在任何边界里"，那时游标走进程级那一份（严格语义）。
_SCOPE: ContextVar[Optional[int]] = ContextVar("cassette_replay_scope", default=None)
_SCOPE_SEQ = itertools.count(1)
# token → label：只给日志用，跑完即弃（`_SCOPE_LABELS` 与游标同生命周期）
_SCOPE_LABELS: Dict[int, str] = {}
_SCOPE_LABELS_LOCK = threading.Lock()
# 环境命名的整进程边界（见 `_ambient_scope`）
SCOPE_ENV = "CHEMAI_CASSETTE_SCOPE"
_AMBIENT_LOCK = threading.Lock()
_AMBIENT_TOKEN: Optional[int] = None
_AMBIENT_LABEL: Optional[str] = None


def _engine_for(mode: str) -> _CassetteEngine:
    """按 (模式, 目录) 复用同一个引擎，所以重放游标是进程级的（见 `_CassetteEngine`）。

    replay 是只读路径：目录不存在就报错。默默建目录会把打错的路径建成一个空的，于是每盘都
    miss、日志写着 `tapes=0`，看着像"没录过"而不是"路径写错了"。
    """
    root = os.environ[CASSETTE_DIR_ENV]
    if mode == REPLAY and not os.path.isdir(root):
        raise RuntimeError(f"{CASSETTE_DIR_ENV} 指向的目录不存在：{root}（replay 不建目录）")
    with _ENGINES_LOCK:
        engine = _ENGINES.get((mode, root))
        if engine is None:
            engine = _CassetteEngine(CassetteStore(root, create=mode != REPLAY), mode)
            _ENGINES[(mode, root)] = engine
        return engine


def _ambient_scope() -> Optional[int]:
    """整进程一份的运行边界，由 `CHEMAI_CASSETTE_SCOPE` 点名 —— 评估 runner 用它。

    为什么不是"runner 在调用处包一层 `replay_scope`"：一条评估里既有事件循环也有线程池，而
    `threading.Thread` 起始拿到的是 contextvar 的默认值（不继承主线程那份），包在主线程上等于
    只给一半调用起了边界。环境变量的语义正好是"这个进程就是这一轮"。

    标签换了就换一本账：runner 在同一进程里连跑两条评估时（`kind="function"` 那种是进程内跑的），
    每条各要自己的游标，不然第二条会接着第一条的序号往下放。
    """
    global _AMBIENT_TOKEN, _AMBIENT_LABEL
    label = os.getenv(SCOPE_ENV, "").strip()
    if not label:
        return None
    with _AMBIENT_LOCK:
        # 返回也在锁内：锁外读全局，换个标签的瞬间就可能有读者拿到"新标签 + 旧一本账"
        if _AMBIENT_TOKEN is None or _AMBIENT_LABEL != label:
            with _SCOPE_LABELS_LOCK:
                _AMBIENT_TOKEN = next(_SCOPE_SEQ)
                _SCOPE_LABELS[_AMBIENT_TOKEN] = f"env:{label}"
            _AMBIENT_LABEL = label
        return _AMBIENT_TOKEN


def _drop_scope(token: int) -> None:
    """运行边界结束时丢掉它那份游标：游标活在进程级引擎上，不清就是每个边界攒一份记账。

    环境边界（`_ambient_scope`）不在这里丢：它属于整个进程，而标签换代之前的那一本账留着也只是
    内存里几个整数 —— 评估跑完进程就退了。
    """
    with _ENGINES_LOCK:
        engines = list(_ENGINES.values())
    for engine in engines:
        with engine._cursor_lock:
            for ns in [k for k in engine._cursor if k[0] == token]:
                del engine._cursor[ns]
    with _SCOPE_LABELS_LOCK:
        _SCOPE_LABELS.pop(token, None)


@contextlib.contextmanager
def replay_scope(label: str = "run"):
    """一次重放运行的边界：这一轮的游标从第一条响应开始放，且只属于这一轮。

    游标是进程级的（不然每次新建客户端都会从头放，429→200 的序列走不完）。但"同一个键出现第
    二次"有两种完全不同的原因：一次运行内真的多调了一次（那是行为变了，该响），或者同一份评估
    跑了第二遍 / 另一个并发评估也打了这个请求（那是记账要分开）。边界给后者一个自己的命名空间，
    而不是把前者的证据清掉 —— 上一版是 `clear()` 全部引擎，两个并发评估会互相抹平。

    嵌套沿用外层的边界：内层另起一本账会让"这轮多调了一次"从中间那次调用上溜掉。
    """
    token = _SCOPE.get()
    fresh = token is None
    if fresh:
        token = next(_SCOPE_SEQ)
        with _SCOPE_LABELS_LOCK:
            _SCOPE_LABELS[token] = label
        logger.debug("replay_scope enter token=%d label=%s", token, label)
    reset = _SCOPE.set(token)
    try:
        yield token
    finally:
        _SCOPE.reset(reset)
        if fresh:
            _drop_scope(token)


_INNER_ASYNC: Optional[httpx.AsyncBaseTransport] = None
_INNER_SYNC: Optional[httpx.BaseTransport] = None
_INNER_LOCK = threading.Lock()


def _inner_async() -> httpx.AsyncBaseTransport:
    """record 模式下的真网络内层，进程级复用。

    每个 transport 现场建一个内层的话，每次都要重付一次 SSL 上下文并放弃整套连接池（本机实测
    ~215ms/次），录带慢且 TLS 会话永不复用。
    """
    global _INNER_ASYNC
    with _INNER_LOCK:
        if _INNER_ASYNC is None:
            _INNER_ASYNC = httpx.AsyncHTTPTransport()
        return _INNER_ASYNC


def _inner_sync() -> httpx.BaseTransport:
    global _INNER_SYNC
    with _INNER_LOCK:
        if _INNER_SYNC is None:
            _INNER_SYNC = httpx.HTTPTransport()
        return _INNER_SYNC


class CassetteTransport(httpx.AsyncBaseTransport):
    """异步出口的 transport：record 透传并落盘，replay 只读磁带，live 原样交给内层。"""

    def __init__(self, store: Optional[CassetteStore] = None, mode: str = LIVE,
                 inner: Optional[httpx.AsyncBaseTransport] = None,
                 engine: Optional[_CassetteEngine] = None):
        self._engine = engine or _CassetteEngine(store, mode)
        self._inner = inner

    @property
    def store(self) -> CassetteStore:
        return self._engine.store

    @property
    def mode(self) -> str:
        return self._engine.mode

    def _ensure_inner(self) -> httpx.AsyncBaseTransport:
        if self._inner is None:
            self._inner = _inner_async()
        return self._inner

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if self.mode == LIVE:
            return await self._ensure_inner().handle_async_request(request)

        body = await request.aread()
        key, sent, model = self._engine.plan(str(request.url), body)
        if self.mode == REPLAY:
            return self._engine.replay(key, sent)

        response = await self._ensure_inner().handle_async_request(request)
        payload = await response.aread()
        return self._engine.record(key, sent, model, str(request.url), response.status_code,
                                   dict(response.headers), payload)


class CassetteSyncTransport(httpx.BaseTransport):
    """同步版：`LLMService` 用的是 `httpx.Client`，不吃 async transport。

    与异步版共用同一个 `_CassetteEngine`，所以同一盘磁带在两个出口上命中同一个键、报同一种
    miss，也走同一条重放游标。
    """

    def __init__(self, store: Optional[CassetteStore] = None, mode: str = LIVE,
                 inner: Optional[httpx.BaseTransport] = None,
                 engine: Optional[_CassetteEngine] = None):
        self._engine = engine or _CassetteEngine(store, mode)
        self._inner = inner

    @property
    def store(self) -> CassetteStore:
        return self._engine.store

    @property
    def mode(self) -> str:
        return self._engine.mode

    def _ensure_inner(self) -> httpx.BaseTransport:
        if self._inner is None:
            self._inner = _inner_sync()
        return self._inner

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        if self.mode == LIVE:
            return self._ensure_inner().handle_request(request)

        body = request.read()
        key, sent, model = self._engine.plan(str(request.url), body)
        if self.mode == REPLAY:
            return self._engine.replay(key, sent)

        response = self._ensure_inner().handle_request(request)
        payload = response.read()
        return self._engine.record(key, sent, model, str(request.url), response.status_code,
                                   dict(response.headers), payload)


_USAGE_PROMPT = ("prompt_tokens", "input_tokens")
_USAGE_COMPLETION = ("completion_tokens", "output_tokens")


def _first_int(source: Dict[str, Any], names) -> Optional[int]:
    """取第一个"确实是正整数"的字段 —— bool 是 int 的子类，端点不会用它报 token。"""
    for name in names:
        value = source.get(name)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            return value
    return None


def normalize_usage(raw: Any) -> Optional[Dict[str, int]]:
    """把三种写法的 usage 归一成 {prompt_tokens, completion_tokens, total_tokens}。

    OpenAI 兼容端点报 `prompt_tokens`/`completion_tokens`，LangChain 聚合后报
    `input_tokens`/`output_tokens`，另外两处把这份数嵌在 `usage`（响应体）或 `token_usage`
    （response_metadata）里。字段名只在这一个函数里认（共识 Q14），否则同一轮对话在两个地方
    显示两个数，而那种差异没人会当场发现。
    认不出任何已知字段就返回 None，不返回 0 —— "端点没报用量"与"报了就 0 token"是两件事，
    前者要让上层回退到估算（见 `agent/context_meter.build_context_event`）。
    """
    if not isinstance(raw, dict):
        return None
    for wrapper in ("usage", "token_usage"):
        inner = raw.get(wrapper)
        if isinstance(inner, dict):
            raw = {**inner, **{k: v for k, v in raw.items() if k == "total_tokens"}}
            break
    prompt = _first_int(raw, _USAGE_PROMPT)
    completion = _first_int(raw, _USAGE_COMPLETION)
    if prompt is None and completion is None:
        return None
    total = _first_int(raw, ("total_tokens",)) or (prompt or 0) + (completion or 0)
    return {"prompt_tokens": prompt or 0, "completion_tokens": completion or 0,
            "total_tokens": total}


def prompt_tokens_of(output: Any) -> Optional[int]:
    """从模型输出里取 provider 报的输入 token 数（dict / LangChain 对象 / 流式列表都行）。

    取数顺序就是"哪个字段算权威"的答案：`usage_metadata`（流式聚合，含缓存输入）优先，其次
    `response_metadata` 里那份。dict 与对象两条路共用 `normalize_usage` 认字段，所以同一个
    逻辑输入两边给同一个数。
    """
    if output is None:
        return None
    if isinstance(output, list):
        return prompt_tokens_of(output[-1] if output else None)
    holders: List[Any]
    if isinstance(output, dict):
        holders = [output.get(h) for h in ("usage_metadata", "usage", "token_usage")]
        holders.append(output)
    else:
        holders = [getattr(output, "usage_metadata", None), getattr(output, "usage", None),
                   getattr(output, "response_metadata", None)]
    for holder in holders:
        if isinstance(holder, dict):
            got = (normalize_usage(holder) or {}).get("prompt_tokens")
            if got:
                return got
    return None


def _json_of(payload: bytes) -> Any:
    try:
        return json.loads(payload.decode("utf-8")) if payload else {}
    except (UnicodeDecodeError, json.JSONDecodeError):
        return {}


def _usage_log_fields(payload: bytes) -> Tuple[Any, Any]:
    """从响应体取 (输入 token, 输出 token)；取不到就是 (None, None) —— 日志不许编数。"""
    usage = normalize_usage(_json_of(payload)) or {}
    return usage.get("prompt_tokens"), usage.get("completion_tokens")


# 照抄 openai 2.53 建客户端时的三样默认值（DEFAULT_TIMEOUT、DEFAULT_CONNECTION_LIMITS，以及
# `kwargs.setdefault("follow_redirects", True)` —— httpx 自己默认**不**跟重定向）。理由与漂移
# 动作见 ADR-0023 决策 9；绊线比的是 SDK 自己的常量，不是字面量。
SDK_TIMEOUT = httpx.Timeout(600.0, connect=5.0, write=600.0, pool=600.0)
SDK_LIMITS = httpx.Limits(max_connections=1000, max_keepalive_connections=100,
                          keepalive_expiry=5.0)
SDK_CLIENT_KWARGS: Dict[str, Any] = {"timeout": SDK_TIMEOUT, "limits": SDK_LIMITS,
                                     "follow_redirects": True}


def model_transport() -> Optional[httpx.AsyncBaseTransport]:
    """异步出口的 transport：live 返回 None（httpx 用自己的，等于这层不存在）。"""
    mode = resolve_mode()
    return None if mode == LIVE else CassetteTransport(engine=_engine_for(mode))


def model_sync_transport() -> Optional[httpx.BaseTransport]:
    """同步出口（`LLMService`）的 transport，与 `model_transport` 同一个模式、同一个引擎。"""
    mode = resolve_mode()
    return None if mode == LIVE else CassetteSyncTransport(engine=_engine_for(mode))


def build_model_client() -> httpx.AsyncClient:
    """给 `ChatOpenAI` 注入的 AsyncClient —— 只在 record/replay 下用。

    live 下 langchain 对自建客户端有 `lru_cache`（同 base_url 全进程共享一份连接池），注入就
    每轮重付一次 SSL 上下文（同一个代价，实测数见 `_inner_async`）。参数照抄
    `SDK_CLIENT_KWARGS`，免得录放路径与生产路径行为不同。
    """
    return httpx.AsyncClient(transport=model_transport(), **SDK_CLIENT_KWARGS)
