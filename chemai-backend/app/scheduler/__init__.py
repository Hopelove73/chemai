"""
调度器模块
使用APScheduler实现定时任务
"""

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import timezone
from zoneinfo import ZoneInfo as _ZoneInfo
_TZ = _ZoneInfo("Asia/Shanghai")

scheduler = AsyncIOScheduler(timezone=timezone.utc)


def init_scheduler(app):
    """初始化调度器并注册任务"""
    from app.scheduler.daily_practice import daily_practice_job
    from app.scheduler.early_warning_check import check_warnings_job
    from app.scheduler.parent_summary import run_daily_summary, run_weekly_summary

    # 每日早上8点推送练习任务
    scheduler.add_job(
        daily_practice_job,
        CronTrigger(hour=8, minute=0),
        id='daily_practice',
        name='每日练习推送',
        replace_existing=True
    )

    # 每6小时检查一次预警
    scheduler.add_job(
        check_warnings_job,
        CronTrigger(hour=0, minute=0),
        id='check_warnings',
        name='学情预警检查',
        replace_existing=True
    )

    # OCR 答题卡轮询 (每5秒)
    # 家长摘要（批 3 P1 / REQ-GDS-004）：weekly 周日 20:00、daily 每日 08:00（Asia/Shanghai）
    scheduler.add_job(
        run_weekly_summary,
        CronTrigger(day_of_week="sun", hour=20, minute=0, timezone=_TZ),
        id="parent_weekly_summary", replace_existing=True,
    )
    scheduler.add_job(
        run_daily_summary,
        CronTrigger(hour=8, minute=0, timezone=_TZ),
        id="parent_daily_summary", replace_existing=True,
    )

    # 每日练习督学提醒（§11 B4）：整点扫一次，按家长自选的 hour 发
    from app.scheduler.daily_practice import practice_reminder_job
    scheduler.add_job(
        practice_reminder_job,
        CronTrigger(minute=0, timezone=_TZ),
        id='practice_reminder', name='练习督学提醒', replace_existing=True,
    )

    from app.scheduler.ocr_scheduler import process_pending_ocr_tasks
    import asyncio as _asyncio
    async def _ocr_wrapper():
        await _asyncio.to_thread(process_pending_ocr_tasks)
    scheduler.add_job(
        _ocr_wrapper,
        trigger='interval',
        seconds=5,
        id='ocr_processor',
        name='OCR答题卡识别',
        replace_existing=True,
    )

    # 主观题待判定扫描（ADR-0018：无 Redis 部署的消费兜底）
    # 走自开会话的作业入口（与 Celery 同一个 handle_semantic_scan），
    # 不能直接调 scan_semantic_pending —— 它要 db 会话，零参调用每轮只会抛 TypeError
    from app.jobs.handlers import handle_semantic_scan
    import asyncio as _aio2
    async def _semantic_scan_wrapper():
        await _aio2.to_thread(handle_semantic_scan)
    scheduler.add_job(
        _semantic_scan_wrapper,
        trigger='interval',
        seconds=120,
        id='semantic_scan',
        name='主观题待判定扫描',
        replace_existing=True,
    )

    # 练习批次兜底消费（无 Redis 部署：enqueue 只登记，逐生布置由本作业接手）
    from app.services.assignment_service import handle_pending_batch_targets
    async def _assignment_drain_wrapper():
        await _aio2.to_thread(handle_pending_batch_targets)
    scheduler.add_job(
        _assignment_drain_wrapper,
        trigger='interval',
        seconds=60,
        id='assignment_drain',
        name='练习批次兜底布置',
        replace_existing=True,
    )

    # 确认门过期作业（T17 / gate-lifecycle 批次 3）：每日 03:00（Asia/Shanghai）
    # 三件事一起：收敛假门 → 作废超期未决的门 → 对账卡死的 run。
    # 挑凌晨：整轮要开好几次库、可能扫几十行，别跟老师的使用高峰抢。
    # 为什么必须有它：门开着就全拦，而没有别的路径会替老师放弃 —— 一张没人点过的
    # 卡片能把那个会话永久锁住（2026-10-17 实测：演示库里攒了 41 扇算数的未决门）。
    from app.scheduler.gate_expiry import run_gate_expiry
    scheduler.add_job(
        run_gate_expiry,
        CronTrigger(hour=3, minute=0, timezone=_TZ),
        id='gate_expiry',
        name='确认门过期与对账',
        replace_existing=True,
    )

    scheduler.start()
    print("调度器已启动")


def shutdown_scheduler():
    """关闭调度器"""
    if scheduler.running:
        scheduler.shutdown()
        print("调度器已关闭")
