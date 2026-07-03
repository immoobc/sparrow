"""融资融券采集器（数据源：东财datacenter）"""

from src.datasource.eastmoney_source import eastmoney_datacenter
from src.logger import logger
from src.storage.database import SessionLocal
from sqlalchemy import text


def collect_margin_single(code: str, page_size: int = 30) -> int:
    """
    采集单只股票的融资融券数据。

    Returns:
        写入条数
    """
    data = eastmoney_datacenter(
        "RPTA_WEB_RZRQ_GGMX",
        filter_str=f'(SCODE="{code}")',
        page_size=page_size,
        sort_columns="DATE",
        sort_types="-1",
    )

    if not data:
        return 0

    db = SessionLocal()
    count = 0
    try:
        for row in data:
            trade_date = str(row.get("DATE", ""))[:10]
            if not trade_date or trade_date == "None":
                continue

            stmt = text("""
                INSERT INTO margin_trading
                    (code, trade_date, rzye, rzmre, rzche, rqye, rqmcl, rqchl, rzrqye)
                VALUES
                    (:code, :trade_date, :rzye, :rzmre, :rzche, :rqye, :rqmcl, :rqchl, :rzrqye)
                ON CONFLICT (code, trade_date)
                DO UPDATE SET
                    rzye = EXCLUDED.rzye,
                    rzmre = EXCLUDED.rzmre,
                    rzche = EXCLUDED.rzche,
                    rqye = EXCLUDED.rqye,
                    rqmcl = EXCLUDED.rqmcl,
                    rqchl = EXCLUDED.rqchl,
                    rzrqye = EXCLUDED.rzrqye
            """)
            db.execute(stmt, {
                "code": code,
                "trade_date": trade_date,
                "rzye": row.get("RZYE", 0),
                "rzmre": row.get("RZMRE", 0),
                "rzche": row.get("RZCHE", 0),
                "rqye": row.get("RQYE", 0),
                "rqmcl": row.get("RQMCL", 0),
                "rqchl": row.get("RQCHL", 0),
                "rzrqye": row.get("RZRQYE", 0),
            })
            count += 1

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[{code}] 融资融券写入失败: {e}")
        return 0
    finally:
        db.close()

    return count


def collect_margin_batch(stock_codes: list[str]) -> dict:
    """
    批量采集融资融券数据。

    Args:
        stock_codes: 融资融券标的列表

    Returns:
        统计信息
    """
    total = 0
    success = 0
    failed = 0

    logger.info(f"开始批量采集融资融券，共 {len(stock_codes)} 只...")

    for i, code in enumerate(stock_codes):
        try:
            n = collect_margin_single(code)
            if n > 0:
                total += n
                success += 1
        except Exception as e:
            logger.warning(f"[{code}] 融资融券采集异常: {e}")
            failed += 1

        if (i + 1) % 100 == 0:
            logger.info(f"融资融券进度: {i + 1}/{len(stock_codes)}")

    logger.info(f"融资融券采集完成: 成功{success}只 总写入{total}条")
    return {"total": total, "success": success, "failed": failed}
