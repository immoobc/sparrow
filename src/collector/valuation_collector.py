"""估值数据采集器 — 每日PE/PB/市值（数据源：腾讯财经，不封IP）"""

from datetime import date, datetime

from sqlalchemy.dialects.postgresql import insert

from src.datasource.tencent_source import fetch_batch_quotes
from src.logger import logger
from src.storage.database import SessionLocal


def collect_valuation_daily(stock_codes: list[str], trade_date: date = None) -> dict:
    """
    采集全市场每日估值数据。

    Args:
        stock_codes: 股票代码列表
        trade_date: 交易日期（默认今天）

    Returns:
        {total: 写入数, success: 成功数, failed: 失败数}
    """
    if trade_date is None:
        trade_date = date.today()

    logger.info(f"开始采集估值数据 [{trade_date}]，共 {len(stock_codes)} 只...")

    # 腾讯财经分批拉取（不封IP，可以大批量）
    all_quotes = fetch_batch_quotes(stock_codes, batch_size=80)
    logger.info(f"腾讯财经返回 {len(all_quotes)} 只行情")

    if not all_quotes:
        logger.warning("估值数据为空")
        return {"total": 0, "success": 0, "failed": 0}

    # 批量写入
    db = SessionLocal()
    count = 0
    failed = 0

    try:
        for code, q in all_quotes.items():
            # 过滤无效数据（停牌/未开盘时price=0）
            if q.get("price", 0) <= 0:
                continue

            try:
                from sqlalchemy import text

                stmt = text("""
                    INSERT INTO valuation_daily
                        (code, trade_date, pe_ttm, pe_static, pb, mcap, float_mcap, turnover)
                    VALUES
                        (:code, :trade_date, :pe_ttm, :pe_static, :pb, :mcap, :float_mcap, :turnover)
                    ON CONFLICT (code, trade_date)
                    DO UPDATE SET
                        pe_ttm = EXCLUDED.pe_ttm,
                        pe_static = EXCLUDED.pe_static,
                        pb = EXCLUDED.pb,
                        mcap = EXCLUDED.mcap,
                        float_mcap = EXCLUDED.float_mcap,
                        turnover = EXCLUDED.turnover
                """)
                db.execute(stmt, {
                    "code": code,
                    "trade_date": trade_date,
                    "pe_ttm": q.get("pe_ttm") or None,
                    "pe_static": q.get("pe_static") or None,
                    "pb": q.get("pb") or None,
                    "mcap": q.get("mcap_yi", 0) * 1e8 if q.get("mcap_yi") else None,
                    "float_mcap": q.get("float_mcap_yi", 0) * 1e8 if q.get("float_mcap_yi") else None,
                    "turnover": q.get("turnover_pct") or None,
                })
                count += 1
            except Exception as e:
                failed += 1
                if failed <= 5:
                    logger.warning(f"[{code}] 估值写入失败: {e}")

        db.commit()
        logger.info(f"估值采集完成: {count} 条写入, {failed} 条失败")
    except Exception as e:
        db.rollback()
        logger.error(f"估值批量写入异常: {e}")
        raise
    finally:
        db.close()

    return {"total": count, "success": count, "failed": failed}
