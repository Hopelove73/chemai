"""DeepSeek Provider — 供 tools.py 中需要自己调 LLM 的 skill 使用"""
import os
import asyncio
import random
from dataclasses import dataclass, field
from typing import AsyncIterator
from dotenv import load_dotenv

# 这两个出口要在业务层早判一次"决定单元内不许等网络"（ADR-0021 规则 2）。缝上 `_CassetteEngine.plan`
# 已经判过，但 live 下 `ChatOpenAI` 走 langchain 自建的共享客户端，transport 层够不到 ——
# 那处残余就是这里留着的原因，见 ADR-0023 决策 16。
# THINKING_OFF：这几个调用方（意图分类、skill）要的是固定格式，思考会把 max_tokens 吃满
# 然后返回空正文，实测过。对话讲评那条留思考，走的是 `agent/guard.py`。
from app.config import config as _cfg
from app.services.llm_transport import THINKING_OFF, refuse_model_call_in_work_unit

load_dotenv()

# ── LLM 请求级重试策略（2026-08，DSH llm-retry 同款）──
# 只重试可重试错误（限流/服务端/超时/传输），4xx 语义错误立即抛出
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}
_RETRY_MAX = 2          # 最多重试 2 次（共 3 次尝试）
_RETRY_BASE_MS = 500    # 首次退避
_RETRY_MAX_MS = 10_000  # 退避封顶
_RETRY_JITTER = 0.1     # ±10% 抖动


def _is_retryable(status: int | None, exc: BaseException | None = None) -> bool:
    if status is not None and status in _RETRYABLE_STATUS:
        return True
    if exc is not None:
        import httpx
        return isinstance(exc, (httpx.TransportError, httpx.TimeoutException))
    return False


def _retry_delay(attempt: int) -> float:
    """attempt=1 → 500ms；指数退避封顶 10s；±10% 抖动（秒）。"""
    exp = min(_RETRY_BASE_MS * (2 ** (attempt - 1)), _RETRY_MAX_MS)
    jitter = 1 - _RETRY_JITTER + 2 * _RETRY_JITTER * random.random()
    return (exp * jitter) / 1000.0


@dataclass
class ChatResult:
    content: str
    tool_calls: list = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""


class DeepSeekProvider:
    def __init__(self, model: str = "", timeout: float = 60.0):
        import httpx

        from app.services.llm_transport import model_transport

        self.api_key = os.getenv("DEEPSEEK_API_KEY", "")
        self.base_url = "https://api.deepseek.com/beta"
        # 模型名跟着 .env 走（同 `llm_service`）：换模型之后留一条写死旧名的出口，
        # "我换模型了"就只对一部分通路成立。
        self.model = model or _cfg.DEEPSEEK_MODEL
        # transport=模型出口的录放接缝（ADR-0023）；live 下是 None，与注入前完全一样。
        # miss 在这里天然不重试：`_is_retryable` 只认 TransportError/TimeoutException，
        # CassetteMiss 走 `raise` 直接冒到调用方。
        # timeout 默认 60s 够分类/小生成；大预算非流式（如教学技能 16k HTML）必须
        # 调大——guard.py 实测 16k 最坏约 74s，60s 会掐断后再重试两次满等。
        self._client = httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self.api_key}"},
            timeout=timeout,
            transport=model_transport(),
        )

    async def chat(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        tools: list[dict] = None,
    ) -> ChatResult:
        refuse_model_call_in_work_unit("DeepSeekProvider.chat")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        payload.update(THINKING_OFF)

        for attempt in range(1, _RETRY_MAX + 2):  # 1..3
            try:
                resp = await self._client.post("/chat/completions", json=payload)
                if resp.status_code == 200:
                    data = resp.json()
                    choice = data["choices"][0]
                    return ChatResult(
                        content=choice["message"].get("content", ""),
                        tool_calls=choice["message"].get("tool_calls", []),
                        usage=data.get("usage", {}),
                        model=data.get("model", ""),
                    )
                if not _is_retryable(resp.status_code):
                    raise Exception(f"API error {resp.status_code}: {resp.text[:200]}")
                last_err = Exception(f"API error {resp.status_code}: {resp.text[:200]}")
            except Exception as e:
                if not _is_retryable(None, e):
                    raise
                last_err = e
            if attempt <= _RETRY_MAX:
                await asyncio.sleep(_retry_delay(attempt))
        # 重试耗尽：显式抛错（不再静默返回空结果——空结果会被误当成成功）
        raise last_err  # noqa: BLE001

    async def chat_stream(
        self,
        messages: list[dict],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        tools: list[dict] = None,
    ) -> AsyncIterator[str]:
        refuse_model_call_in_work_unit("DeepSeekProvider.chat_stream")
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }
        payload.update(THINKING_OFF)
        if tools:
            payload["tools"] = tools

        # 流式重试只覆盖"首块之前"的失败（建连/HTTP 状态错误）；一旦开始出块，
        # 中途断流直接抛出（重试会产生重复前缀，交给上层处理）
        attempt = 1
        while True:
            started = False
            try:
                async with self._client.stream("POST", "/chat/completions", json=payload) as resp:
                    if resp.status_code != 200:
                        if _is_retryable(resp.status_code) and attempt <= _RETRY_MAX:
                            await asyncio.sleep(_retry_delay(attempt))
                            attempt += 1
                            continue
                        raise Exception(f"API error {resp.status_code}")
                    async for line in resp.aiter_lines():
                        if line.startswith("data: "):
                            started = True
                            yield line[6:]
                return
            except Exception as e:
                if started or attempt > _RETRY_MAX or not _is_retryable(None, e):
                    raise
                await asyncio.sleep(_retry_delay(attempt))
                attempt += 1

    async def close(self):
        await self._client.aclose()

    @property
    def model_name(self) -> str:
        return f"deepseek/{self.model}"


# Module-level singleton for the intent classifier (shared connection pool)
classifier_provider: DeepSeekProvider | None = None
_classifier_key: tuple | None = None


def get_classifier_provider() -> DeepSeekProvider:
    """Return or create the shared classifier provider singleton.

    键里要带上（出口模式, 磁带目录）：transport 是在建客户端那一刻绑上去的，纯单例于是把第一次
    调用时的目录一路用到底 —— 同一进程里连跑第二条评估时，它放的是**第一条**的磁带，游标错位
    还一声不响（评估 runner 的 `kind="function"` 条目正是进程内跑的）。live 下这个键恒定，
    行为与纯单例一致，连接池照样共享。
    """
    global classifier_provider, _classifier_key
    from app.services.llm_transport import CASSETTE_DIR_ENV, LIVE, MODE_ENV

    key = (os.getenv(MODE_ENV, LIVE), os.getenv(CASSETTE_DIR_ENV, ""))
    if classifier_provider is None or _classifier_key != key:
        classifier_provider = DeepSeekProvider()
        _classifier_key = key
    return classifier_provider
