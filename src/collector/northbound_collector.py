"""北向资金采集器"""

from datetime import date

from sqlalchemy import text

from src.datasource.ths_source import fetch_northbound_close, fetch_northbound_realtime
from src.logger import logger
from src.storage.database import SessionLocal


def collect_northbound_daily(trade_date: date = None) -> bool:
    """
    采集当日北向资金收盘数据并写入 northbound_flow 表。

    Args:
        trade_date: 交易日期（默认今天）

    Returns:
        是否成功
    """
    if trade_date is None:
        trade_date = date.today()

    logger.info(f"采集北向资金 [{trade_date}]...")

    data = fetch_northbound_close()
    if not data:
        logger.warning("北向资金数据为空（可能非交易时段）")
        return False

    hgt = data.get("hgt", 0)
    sgt = data.get("sgt", 0)
    total = hgt + sgt

    db = SessionLocal()
    try:
        stmt = text("""
            INSERT INTO northbound_flow (trade_date, hgt_net, sgt_net, total_net)
            VALUES (:trade_date, :hgt, :sgt, :total)
            ON CONFLICT (trade_date)
            DO UPDATE SET
                hgt_net = EXCLUDED.hgt_net,
                sgt_net = EXCLUDED.sgt_net,
                total_net = EXCLUDED.total_net
        """)
        db.execute(stmt, {
            "trade_date": trade_date,
            "hgt": hgt,
            "sgt": sgt,
            "total": total,
        })
        db.commit()
        logger.info(f"北向资金写入: 沪股通={hgt:.2f}亿 深股通={sgt:.2f}亿 合计={total:.2f}亿")
        return True
    except Exception as e:
        db.rollback()
        logger.error(f"北向资金写入失败: {e}")
        return False
    finally:
        db.close()


def collect_northbound_minute(trade_date: date = None) -> int:
    """
    采集北向资金分钟级流向并写入 northbound_minute 表。
    需要先建表（在后续Phase补充）。

    Returns:
        写入条数
    """
    if trade_date is None:
        trade_date = date.today()

    df = fetch_northbound_realtime()
    if df.empty:
        return 0

    db = SessionLocal()
    count = 0
    try:
        for _, row in df.iterrows():
            time_str = row.get("time")
            if not time_str:
                continue
            hgt = row.get("hgt_yi")
            sgt = row.get("sgt_yi")
            if hgt is None and sgt is None:
                continue

            stmt = text("""
                INSERT INTO northbound_minute (trade_date, time, hgt_cumul, sgt_cumul)
                VALUES (:trade_date, :time, :hgt, :sgt)
                ON CONFLICT DO NOTHING
            """)
            try:
                db.execute(stmt, {
                    "trade_date": trade_date,
                    "time": time_str,
                    "hgt": float(hgt) if hgt is not None else None,
                    "sgt": float(sgt) if sgt is not None else None,
                })
                count += 1
            except Exception:
                pass

        db.commit()
        logger.info(f"北向分钟流向写入: {count} 条")
    except Exception as e:
        db.rollback()
        logger.error(f"北向分钟流向写入失败: {e}")
    finally:
        db.close()

    return count
