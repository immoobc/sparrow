"""采集任务调度器 — 完整版

用法:
    python -m src.collector.scheduler

定时任务时间表 (工作日 Mon-Fri):
    09:00  股票列表更新
    15:05  北向资金收盘数据
    15:30  强势股归因 (同花顺)
    15:35  每日K线采集 (mootdx)
    15:40  每日估值采集 (腾讯)
    15:45  行业板块行情 (东财)
    18:00  龙虎榜+席位 (东财)
    20:00  大宗交易 (东财)
    21:00  数据质量检查
"""

from datetime import date

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from src.logger import logger


def _get_stock_codes() -> list[str]:
    """获取全市场活跃股票代码"""
    from sqlalchemy import text
    from src.storage.database import SessionLocal

    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT code FROM stock_basic WHERE is_active = TRUE ORDER BY code")
        )
        return [row[0].strip() for row in result]
    finally:
        db.close()


# ── 各任务函数 ──────────────────────────────────────────────


def job_stock_list():
    """每日股票列表更新"""
    from src.collector.stock_list_collector import collect_stock_list
    try:
        collect_stock_list()
    except Exception as e:
        logger.error(f"[调度] 股票列表更新异常: {e}")


def job_daily_kline():
    """每日K线采集"""
    from src.collector.daily_kline_collector import collect_daily_all
    try:
        codes = _get_stock_codes()
        if codes:
            collect_daily_all(codes)
    except Exception as e:
        logger.error(f"[调度] 每日K线异常: {e}")


def job_index_daily():
    """每日指数K线采集"""
    from src.collector.index_collector import collect_index_daily
    try:
        collect_index_daily()
    except Exception as e:
        logger.error(f"[调度] 指数K线异常: {e}")


def job_valuation():
    """每日估值采集"""
    from src.collector.valuation_collector import collect_valuation_daily
    try:
        codes = _get_stock_codes()
        if codes:
            collect_valuation_daily(codes)
    except Exception as e:
        logger.error(f"[调度] 估值采集异常: {e}")


def job_northbound():
    """北向资金"""
    from src.collector.northbound_collector import collect_northbound_daily
    try:
        collect_northbound_daily()
    except Exception as e:
        logger.error(f"[调度] 北向资金异常: {e}")


def job_hot_stocks():
    """强势股归因"""
    from src.collector.hot_stocks_collector import collect_hot_stocks
    try:
        collect_hot_stocks()
    except Exception as e:
        logger.error(f"[调度] 强势股归因异常: {e}")


def job_sector_daily():
    """行业板块行情"""
    from src.collector.sector_collector import collect_sector_daily
    try:
        collect_sector_daily()
    except Exception as e:
        logger.error(f"[调度] 行业板块异常: {e}")


def job_dragon_tiger():
    """龙虎榜"""
    from src.collector.dragon_tiger_collector import collect_daily_dragon_tiger
    try:
        collect_daily_dragon_tiger()
    except Exception as e:
        logger.error(f"[调度] 龙虎榜异常: {e}")


def job_block_trade():
    """大宗交易"""
    from src.collector.event_collector import collect_block_trade
    try:
        collect_block_trade()
    except Exception as e:
        logger.error(f"[调度] 大宗交易异常: {e}")


def job_quality_check():
    """数据质量检查"""
    from src.quality.checker import check_daily_completeness, check_data_freshness
    try:
        check_daily_completeness()
        check_data_freshness()
    except Exception as e:
        logger.error(f"[调度] 质量检查异常: {e}")


# ── 调度器配置 ──────────────────────────────────────────────


def create_scheduler() -> BlockingScheduler:
    """创建并配置调度器"""
    scheduler = BlockingScheduler(timezone="Asia/Shanghai")
    weekdays = "mon-fri"

    jobs = [
        # (函数, 小时, 分钟, ID, 名称)
        (job_stock_list, 9, 0, "stock_list", "股票列表更新"),
        (job_northbound, 15, 5, "northbound", "北向资金"),
        (job_hot_stocks, 15, 30, "hot_stocks", "强势股归因"),
        (job_daily_kline, 15, 35, "daily_kline", "每日K线"),
        (job_index_daily, 15, 36, "index_daily", "指数K线"),
        (job_valuation, 15, 40, "valuation", "每日估值"),
        (job_sector_daily, 15, 45, "sector_daily", "行业板块行情"),
        (job_dragon_tiger, 18, 0, "dragon_tiger", "龙虎榜"),
        (job_block_trade, 20, 0, "block_trade", "大宗交易"),
        (job_quality_check, 21, 0, "quality_check", "数据质量检查"),
    ]

    for func, hour, minute, job_id, name in jobs:
        scheduler.add_job(
            func,
            CronTrigger(day_of_week=weekdays, hour=hour, minute=minute),
            id=job_id,
            name=name,
        )

    return scheduler


def main():
    logger.info("=" * 50)
    logger.info("  Sparrow 调度器启动")
    logger.info("=" * 50)

    scheduler = create_scheduler()

    logger.info("已注册任务:")
    for job in scheduler.get_jobs():
        logger.info(f"  {job.name:15s} | {job.trigger}")

    logger.info("-" * 50)
    logger.info("按 Ctrl+C 停止")

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        logger.info("调度器已停止")


if __name__ == "__main__":
    main()
