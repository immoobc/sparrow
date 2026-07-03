"""高速历史K线回填 — 批量写入 + 多线程拉取

优化点:
1. 批量 INSERT (executemany) 代替逐条写入，单只股票一次提交
2. 多线程拉取 mootdx（TCP不封IP，可安全并发）
3. 跳过已有数据的股票

用法: python -m scripts.fast_backfill [--workers 4]
"""

import sys
import argparse
from pathlib import Path
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.logger import logger


def fetch_one_stock(code: str) -> list[dict]:
    """拉取单只股票全历史K线（tdxpy直连，线程安全）"""
    import threading
    from tdxpy.hq import TdxHq_API

    servers = [
        ("119.97.185.59", 7709),
        ("124.70.133.119", 7709),
        ("116.205.183.150", 7709),
        ("123.60.73.44", 7709),
        ("116.205.163.254", 7709),
        ("121.36.225.169", 7709),
        ("123.60.70.228", 7709),
        ("124.71.9.153", 7709),
    ]

    tid = threading.current_thread().ident or 0
    server = servers[tid % len(servers)]

    api = TdxHq_API()
    try:
        api.connect(server[0], server[1])
    except Exception:
        # fallback 到其他服务器
        for ip, port in servers:
            try:
                api.connect(ip, port)
                break
            except Exception:
                continue
        else:
            return []

    market = 1 if code.startswith(("6", "9")) else 0
    all_records = []
    start = 0

    try:
        while True:
            raw = api.get_security_bars(4, market, code, start, 800)
            if not raw:
                break

            for item in raw:
                dt = item.get("datetime", "")
                year = item.get("year", 0)
                if year < 1990 or year > 2030:
                    continue
                if not dt or len(dt) < 10:
                    continue
                all_records.append({
                    "code": code,
                    "trade_date": dt[:10],
                    "open": round(item.get("open", 0), 3),
                    "high": round(item.get("high", 0), 3),
                    "low": round(item.get("low", 0), 3),
                    "close": round(item.get("close", 0), 3),
                    "volume": int(item.get("vol", 0) or 0),
                    "amount": round(item.get("amount", 0) or 0, 2),
                })

            if len(raw) < 800:
                break
            start += 800
    finally:
        api.disconnect()

    return all_records


def bulk_insert(records: list[dict]) -> int:
    """批量写入一只股票的全部K线（使用临时表+COPY加速）"""
    if not records:
        return 0

    import io
    import psycopg2
    from src.config import settings

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()

    # 用 StringIO + copy_from 实现高速写入
    buf = io.StringIO()
    for r in records:
        line = f"{r['code']}\t{r['trade_date']}\t{r['open']}\t{r['high']}\t{r['low']}\t{r['close']}\t{r['volume']}\t{r['amount']}\n"
        buf.write(line)
    buf.seek(0)

    # 先写到临时表，再 INSERT ... ON CONFLICT
    cur.execute("""
        CREATE TEMP TABLE IF NOT EXISTS _tmp_kline (
            code CHAR(6), trade_date DATE, open DECIMAL(10,3),
            high DECIMAL(10,3), low DECIMAL(10,3), close DECIMAL(10,3),
            volume BIGINT, amount DECIMAL(18,2)
        ) ON COMMIT DROP
    """)
    cur.execute("TRUNCATE _tmp_kline")
    cur.copy_from(buf, "_tmp_kline", columns=("code", "trade_date", "open", "high", "low", "close", "volume", "amount"))

    cur.execute("""
        INSERT INTO stock_daily (code, trade_date, open, high, low, close, volume, amount)
        SELECT code, trade_date, open, high, low, close, volume, amount FROM _tmp_kline
        ON CONFLICT (code, trade_date) DO NOTHING
    """)

    conn.commit()
    count = len(records)
    cur.close()
    conn.close()
    return count


def get_existing_codes() -> set:
    """获取已有K线数据的股票代码（跳过用）"""
    import psycopg2
    from src.config import settings

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    cur.execute("SELECT DISTINCT code FROM stock_daily")
    codes = {row[0].strip() for row in cur.fetchall()}
    cur.close()
    conn.close()
    return codes


def get_all_codes() -> list[str]:
    """获取全部活跃股票代码"""
    import psycopg2
    from src.config import settings

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()
    cur.execute("SELECT code FROM stock_basic WHERE is_active = TRUE ORDER BY code")
    codes = [row[0].strip() for row in cur.fetchall()]
    cur.close()
    conn.close()
    return codes


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--workers", type=int, default=4, help="并发线程数")
    parser.add_argument("--force", action="store_true", help="不跳过已有数据")
    args = parser.parse_args()

    all_codes = get_all_codes()
    existing = get_existing_codes() if not args.force else set()
    todo = [c for c in all_codes if c not in existing]

    logger.info(f"全市场 {len(all_codes)} 只, 已有 {len(existing)} 只, 待回填 {len(todo)} 只")
    logger.info(f"并发线程: {args.workers}")

    if not todo:
        logger.info("无需回填")
        return

    total_rows = 0
    success = 0
    failed = 0
    start_time = datetime.now()

    with ThreadPoolExecutor(max_workers=args.workers) as executor:
        futures = {executor.submit(fetch_one_stock, code): code for code in todo}

        for i, future in enumerate(as_completed(futures), 1):
            code = futures[future]
            try:
                records = future.result()
                if records:
                    bulk_insert(records)
                    total_rows += len(records)
                    success += 1
                else:
                    # 无数据(退市/暂停)不算失败
                    pass
            except Exception as e:
                logger.warning(f"[{code}] 失败: {e}")
                failed += 1

            if i % 50 == 0:
                elapsed = (datetime.now() - start_time).total_seconds()
                speed = i / elapsed * 60  # 只/分钟
                eta = (len(todo) - i) / (i / elapsed) / 60
                logger.info(
                    f"进度: {i}/{len(todo)} ({i*100//len(todo)}%) | "
                    f"成功{success} 失败{failed} | "
                    f"{total_rows:,}条 | "
                    f"速度{speed:.0f}只/分 | ETA {eta:.0f}分钟"
                )

    elapsed = (datetime.now() - start_time).total_seconds()
    logger.info(
        f"\n✓ 回填完成! "
        f"成功{success}只 失败{failed}只 | "
        f"共{total_rows:,}条K线 | "
        f"耗时{elapsed/60:.1f}分钟"
    )


if __name__ == "__main__":
    main()
