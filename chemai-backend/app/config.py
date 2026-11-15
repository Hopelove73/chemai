"""Centralized config — loads .env once, provides typed access.

Import: from app.config import config
Usage: config.DEEPSEEK_API_KEY, config.DATABASE_URL
"""
import os
from dotenv import load_dotenv
from functools import lru_cache

load_dotenv()


class _Config:
    """Application configuration singleton."""

    # Database
    DATABASE_URL: str = os.getenv("DATABASE_URL", "sqlite:///./chemai.db")

    # LLM Providers
    DEEPSEEK_API_KEY: str = os.getenv("DEEPSEEK_API_KEY", "")
    DEEPSEEK_MODEL: str = os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    DEEPSEEK_BASE_URL: str = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

    # 上下文窗口按"真正在服务对话的那个模型"定：圆环的分母就是"这个模型还剩多少可吃"。
    # deepseek-flash 标称 1M、deepseek-chat 是 128K（官方 模型 & 价格 页），沿用写死的
    # 128000 会在离上限还远得很的时候喊"满了"，把老师劝去开新会话。认不出的模型退回 128K ——
    # 分母猜小只会提前提醒，猜大会漏报，所以取小的那个。
    # `CONTEXT_WINDOW` 环境变量仍可覆盖，那时圆环读的是"预算"而不是"容量"。
    # 本机就覆盖成 200000：按 1M 画环会一直贴近 0%，等于取消了这个预警（用户裁定按 200K 报）。
    MODEL_CONTEXT_WINDOWS: dict = {"deepseek-chat": 128_000,
                                   "deepseek-reasoner": 128_000,
                                   "deepseek-flash": 1_000_000}
    # 哪些 DeepSeek 型号真能看图。不能从 `DEEPSEEK_MODEL` 猜：文本 provider 与
    # 视觉 provider 可以是不同型号（mimo 那边就是）。型号名当值用的地方只许在配置里，
    # 绊线就是这么放的。
    VISION_CAPABLE_MODELS: frozenset = frozenset({"deepseek-flash"})

    CONTEXT_WINDOW: int = int(os.getenv("CONTEXT_WINDOW", "0")) or \
        MODEL_CONTEXT_WINDOWS.get(DEEPSEEK_MODEL, 128_000)

    XIAOMI_API_KEY: str = os.getenv("XIAOMI_API_KEY", "")
    MIMO_MODEL: str = os.getenv("MIMO_MODEL", "mimo-v2.6-flash")
    MIMO_BASE_URL: str = os.getenv("MIMO_BASE_URL", "https://api.xiaomimimo.com/v1")


    DASHSCOPE_API_KEY: str = os.getenv("DASHSCOPE_API_KEY", "")
    DASHSCOPE_MODEL: str = os.getenv("DASHSCOPE_MODEL", "qwen-turbo")

    # Server
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8000"))
    DEBUG: bool = os.getenv("DEBUG", "").lower() in ("1", "true", "yes")

    # Baidu OCR
    BAIDU_OCR_APP_ID: str = os.getenv("BAIDU_OCR_APP_ID", "")
    BAIDU_OCR_API_KEY: str = os.getenv("BAIDU_OCR_API_KEY", "")
    BAIDU_OCR_SECRET_KEY: str = os.getenv("BAIDU_OCR_SECRET_KEY", "")

    # MinerU
    MINERU_PATH: str = os.getenv("MINERU_PATH", "")
    MINERU_ENABLED: bool = os.getenv("MINERU_ENABLED", "1").lower() not in ("0", "false", "no")

    # OCR Sheet Provider (mineru / baidu / paddle) — ADR-0004: 默认 PP-OCRv6_small
    OCR_SHEET_PROVIDER: str = os.getenv("OCR_SHEET_PROVIDER", "paddle")
    # 低置信/缺答案任务单卡视觉复核（默认开，见 docs/ocr-answer-sheet-plan.md a4）
    OCR_VISION_REVIEW_ENABLED: bool = os.getenv("OCR_VISION_REVIEW_ENABLED", "1").lower() not in ("0", "false", "no")

    # Test
    SKIP_DB_INIT: bool = os.getenv("SKIP_DB_INIT", "").lower() in ("1", "true", "yes")

    # API
    WRAP_RESPONSES: bool = os.getenv("WRAP_RESPONSES", "1").lower() not in ("0", "false", "no")

    @property
    def provider_keys(self) -> dict:
        return {
            "deepseek": self.DEEPSEEK_API_KEY,
            "mimo": self.XIAOMI_API_KEY,
            "dashscope": self.DASHSCOPE_API_KEY,
        }


@lru_cache
def get_config() -> _Config:
    return _Config()


# Module-level singleton
config = get_config()
