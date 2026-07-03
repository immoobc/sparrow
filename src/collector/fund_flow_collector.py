"""资金流向采集器 — 个股日级资金流（数据源：东财push2his，需限流）"""

from datetime import date

from sqlalchemy import text

from src.datasource.eastmoney_source import em_get
from src.logger import logger
from src.storage.database import SessionLocal

UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"


def _fetch_fund_flow_120d(code: str) -> list[dict]:
    """
    拉取个股最近120个交易日的资金流向（日级）。
    数据源: 东财 push2his
    """
    market_code = 1 if code.startswith("6") else 0
    url = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
    params = {
        "secid": f"{market_code}.{code}",
        "fields1": "f1,f2,f3,f7",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
        "lmt": "120",
    }
    headers = {
        "User-Agent": UA,
        "Referer": "https://quote.eastmoney.com/",
    }

    try:
        r = em_get(url, params=params, headers=headers, timeout=15)
        d = r.json()
    except Exception as e:
        logger.warning(f"[{code}] 资金流请求失败: {e}")
        return []

    klines = d.get("data", {}).get("klines", [])
    rows = []
    for line in klines:
        parts = line.split(",")
        if len(parts) >= 6:
            rows.append({
                "date": parts[0],
                "main_net": float(parts[1]) if parts[1] != "-" else 0,
                "small_net": float(parts[2]) if parts[2] != "-" else 0,
                "mid_net": float(parts[3]) if parts[3] != "-" else 0,
                "large_net": float(parts[4]) if parts[4] != "-" else 0,
                "super_net": float(parts[5]) if parts[5] != "-" else 0,
            })
    return rows


def collect_fund_flow_single(code: str) -> int:
    """
    采集单只股票的资金流向并写入 fund_flow_daily 表。

    Returns:
        写入条数
    """
    rows = _fetch_fund_flow_120d(code)
    if not rows:
        return 0

    db = SessionLocal()
    count = 0
    try:
        for row in rows:
            stmt = text("""
                INSERT INTO fund_flow_daily
                    (code, trade_date, main_net, super_net, large_net, mid_net, small_net)
                VALUES
                    (:code, :trade_date, :main_net, :super_net, :large_net, :mid_net, :small_net)
                ON CONFLICT (code, trade_date)
                DO UPDATE SET
                    main_net = EXCLUDED.main_net,
                    super_net = EXCLUDED.super_net,
                    large_net = EXCLUDED.large_net,
                    mid_net = EXCLUDED.mid_net,
                    small_net = EXCLUDED.small_net
            """)
            db.execute(stmt, {
                "code": code,
                "trade_date": row["date"],
                "main_net": row["main_net"],
                "super_net": row["super_net"],
                "large_net": row["large_net"],
                "mid_net": row["mid_net"],
                "small_net": row["small_net"],
            })
            count += 1

        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"[{code}] 资金流写入失败: {e}")
        return 0
    finally:
        db.close()

    return count


def collect_fund_flow_batch(stock_codes: list[str]) -> dict:
    """
    批量采集资金流向（全市场，需注意东财限流）。
    每只间隔 ≥ 1秒（em_get 内置限流）。

    预计耗时: len(stock_codes) × 1.5秒

    Args:
        stock_codes: 股票代码列表

    Returns:
        {total: 写入总条数, success: 成功只数, failed: 失败只数}
    """
    total = 0
    success = 0
    failed = 0

    logger.info(f"开始批量采集资金流向，共 {len(stock_codes)} 只 "
                f"(预计耗时 ~{len(stock_codes) * 1.5 / 60:.0f} 分钟)...")

    for i, code in enumerate(stock_codes):
        try:
            n = collect_fund_flow_single(code)
            if n > 0:
                total += n
                success += 1
            else:
                failed += 1
        except Exception as e:
            logger.warning(f"[{code}] 资金流采集异常: {e}")
            failed += 1

        if (i + 1) % 100 == 0:
            logger.info(
                f"资金流进度: {i + 1}/{len(stock_codes)} "
                f"(成功{success} 失败{failed} 总写入{total}条)"
            )

    logger.info(
        f"资金流采集完成: 成功{success}只 失败{failed}只 总写入{total}条"
    )
    return {"total": total, "success": success, "failed": failed}
