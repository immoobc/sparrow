"""一键更新 — 整合所有数据采集步骤

一个函数搞定:
1. 更新关注的ETF/股票最新K线
2. 增量采集全市场当日K线(如果盘后)
3. 刷新 Parquet 缓存
4. 返回更新状态

并发保护:
- 同一时间只有一个更新任务在执行
- 其他并发请求等待复用同一次结果（去峰）
- 60秒内重复请求直接返回上次结果（防抖）
"""

import time
import threading
from datetime import date, timedelta

from src.datasource.mootdx_source import get_client, _get_market
from src.logger import logger
from src.storage.cache import export_to_parquet, get_cache_info

# 你关注的标的 (ETF + 少量个股)
WATCH_LIST = [
    "513050",  # 中概互联ETF
    "159941",  # 纳指ETF
    "510300",  # 沪深300ETF
    "510500",  # 中证500ETF
    "159915",  # 创业板ETF
]

# ── 并发去重机制 ──
_update_lock = threading.Lock()
_last_result: dict | None = None
_last_update_time: float = 0
_DEBOUNCE_SECONDS = 60  # 60秒内重复请求直接返回缓存结果


def update_watchlist() -> dict:
    """更新关注列表的最新K线到数据库"""
    import psycopg2
    import io
    from src.config import settings

    client = get_client()
    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()

    total = 0
    for code in WATCH_LIST:
        market = _get_market(code)
        raw = client.client.get_security_bars(4, market, code, 0, 30)
        if not raw:
            continue

        buf = io.StringIO()
        count = 0
        for item in raw:
            year = item.get("year", 0)
            dt = item.get("datetime", "")
            if year < 2000 or year > 2030 or not dt:
                continue
            line = f"{code}\t{dt[:10]}\t{item['open']:.3f}\t{item['high']:.3f}\t{item['low']:.3f}\t{item['close']:.3f}\t{int(item.get('vol',0))}\t{item.get('amount',0):.2f}\n"
            buf.write(line)
            count += 1

        buf.seek(0)
        cur.execute("""
            CREATE TEMP TABLE IF NOT EXISTS _tmp_kline (
                code CHAR(6), trade_date DATE, open DECIMAL(10,3),
                high DECIMAL(10,3), low DECIMAL(10,3), close DECIMAL(10,3),
                volume BIGINT, amount DECIMAL(18,2)
            ) ON COMMIT DROP
        """)
        cur.execute("TRUNCATE _tmp_kline")
        cur.copy_from(buf, "_tmp_kline", columns=("code", "trade_date", "open", "high", "low", "close", "volume", "amount"))
        cur.execute("""
            INSERT INTO stock_daily (code, trade_date, open, high, low, close, volume, amount)
            SELECT code, trade_date, open, high, low, close, volume, amount FROM _tmp_kline
            ON CONFLICT (code, trade_date) DO UPDATE SET
                open = EXCLUDED.open, high = EXCLUDED.high,
                low = EXCLUDED.low, close = EXCLUDED.close,
                volume = EXCLUDED.volume, amount = EXCLUDED.amount
        """)
        conn.commit()
        total += count
        logger.info(f"  [{code}] 更新 {count} 条")

    cur.close()
    conn.close()
    return {"updated_codes": len(WATCH_LIST), "total_rows": total}


def one_click_update(full_market: bool = False) -> dict:
    """
    一键更新所有数据（带并发去重）。

    并发保护:
    - 60秒内的重复请求直接返回上次结果（防抖）
    - 如果已有更新正在执行，后续请求等待复用结果（去峰）
    - 同一时刻只有一个实际的采集任务在跑

    Args:
        full_market: 是否更新全市场K线(耗时较长~15分钟)
                     False=只更新关注列表(快，<10秒)

    Returns:
        更新结果摘要
    """
    global _last_result, _last_update_time

    # 防抖: 60秒内重复请求直接返回
    if _last_result and (time.time() - _last_update_time) < _DEBOUNCE_SECONDS:
        logger.info("一键更新: 60秒内已更新过，直接返回上次结果")
        return _last_result

    # 去峰: 拿不到锁说明有人在跑，等它跑完复用结果
    acquired = _update_lock.acquire(blocking=True, timeout=120)
    if not acquired:
        logger.warning("一键更新: 等待超时，放弃")
        return _last_result or {"error": "更新超时，请稍后重试"}

    try:
        # 二次检查: 等锁期间可能已经有人完成了
        if _last_result and (time.time() - _last_update_time) < _DEBOUNCE_SECONDS:
            logger.info("一键更新: 等锁期间已由其他请求完成")
            return _last_result

        result = _do_update(full_market)
        _last_result = result
        _last_update_time = time.time()
        return result
    finally:
        _update_lock.release()


def _do_update(full_market: bool = False) -> dict:
    """实际执行更新（无锁保护，由 one_click_update 调用）"""
    t0 = time.time()
    results = {}

    # 1. 更新关注列表
    logger.info("Step 1: 更新关注列表...")
    watchlist_result = update_watchlist()
    results["watchlist"] = watchlist_result

    # 2. 可选: 全市场更新
    if full_market:
        logger.info("Step 2: 全市场K线增量更新...")
        from src.collector.daily_kline_collector import collect_daily_all
        from sqlalchemy import text
        from src.storage.database import SessionLocal

        db = SessionLocal()
        r = db.execute(text("SELECT code FROM stock_basic WHERE is_active = TRUE"))
        codes = [row[0].strip() for row in r]
        db.close()

        if codes:
            market_result = collect_daily_all(codes)
            results["market"] = market_result

    # 3. 刷新 Parquet 缓存
    logger.info("Step 3: 刷新 Parquet 缓存...")
    cache_result = export_to_parquet()
    results["cache"] = cache_result

    elapsed = time.time() - t0
    results["elapsed"] = round(elapsed, 1)
    logger.info(f"一键更新完成: {elapsed:.1f}秒")

    return results
