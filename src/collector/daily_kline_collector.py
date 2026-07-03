"""日K线采集器 — 核心采集任务"""

from datetime import date, datetime, timedelta

import pandas as pd
from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from src.datasource.mootdx_source import fetch_all_daily_bars, fetch_daily_bars
from src.logger import logger
from src.storage.database import SessionLocal
from src.storage.models import StockDaily


def _parse_kline_df(code: str, df: pd.DataFrame) -> list[dict]:
    """将 mootdx 返回的 DataFrame 转为数据库记录列表"""
    records = []
    for _, row in df.iterrows():
        dt_val = row.get("datetime")
        if dt_val is None:
            continue

        # mootdx datetime 格式: "2026-06-25" 或 Timestamp
        if isinstance(dt_val, str):
            trade_date = datetime.strptime(dt_val[:10], "%Y-%m-%d").date()
        elif hasattr(dt_val, "date"):
            trade_date = dt_val.date() if callable(getattr(dt_val, "date")) else dt_val
        else:
            continue

        records.append(
            {
                "code": code,
                "trade_date": trade_date,
                "open": float(row.get("open", 0)),
                "high": float(row.get("high", 0)),
                "low": float(row.get("low", 0)),
                "close": float(row.get("close", 0)),
                "volume": int(row.get("vol", 0) or 0),
                "amount": float(row.get("amount", 0) or 0),
            }
        )
    return records


def _batch_upsert(records: list[dict]) -> int:
    """批量 UPSERT 日K线数据"""
    if not records:
        return 0

    db = SessionLocal()
    try:
        for rec in records:
            stmt = insert(StockDaily).values(**rec).on_conflict_do_update(
                index_elements=["code", "trade_date"],
                set_={
                    "open": rec["open"],
                    "high": rec["high"],
                    "low": rec["low"],
                    "close": rec["close"],
                    "volume": rec["volume"],
                    "amount": rec["amount"],
                },
            )
            db.execute(stmt)
        db.commit()
        return len(records)
    except Exception as e:
        db.rollback()
        logger.error(f"日K线写入失败: {e}")
        raise
    finally:
        db.close()


def collect_full_history(code: str) -> int:
    """
    采集单只股票全部历史日K线。
    用于首次建库回填。

    Args:
        code: 6位股票代码

    Returns:
        写入的记录数
    """
    logger.info(f"[{code}] 开始采集全历史日K线...")
    df = fetch_all_daily_bars(code)
    if df.empty:
        logger.warning(f"[{code}] 无K线数据")
        return 0

    records = _parse_kline_df(code, df)
    count = _batch_upsert(records)
    logger.info(f"[{code}] 写入 {count} 条日K线")
    return count


def collect_latest(code: str, days: int = 10) -> int:
    """
    采集单只股票最近N天日K线。
    用于每日增量更新。

    Args:
        code: 6位股票代码
        days: 拉取最近多少天（冗余拉取，靠UPSERT去重）

    Returns:
        写入的记录数
    """
    df = fetch_daily_bars(code, count=days, start=0)
    if df is None or df.empty:
        return 0

    records = _parse_kline_df(code, df)
    return _batch_upsert(records)


def collect_daily_all(stock_codes: list[str]) -> dict:
    """
    全市场每日增量采集（盘后调用）。

    Args:
        stock_codes: 股票代码列表

    Returns:
        {total: 总写入数, success: 成功只数, failed: 失败只数}
    """
    total = 0
    success = 0
    failed = 0

    logger.info(f"开始每日K线采集，共 {len(stock_codes)} 只...")

    for i, code in enumerate(stock_codes):
        try:
            count = collect_latest(code, days=5)
            total += count
            success += 1
        except Exception as e:
            logger.warning(f"[{code}] 采集失败: {e}")
            failed += 1

        # 每100只打印进度
        if (i + 1) % 100 == 0:
            logger.info(
                f"进度: {i + 1}/{len(stock_codes)} "
                f"(成功{success} 失败{failed})"
            )

    logger.info(
        f"每日K线采集完成: 共{total}条 "
        f"成功{success}只 失败{failed}只"
    )
    return {"total": total, "success": success, "failed": failed}


def backfill_all(stock_codes: list[str], skip_existing: bool = True) -> dict:
    """
    全市场历史K线回填。

    Args:
        stock_codes: 股票代码列表
        skip_existing: 是否跳过已有数据的股票

    Returns:
        统计信息
    """
    total = 0
    success = 0
    failed = 0
    skipped = 0

    logger.info(f"开始历史K线回填，共 {len(stock_codes)} 只...")

    db = SessionLocal()
    try:
        for i, code in enumerate(stock_codes):
            # 检查是否已有数据
            if skip_existing:
                result = db.execute(
                    text(
                        "SELECT COUNT(*) FROM stock_daily WHERE code = :code"
                    ),
                    {"code": code},
                )
                count = result.scalar()
                if count and count > 100:
                    skipped += 1
                    continue

            try:
                n = collect_full_history(code)
                total += n
                success += 1
            except Exception as e:
                logger.warning(f"[{code}] 回填失败: {e}")
                failed += 1

            if (i + 1) % 50 == 0:
                logger.info(
                    f"回填进度: {i + 1}/{len(stock_codes)} "
                    f"(成功{success} 跳过{skipped} 失败{failed})"
                )
    finally:
        db.close()

    logger.info(
        f"历史K线回填完成: 共{total}条 "
        f"成功{success}只 跳过{skipped}只 失败{failed}只"
    )
    return {
        "total": total,
        "success": success,
        "skipped": skipped,
        "failed": failed,
    }
