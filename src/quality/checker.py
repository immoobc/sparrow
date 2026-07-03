"""数据质量检查器 — 每日自动检测缺失/异常"""

from datetime import date, timedelta

from sqlalchemy import text

from src.logger import logger
from src.storage.database import SessionLocal


def check_daily_completeness(trade_date: date = None) -> dict:
    """
    检查指定交易日的数据完整性。

    检查项:
    1. stock_daily: 是否有数据 + 数量是否合理
    2. valuation_daily: 是否采集了估值
    3. 异常值: 涨跌幅超过合理范围

    Returns:
        {issues: [{table, type, detail}], ok: bool}
    """
    if trade_date is None:
        trade_date = date.today()

    issues = []
    db = SessionLocal()

    try:
        # 1. 日K线数量检查
        result = db.execute(
            text("SELECT COUNT(*) FROM stock_daily WHERE trade_date = :d"),
            {"d": trade_date},
        )
        kline_count = result.scalar() or 0

        if kline_count == 0:
            issues.append({
                "table": "stock_daily",
                "type": "missing",
                "detail": f"{trade_date} 无K线数据",
            })
        elif kline_count < 1000:
            issues.append({
                "table": "stock_daily",
                "type": "incomplete",
                "detail": f"{trade_date} 仅 {kline_count} 条K线(预期>4000)",
            })

        # 2. 估值数据检查
        result = db.execute(
            text("SELECT COUNT(*) FROM valuation_daily WHERE trade_date = :d"),
            {"d": trade_date},
        )
        val_count = result.scalar() or 0
        if val_count == 0 and kline_count > 0:
            issues.append({
                "table": "valuation_daily",
                "type": "missing",
                "detail": f"{trade_date} 无估值数据",
            })

        # 3. 异常涨跌幅检查 (非ST股涨跌幅>20%异常)
        result = db.execute(
            text("""
                SELECT d.code, d.change_pct, b.is_st
                FROM stock_daily d
                LEFT JOIN stock_basic b ON d.code = b.code
                WHERE d.trade_date = :d
                  AND ABS(d.change_pct) > 21
                  AND (b.is_st IS NULL OR b.is_st = FALSE)
            """),
            {"d": trade_date},
        )
        outliers = result.fetchall()
        if outliers:
            codes = [f"{r[0]}({r[1]}%)" for r in outliers[:10]]
            issues.append({
                "table": "stock_daily",
                "type": "outlier",
                "detail": f"异常涨跌幅: {', '.join(codes)}",
            })

        # 4. 重复数据检查
        result = db.execute(
            text("""
                SELECT code, COUNT(*) as cnt
                FROM stock_daily
                WHERE trade_date = :d
                GROUP BY code HAVING COUNT(*) > 1
            """),
            {"d": trade_date},
        )
        dups = result.fetchall()
        if dups:
            issues.append({
                "table": "stock_daily",
                "type": "duplicate",
                "detail": f"{len(dups)} 只股票有重复记录",
            })

    finally:
        db.close()

    if issues:
        logger.warning(f"数据质量检查 [{trade_date}] 发现 {len(issues)} 个问题:")
        for issue in issues:
            logger.warning(f"  [{issue['type']}] {issue['table']}: {issue['detail']}")
    else:
        logger.info(f"数据质量检查 [{trade_date}] ✓ 全部正常 (K线{kline_count}条 估值{val_count}条)")

    return {"issues": issues, "ok": len(issues) == 0}


def check_data_freshness() -> dict:
    """
    检查各表数据新鲜度（最后更新时间）。

    Returns:
        {table: last_date}
    """
    db = SessionLocal()
    freshness = {}

    tables_with_date = [
        ("stock_daily", "trade_date"),
        ("valuation_daily", "trade_date"),
        ("fund_flow_daily", "trade_date"),
        ("northbound_flow", "trade_date"),
        ("dragon_tiger_record", "trade_date"),
        ("hot_stocks", "trade_date"),
        ("sector_daily", "trade_date"),
    ]

    try:
        for table, date_col in tables_with_date:
            try:
                result = db.execute(
                    text(f"SELECT MAX({date_col}) FROM {table}")
                )
                last = result.scalar()
                freshness[table] = str(last) if last else "无数据"
            except Exception:
                freshness[table] = "表不存在"
    finally:
        db.close()

    logger.info("数据新鲜度检查:")
    for table, last in freshness.items():
        logger.info(f"  {table:25s}: {last}")

    return freshness
