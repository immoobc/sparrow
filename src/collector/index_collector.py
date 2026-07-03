"""指数日K线采集器 — 采集常见A股指数历史和每日增量

覆盖指数:
- 000001 上证指数
- 000300 沪深300
- 000905 中证500
- 000852 中证1000
- 399001 深证成指
- 399006 创业板指
- 399303 国证2000 (小盘代表)

数据源: mootdx (TCP 7709, 不封IP)
存储: index_daily 表 (PostgreSQL, 已有分区)
"""

from datetime import date, datetime

import pandas as pd
from sqlalchemy import text

from src.datasource.mootdx_source import get_client
from src.logger import logger
from src.storage.database import SessionLocal


# 常见指数列表: (代码, 通达信市场, 名称)
INDEX_LIST = [
    ("000001", 1, "上证指数"),
    ("000300", 1, "沪深300"),
    ("000905", 1, "中证500"),
    ("000852", 1, "中证1000"),
    ("399001", 0, "深证成指"),
    ("399006", 0, "创业板指"),
    ("399303", 0, "国证2000"),
]


def fetch_index_bars(code: str, market: int, count: int = 800, start: int = 0) -> list[dict]:
    """
    从通达信拉取指数日K线。

    Args:
        code: 指数代码
        market: 0=深圳, 1=上海
        count: 一次拉取条数(最大800)
        start: 起始偏移

    Returns:
        记录列表 [{code, trade_date, open, high, low, close, volume, amount, change_pct}]
    """
    client = get_client()
    raw = client.client.get_index_bars(4, market, code, start, count)
    if not raw:
        return []

    records = []
    prev_close = None
    for item in raw:
        dt = item.get("datetime", "")
        if not dt or len(dt) < 10:
            continue
        year = item.get("year", 0)
        if year < 1990 or year > 2030:
            continue

        close = float(item.get("close", 0))
        change_pct = 0
        if prev_close and prev_close > 0:
            change_pct = round((close / prev_close - 1) * 100, 4)
        prev_close = close

        records.append({
            "code": code,
            "trade_date": datetime.strptime(dt[:10], "%Y-%m-%d").date(),
            "open": float(item.get("open", 0)),
            "high": float(item.get("high", 0)),
            "low": float(item.get("low", 0)),
            "close": close,
            "volume": int(item.get("vol", 0) or 0),
            "amount": float(item.get("amount", 0) or 0),
            "change_pct": change_pct,
        })

    return records


def fetch_all_index_bars(code: str, market: int) -> list[dict]:
    """拉取指数全部历史K线(分页)"""
    all_records = []
    start = 0
    batch_size = 800

    while True:
        records = fetch_index_bars(code, market, count=batch_size, start=start)
        if not records:
            break
        all_records.extend(records)
        if len(records) < batch_size:
            break
        start += batch_size

    # change_pct 需要重算(分页边界问题)
    all_records.sort(key=lambda x: x["trade_date"])
    for i in range(1, len(all_records)):
        prev_close = all_records[i - 1]["close"]
        if prev_close > 0:
            all_records[i]["change_pct"] = round(
                (all_records[i]["close"] / prev_close - 1) * 100, 4
            )

    return all_records


def _upsert_index_records(records: list[dict]) -> int:
    """批量 UPSERT 指数K线到 index_daily 表"""
    if not records:
        return 0

    db = SessionLocal()
    try:
        for rec in records:
            db.execute(text("""
                INSERT INTO index_daily (code, trade_date, open, high, low, close, volume, amount, change_pct)
                VALUES (:code, :trade_date, :open, :high, :low, :close, :volume, :amount, :change_pct)
                ON CONFLICT (code, trade_date) DO UPDATE SET
                    open = EXCLUDED.open, high = EXCLUDED.high,
                    low = EXCLUDED.low, close = EXCLUDED.close,
                    volume = EXCLUDED.volume, amount = EXCLUDED.amount,
                    change_pct = EXCLUDED.change_pct
            """), rec)
        db.commit()
        return len(records)
    except Exception as e:
        db.rollback()
        logger.error(f"指数K线写入失败: {e}")
        raise
    finally:
        db.close()


def collect_index_daily() -> dict:
    """
    每日增量采集所有常见指数(最近10天)。
    用于盘后定时任务。

    Returns:
        {total: 总写入数, indices: 采集的指数数}
    """
    total = 0
    success = 0

    logger.info(f"开始指数K线采集，共 {len(INDEX_LIST)} 只...")

    for code, market, name in INDEX_LIST:
        try:
            records = fetch_index_bars(code, market, count=10, start=0)
            count = _upsert_index_records(records)
            total += count
            success += 1
            logger.info(f"  [{code}] {name}: {count}条")
        except Exception as e:
            logger.warning(f"  [{code}] {name} 采集失败: {e}")

    logger.info(f"指数K线采集完成: {total}条, {success}/{len(INDEX_LIST)}只成功")
    return {"total": total, "indices": success}


def backfill_index_all() -> dict:
    """
    回填所有指数的全部历史K线。
    首次运行或重建时使用。

    Returns:
        {total: 总写入数, indices: 成功数}
    """
    total = 0
    success = 0

    logger.info(f"开始指数历史回填，共 {len(INDEX_LIST)} 只...")

    for code, market, name in INDEX_LIST:
        try:
            records = fetch_all_index_bars(code, market)
            count = _upsert_index_records(records)
            total += count
            success += 1
            logger.info(f"  [{code}] {name}: {count}条 ({records[0]['trade_date']} ~ {records[-1]['trade_date']})" if records else f"  [{code}] {name}: 无数据")
        except Exception as e:
            logger.warning(f"  [{code}] {name} 回填失败: {e}")

    logger.info(f"指数历史回填完成: {total}条, {success}/{len(INDEX_LIST)}只成功")
    return {"total": total, "indices": success}


def load_index_daily(
    code: str = "000300",
    start_date: str = None,
    end_date: str = None,
) -> pd.DataFrame:
    """
    从数据库加载指数日K线。

    Args:
        code: 指数代码 (如 "000300")
        start_date: 起始日期
        end_date: 结束日期

    Returns:
        DataFrame[trade_date, open, high, low, close, volume, amount, change_pct]
    """
    db = SessionLocal()
    try:
        sql = "SELECT trade_date, open, high, low, close, volume, amount, change_pct FROM index_daily WHERE code = :code"
        params = {"code": code}
        if start_date:
            sql += " AND trade_date >= :start"
            params["start"] = start_date
        if end_date:
            sql += " AND trade_date <= :end"
            params["end"] = end_date
        sql += " ORDER BY trade_date"

        result = db.execute(text(sql), params)
        rows = result.fetchall()
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume", "amount", "change_pct"])
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        for col in ["open", "high", "low", "close", "amount", "change_pct"]:
            df[col] = df[col].astype(float)
        return df
    finally:
        db.close()


def load_all_index_nav(start_date: str = None, end_date: str = None) -> dict:
    """
    加载所有指数的净值序列（用于对比图表）。

    Returns:
        {
            "000300": {"name": "沪深300", "dates": [...], "nav": [...], "annual_return": float},
            ...
        }
    """
    result = {}
    for code, market, name in INDEX_LIST:
        df = load_index_daily(code, start_date, end_date)
        if df.empty or len(df) < 10:
            continue

        # 计算净值
        df["nav"] = df["close"] / df["close"].iloc[0]
        n_years = (df["trade_date"].iloc[-1] - df["trade_date"].iloc[0]).days / 365.25
        annual_ret = (df["nav"].iloc[-1] ** (1 / n_years) - 1) * 100 if n_years > 0 else 0

        result[code] = {
            "name": name,
            "dates": df["trade_date"].tolist(),
            "nav": df["nav"].tolist(),
            "annual_return": round(annual_ret, 1),
            "final_nav": round(float(df["nav"].iloc[-1]), 4),
        }

    return result
