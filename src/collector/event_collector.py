"""事件采集器 — 解禁/分红/大宗交易/股东户数"""

from datetime import date, timedelta

from sqlalchemy import text

from src.datasource.eastmoney_source import eastmoney_datacenter
from src.logger import logger
from src.storage.database import SessionLocal


def collect_lockup_schedule() -> int:
    """
    采集限售解禁日历（全市场历史+未来）。

    Returns:
        写入条数
    """
    logger.info("采集限售解禁日历...")

    # 拉取未来90天 + 近半年历史
    today = date.today()
    start = (today - timedelta(days=180)).strftime("%Y-%m-%d")
    end = (today + timedelta(days=90)).strftime("%Y-%m-%d")

    data = eastmoney_datacenter(
        "RPT_LIFT_STAGE",
        filter_str=f"(FREE_DATE>='{start}')(FREE_DATE<='{end}')",
        page_size=500,
        sort_columns="FREE_DATE",
        sort_types="-1",
    )

    if not data:
        logger.info("无解禁数据")
        return 0

    db = SessionLocal()
    count = 0
    try:
        for row in data:
            code = row.get("SECURITY_CODE", "")
            free_date = str(row.get("FREE_DATE", ""))[:10]
            if not code or not free_date or free_date == "None":
                continue

            stmt = text("""
                INSERT INTO lockup_schedule
                    (code, free_date, stock_type, free_shares, free_ratio)
                VALUES
                    (:code, :free_date, :stock_type, :free_shares, :free_ratio)
                ON CONFLICT (code, free_date, stock_type) DO UPDATE SET
                    free_shares = EXCLUDED.free_shares,
                    free_ratio = EXCLUDED.free_ratio
            """)
            db.execute(stmt, {
                "code": code,
                "free_date": free_date,
                "stock_type": row.get("LIMITED_STOCK_TYPE", ""),
                "free_shares": row.get("FREE_SHARES_NUM", 0),
                "free_ratio": row.get("FREE_RATIO", 0),
            })
            count += 1

        db.commit()
        logger.info(f"解禁日历写入: {count} 条")
    except Exception as e:
        db.rollback()
        logger.error(f"解禁日历写入失败: {e}")
    finally:
        db.close()

    return count


def collect_dividend_history(code: str, page_size: int = 20) -> int:
    """采集单只股票分红历史"""
    data = eastmoney_datacenter(
        "RPT_SHAREBONUS_DET",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=page_size,
        sort_columns="EX_DIVIDEND_DATE",
        sort_types="-1",
    )

    if not data:
        return 0

    db = SessionLocal()
    count = 0
    try:
        for row in data:
            ex_date = str(row.get("EX_DIVIDEND_DATE", ""))[:10]
            if not ex_date or ex_date == "None":
                ex_date = None

            stmt = text("""
                INSERT INTO dividend_history
                    (code, ex_date, record_date, report_year, bonus_rmb,
                     transfer_ratio, bonus_ratio, progress)
                VALUES
                    (:code, :ex_date, :record_date, :report_year, :bonus_rmb,
                     :transfer_ratio, :bonus_ratio, :progress)
                ON CONFLICT (code, ex_date, report_year) DO UPDATE SET
                    bonus_rmb = EXCLUDED.bonus_rmb,
                    transfer_ratio = EXCLUDED.transfer_ratio,
                    bonus_ratio = EXCLUDED.bonus_ratio,
                    progress = EXCLUDED.progress
            """)
            record_date = str(row.get("EQUITY_RECORD_DATE", ""))[:10]
            db.execute(stmt, {
                "code": code,
                "ex_date": ex_date,
                "record_date": record_date if record_date != "None" else None,
                "report_year": row.get("REPORT_DATE", ""),
                "bonus_rmb": row.get("PRETAX_BONUS_RMB", 0),
                "transfer_ratio": row.get("TRANSFER_RATIO", 0),
                "bonus_ratio": row.get("BONUS_RATIO", 0),
                "progress": row.get("ASSIGN_PROGRESS", ""),
            })
            count += 1

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[{code}] 分红历史写入失败: {e}")
        return 0
    finally:
        db.close()

    return count


def collect_block_trade(trade_date: date = None, page_size: int = 200) -> int:
    """
    采集大宗交易（按日期）。

    Args:
        trade_date: 交易日期
        page_size: 单次拉取条数

    Returns:
        写入条数
    """
    if trade_date is None:
        trade_date = date.today()

    date_str = trade_date.strftime("%Y-%m-%d")
    logger.info(f"采集大宗交易 [{date_str}]...")

    data = eastmoney_datacenter(
        "RPT_DATA_BLOCKTRADE",
        filter_str=f"(TRADE_DATE='{date_str}')",
        page_size=page_size,
        sort_columns="DEAL_AMT",
        sort_types="-1",
    )

    if not data:
        logger.info(f"[{date_str}] 无大宗交易数据")
        return 0

    db = SessionLocal()
    count = 0
    try:
        for row in data:
            code = row.get("SECURITY_CODE", "")
            if not code:
                continue

            close_price = row.get("CLOSE_PRICE") or 0
            deal_price = row.get("DEAL_PRICE") or 0
            premium = ((deal_price / close_price - 1) * 100) if close_price > 0 else 0

            stmt = text("""
                INSERT INTO block_trade
                    (code, trade_date, deal_price, close_price, premium_pct,
                     deal_volume, deal_amount, buyer, seller)
                VALUES
                    (:code, :trade_date, :deal_price, :close_price, :premium_pct,
                     :deal_volume, :deal_amount, :buyer, :seller)
            """)
            db.execute(stmt, {
                "code": code,
                "trade_date": date_str,
                "deal_price": deal_price,
                "close_price": close_price,
                "premium_pct": round(premium, 4),
                "deal_volume": row.get("DEAL_VOLUME", 0),
                "deal_amount": row.get("DEAL_AMT", 0),
                "buyer": row.get("BUYER_NAME", ""),
                "seller": row.get("SELLER_NAME", ""),
            })
            count += 1

        db.commit()
        logger.info(f"大宗交易写入: {count} 条")
    except Exception as e:
        db.rollback()
        logger.error(f"大宗交易写入失败: {e}")
    finally:
        db.close()

    return count


def collect_holder_num(code: str) -> int:
    """采集单只股票股东户数变化"""
    data = eastmoney_datacenter(
        "RPT_HOLDERNUMLATEST",
        filter_str=f'(SECURITY_CODE="{code}")',
        page_size=10,
        sort_columns="END_DATE",
        sort_types="-1",
    )

    if not data:
        return 0

    db = SessionLocal()
    count = 0
    try:
        for row in data:
            end_date = str(row.get("END_DATE", ""))[:10]
            if not end_date or end_date == "None":
                continue

            stmt = text("""
                INSERT INTO holder_num
                    (code, end_date, holder_num, change_num, change_ratio, avg_shares)
                VALUES
                    (:code, :end_date, :holder_num, :change_num, :change_ratio, :avg_shares)
                ON CONFLICT (code, end_date) DO UPDATE SET
                    holder_num = EXCLUDED.holder_num,
                    change_num = EXCLUDED.change_num,
                    change_ratio = EXCLUDED.change_ratio,
                    avg_shares = EXCLUDED.avg_shares
            """)
            db.execute(stmt, {
                "code": code,
                "end_date": end_date,
                "holder_num": row.get("HOLDER_NUM", 0),
                "change_num": row.get("HOLDER_NUM_CHANGE", 0),
                "change_ratio": row.get("HOLDER_NUM_RATIO", 0),
                "avg_shares": row.get("AVG_FREE_SHARES", 0),
            })
            count += 1

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[{code}] 股东户数写入失败: {e}")
        return 0
    finally:
        db.close()

    return count
