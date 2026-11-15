"""确认门过期作业（T17）—— APScheduler 包装。

`handle_gate_expiry` 是同步的（`app/jobs/handlers.py` 的一贯形状：幂等、可脱离队列
单独测试），这里用 `asyncio.to_thread` 让它在事件循环外跑：整轮要开好几次库、还可能
扫到几十行门，直接在 AsyncIOScheduler 的循环里同步跑会卡住所有请求。

与 `parent_summary` / `early_warning_check` 同一形状。
"""
import asyncio
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _run_sync() -> dict:
    from app.jobs.handlers import handle_gate_expiry
    return handle_gate_expiry()


async def run_gate_expiry() -> None:
    """每日一圈：收敛假门 → 作废超期未决的门 → 对账卡死的 run。

    失败只记日志不抛：这是善后作业，一次失败不该把调度器带崩，下一轮自然重来
    （三件事都幂等）。
    """
    try:
        out = await asyncio.to_thread(_run_sync)
    except Exception as e:  # noqa: BLE001
        logger.warning("gate expiry job failed: %s", e, exc_info=True)
        return
    if out.get("stale_superseded") or out.get("expired") or out.get("runs_settled"):
        logger.info("gate expiry done at %s: %s", datetime.now(timezone.utc), out)
