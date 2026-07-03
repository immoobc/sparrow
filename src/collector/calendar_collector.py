"""交易日历采集/生成器

策略：通过上证指数的日K线数据反推交易日历。
上证指数1990-12-19开市至今，有K线的日期就是交易日。
"""

from datetime import date, timedelta

from sqlalchemy import text

from src.logger import logger
from src.storage.database import SessionLocal


def generate_trade_calendar() -> int:
    """
    通过上证指数K线反推交易日历并写入 trade_calendar 表。

    Returns:
        写入条数
    """
    logger.info("生成交易日历（基于上证指数K线）...")

    from src.datasource.mootdx_source import get_client, _get_market

    client = get_client()
    all_dates = set()
    start = 0
    batch_size = 800

    # 上证指数: market=1, code='999999'（通达信内部指数代码）
    while True:
        raw_data = client.client.get_security_bars(4, 1, '999999', start, batch_size)
        if not raw_data:
            break

        batch_dates = []
        for item in raw_data:
            dt = item.get("datetime", "")
            year = item.get("year", 0)
            if year < 1990 or year > 2030:
                continue
            if dt and len(dt) >= 10:
                batch_dates.append(dt[:10])

        if not batch_dates:
            break

        all_dates.update(batch_dates)
        if len(raw_data) < batch_size:
            break
        start += batch_size

    if not all_dates:
        # 如果上证指数拉不到，用茅台兜底推断交易日
        logger.warning("上证指数K线异常，使用茅台600519推断交易日...")
        from src.datasource.mootdx_source import fetch_all_daily_bars
        df = fetch_all_daily_bars("600519")
        if df.empty:
            logger.error("无法获取任何K线数据")
            return 0
        all_dates = set(df["datetime"].tolist())

    # 严格过滤：只保留合法日期格式 YYYY-MM-DD
    valid_dates = set()
    for d in all_dates:
        try:
            parsed = date.fromisoformat(d)
            if 1990 <= parsed.year <= 2030:
                valid_dates.add(d)
        except (ValueError, TypeError):
            continue

    all_dates = valid_dates
    logger.info(f"从K线提取到 {len(all_dates)} 个交易日")

    # 确定日历范围
    sorted_dates = sorted(all_dates)
    start_date = date.fromisoformat(sorted_dates[0])
    end_date = date.fromisoformat(sorted_dates[-1])

    # 生成完整日历范围
    db = SessionLocal()
    count = 0
    trade_dates_set = set(sorted_dates)

    try:
        current = start_date
        all_trade_days = sorted(trade_dates_set)

        while current <= end_date:
            is_open = current.isoformat() in trade_dates_set
            current_str = current.isoformat()

            prev_trade = None
            next_trade = None

            if is_open:
                try:
                    idx = all_trade_days.index(current_str)
                    if idx > 0:
                        prev_trade = date.fromisoformat(all_trade_days[idx - 1])
                    if idx < len(all_trade_days) - 1:
                        next_trade = date.fromisoformat(all_trade_days[idx + 1])
                except ValueError:
                    pass
            else:
                for td in reversed(all_trade_days):
                    if td < current_str:
                        prev_trade = date.fromisoformat(td)
                        break
                for td in all_trade_days:
                    if td > current_str:
                        next_trade = date.fromisoformat(td)
                        break

            stmt = text("""
                INSERT INTO trade_calendar (cal_date, is_open, prev_trade, next_trade)
                VALUES (:cal_date, :is_open, :prev_trade, :next_trade)
                ON CONFLICT (cal_date) DO UPDATE SET
                    is_open = EXCLUDED.is_open,
                    prev_trade = EXCLUDED.prev_trade,
                    next_trade = EXCLUDED.next_trade
            """)
            db.execute(stmt, {
                "cal_date": current,
                "is_open": is_open,
                "prev_trade": prev_trade,
                "next_trade": next_trade,
            })
            count += 1
            current += timedelta(days=1)

        db.commit()
        logger.info(f"交易日历写入: {count} 天 ({start_date} ~ {end_date})")
    except Exception as e:
        db.rollback()
        logger.error(f"交易日历写入失败: {e}")
        raise
    finally:
        db.close()

    return count


def is_trade_day(check_date: date = None) -> bool:
    """检查某天是否为交易日"""
    if check_date is None:
        check_date = date.today()

    db = SessionLocal()
    try:
        result = db.execute(
            text("SELECT is_open FROM trade_calendar WHERE cal_date = :d"),
            {"d": check_date},
        )
        row = result.fetchone()
        return bool(row and row[0])
    finally:
        db.close()


def get_last_trade_date() -> date | None:
    """获取最近一个交易日"""
    db = SessionLocal()
    try:
        result = db.execute(
            text("""
                SELECT cal_date FROM trade_calendar
                WHERE is_open = TRUE AND cal_date <= CURRENT_DATE
                ORDER BY cal_date DESC LIMIT 1
            """)
        )
        row = result.fetchone()
        return row[0] if row else None
    finally:
        db.close()
