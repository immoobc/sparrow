"""强势股/热点归因采集器（数据源：同花顺，不封IP）"""

from datetime import date

from sqlalchemy import text

from src.datasource.ths_source import fetch_hot_stocks
from src.logger import logger
from src.storage.database import SessionLocal


def collect_hot_stocks(trade_date: date = None) -> int:
    """
    采集当日强势股及题材归因标签。

    Args:
        trade_date: 交易日期

    Returns:
        写入条数
    """
    if trade_date is None:
        trade_date = date.today()

    date_str = trade_date.strftime("%Y-%m-%d")
    logger.info(f"采集强势股归因 [{date_str}]...")

    df = fetch_hot_stocks(date_str)
    if df.empty:
        logger.info(f"[{date_str}] 无强势股数据")
        return 0

    db = SessionLocal()
    count = 0
    try:
        for _, row in df.iterrows():
            code = str(row.get("code", ""))
            if not code or len(code) != 6:
                continue

            stmt = text("""
                INSERT INTO hot_stocks
                    (code, trade_date, name, reason_tags, change_pct, turnover_pct, amount, dde_net)
                VALUES
                    (:code, :trade_date, :name, :reason_tags, :change_pct,
                     :turnover_pct, :amount, :dde_net)
                ON CONFLICT (code, trade_date) DO UPDATE SET
                    reason_tags = EXCLUDED.reason_tags,
                    change_pct = EXCLUDED.change_pct,
                    turnover_pct = EXCLUDED.turnover_pct,
                    amount = EXCLUDED.amount,
                    dde_net = EXCLUDED.dde_net
            """)
            db.execute(stmt, {
                "code": code,
                "trade_date": date_str,
                "name": str(row.get("name", ""))[:20],
                "reason_tags": str(row.get("reason", ""))[:200],
                "change_pct": float(row.get("zhangfu", 0) or 0),
                "turnover_pct": float(row.get("huanshou", 0) or 0),
                "amount": float(row.get("chengjiaoe", 0) or 0),
                "dde_net": float(row.get("ddejingliang", 0) or 0),
            })
            count += 1

        db.commit()
        logger.info(f"强势股归因写入: {count} 条")
    except Exception as e:
        db.rollback()
        logger.error(f"强势股归因写入失败: {e}")
    finally:
        db.close()

    return count
