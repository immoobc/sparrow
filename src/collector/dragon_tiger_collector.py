"""龙虎榜采集器（数据源：东财datacenter）"""

from datetime import date, datetime

from sqlalchemy import text

from src.datasource.eastmoney_source import eastmoney_datacenter
from src.logger import logger
from src.storage.database import SessionLocal


def collect_daily_dragon_tiger(trade_date: date = None) -> dict:
    """
    采集全市场龙虎榜（当日所有上榜记录 + 买卖席位）。

    Args:
        trade_date: 交易日期（默认今天）

    Returns:
        {records: 上榜记录数, seats: 席位明细数}
    """
    if trade_date is None:
        trade_date = date.today()

    date_str = trade_date.strftime("%Y-%m-%d")
    logger.info(f"采集龙虎榜 [{date_str}]...")

    # 1. 拉取全市场上榜记录
    data = eastmoney_datacenter(
        "RPT_DAILYBILLBOARD_DETAILSNEW",
        filter_str=f"(TRADE_DATE>='{date_str}')(TRADE_DATE<='{date_str}')",
        page_size=500,
        sort_columns="BILLBOARD_NET_AMT",
        sort_types="-1",
    )

    if not data:
        logger.info(f"[{date_str}] 无龙虎榜数据（非交易日或未更新）")
        return {"records": 0, "seats": 0}

    db = SessionLocal()
    record_count = 0
    seat_count = 0

    try:
        # 写入上榜记录
        for row in data:
            code = row.get("SECURITY_CODE", "")
            if not code:
                continue

            stmt = text("""
                INSERT INTO dragon_tiger_record
                    (code, trade_date, reason, net_buy_amt, buy_amt, sell_amt,
                     turnover_pct, close_price, change_pct)
                VALUES
                    (:code, :trade_date, :reason, :net_buy_amt, :buy_amt, :sell_amt,
                     :turnover_pct, :close_price, :change_pct)
                ON CONFLICT (code, trade_date, reason) DO UPDATE SET
                    net_buy_amt = EXCLUDED.net_buy_amt,
                    buy_amt = EXCLUDED.buy_amt,
                    sell_amt = EXCLUDED.sell_amt
            """)
            db.execute(stmt, {
                "code": code,
                "trade_date": date_str,
                "reason": row.get("EXPLANATION", ""),
                "net_buy_amt": row.get("BILLBOARD_NET_AMT", 0),
                "buy_amt": row.get("BILLBOARD_BUY_AMT", 0),
                "sell_amt": row.get("BILLBOARD_SELL_AMT", 0),
                "turnover_pct": float(row.get("TURNOVERRATE", 0) or 0),
                "close_price": row.get("CLOSE_PRICE", 0),
                "change_pct": float(row.get("CHANGE_RATE", 0) or 0),
            })
            record_count += 1

        # 2. 对每只上榜股票拉取买卖席位
        unique_codes = list({row.get("SECURITY_CODE", "") for row in data if row.get("SECURITY_CODE")})

        for code in unique_codes:
            # 买入席位
            buy_data = eastmoney_datacenter(
                "RPT_BILLBOARD_DAILYDETAILSBUY",
                filter_str=f"(TRADE_DATE='{date_str}')(SECURITY_CODE=\"{code}\")",
                page_size=10,
                sort_columns="BUY",
                sort_types="-1",
            )
            for rank, row in enumerate(buy_data[:5], 1):
                stmt = text("""
                    INSERT INTO dragon_tiger_seat
                        (code, trade_date, direction, rank, dept_name, dept_code,
                         buy_amt, sell_amt, net_amt)
                    VALUES
                        (:code, :trade_date, 'BUY', :rank, :dept_name, :dept_code,
                         :buy_amt, :sell_amt, :net_amt)
                """)
                db.execute(stmt, {
                    "code": code,
                    "trade_date": date_str,
                    "rank": rank,
                    "dept_name": row.get("OPERATEDEPT_NAME", ""),
                    "dept_code": str(row.get("OPERATEDEPT_CODE", "")),
                    "buy_amt": row.get("BUY", 0),
                    "sell_amt": row.get("SELL", 0),
                    "net_amt": row.get("NET", 0),
                })
                seat_count += 1

            # 卖出席位
            sell_data = eastmoney_datacenter(
                "RPT_BILLBOARD_DAILYDETAILSSELL",
                filter_str=f"(TRADE_DATE='{date_str}')(SECURITY_CODE=\"{code}\")",
                page_size=10,
                sort_columns="SELL",
                sort_types="-1",
            )
            for rank, row in enumerate(sell_data[:5], 1):
                stmt = text("""
                    INSERT INTO dragon_tiger_seat
                        (code, trade_date, direction, rank, dept_name, dept_code,
                         buy_amt, sell_amt, net_amt)
                    VALUES
                        (:code, :trade_date, 'SELL', :rank, :dept_name, :dept_code,
                         :buy_amt, :sell_amt, :net_amt)
                """)
                db.execute(stmt, {
                    "code": code,
                    "trade_date": date_str,
                    "rank": rank,
                    "dept_name": row.get("OPERATEDEPT_NAME", ""),
                    "dept_code": str(row.get("OPERATEDEPT_CODE", "")),
                    "buy_amt": row.get("BUY", 0),
                    "sell_amt": row.get("SELL", 0),
                    "net_amt": row.get("NET", 0),
                })
                seat_count += 1

        db.commit()
        logger.info(f"龙虎榜写入完成: {record_count}条上榜 {seat_count}条席位")

    except Exception as e:
        db.rollback()
        logger.error(f"龙虎榜写入失败: {e}")
        raise
    finally:
        db.close()

    return {"records": record_count, "seats": seat_count}
