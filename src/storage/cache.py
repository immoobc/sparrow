"""本地 Parquet 缓存层 — 回测/因子研究的高速数据读取

架构:
    PG (权威存储) → export_to_parquet() → data/parquet/*.parquet
                                              ↓
    因子研究/回测 ← load_daily() ← 直接读 Parquet (<1秒)

分文件策略:
    - 历史年份(非当年): 按年分文件 daily_2020.parquet, daily_2021.parquet ...
      一旦建好永不再动。
    - 当年数据: 按月分文件 daily_2026_07.parquet
      增量更新只重建当月文件（几秒）。

增量更新: 只重建当月 → 1-3秒
断电续建: 检测缺失的 chunk 自动补建
全量重建: export_to_parquet(incremental=False)

使用:
    from src.storage.cache import load_daily, export_to_parquet

    # 日常增量(只更新当月)
    export_to_parquet()

    # 全量重建(首次建库)
    export_to_parquet(incremental=False)

    # 读取
    df = load_daily("2023-01-01", "2025-12-31")  # <1秒
"""

import time
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import psycopg2

from src.config import settings
from src.logger import logger

# 缓存目录
CACHE_DIR = Path(settings.data_dir) / "parquet"

# Arrow schema (统一)
_COLUMNS = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount"]


def _to_df(rows: list) -> pd.DataFrame:
    """原始行 → 规范化 DataFrame"""
    df = pd.DataFrame(rows, columns=_COLUMNS)
    df["code"] = df["code"].str.strip().str.zfill(6)
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    for col in ["open", "high", "low", "close", "amount"]:
        df[col] = df[col].astype("float32")
    df["volume"] = df["volume"].astype("int64")
    return df


def _write_parquet(df: pd.DataFrame, path: Path):
    """DataFrame → Parquet (zstd 压缩)"""
    df.to_parquet(path, index=False, engine="pyarrow", compression="zstd")


def _query_date_range(conn, start: str, end: str) -> list:
    """从 PG 查询一个日期范围的数据"""
    cur = conn.cursor()
    cur.execute("""
        SELECT code, trade_date, open, high, low, close, volume, amount
        FROM stock_daily
        WHERE trade_date >= %s AND trade_date < %s AND close > 0
        ORDER BY code, trade_date
    """, (start, end))
    rows = cur.fetchall()
    cur.close()
    return rows


def _query_date_range_streaming(conn, start: str, end: str, chunk_size: int = 100_000):
    """流式查询，用于大范围导出，避免一次性加载到内存"""
    import gc
    import pyarrow as pa
    import pyarrow.parquet as pq

    cur = conn.cursor(name=f"stream_{start}_{end}".replace("-", ""))
    cur.itersize = chunk_size
    cur.execute("""
        SELECT code, trade_date, open, high, low, close, volume, amount
        FROM stock_daily
        WHERE trade_date >= %s AND trade_date < %s AND close > 0
        ORDER BY code, trade_date
    """, (start, end))

    all_rows = []
    while True:
        rows = cur.fetchmany(chunk_size)
        if not rows:
            break
        all_rows.extend(rows)

    cur.close()
    return all_rows


# ══════════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════════

def export_to_parquet(
    start_date: str = "1990-01-01",
    end_date: str = None,
    incremental: bool = True,
    **kwargs,  # 兼容旧调用的 chunk_years 参数
) -> dict:
    """
    从 PostgreSQL 导出日K线到 Parquet 缓存。

    增量模式(默认):
        - 只重建当月文件 + 补建缺失的历史文件
        - 通常 1-3 秒

    全量模式:
        - 重建所有文件（历史按年，当年按月）
        - 首次建库/数据修复时使用

    Returns:
        {files: 文件数, rows: 总行数, size_mb: 磁盘大小, elapsed: 耗时}
    """
    if end_date is None:
        end_date = date.today().isoformat()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    t0 = time.time()

    if incremental:
        result = _export_incremental(end_date)
    else:
        result = _export_full(start_date, end_date)

    elapsed = time.time() - t0
    result["elapsed"] = round(elapsed, 1)
    logger.info(
        f"导出完成: {result['files']}个文件, {result['rows']:,}条, "
        f"{result.get('size_mb', 0):.1f}MB, {elapsed:.1f}秒"
    )
    return result


# ══════════════════════════════════════════════════════════════
# 增量导出: 只重建当月 + 补缺失
# ══════════════════════════════════════════════════════════════

def _export_incremental(end_date: str) -> dict:
    """
    快速增量:
    1. 重建当月 parquet (几千条数据，秒级)
    2. 检查是否有缺失的历史 chunk，有则补建
    """
    today = date.today()
    current_year = today.year
    current_month = today.month

    logger.info(f"增量导出: 重建 {current_year}-{current_month:02d} ...")

    conn = psycopg2.connect(settings.database_url)

    try:
        # ── 1. 重建当月文件 ──
        month_start = f"{current_year}-{current_month:02d}-01"
        if current_month == 12:
            month_end = f"{current_year + 1}-01-01"
        else:
            month_end = f"{current_year}-{current_month + 1:02d}-01"

        fname = CACHE_DIR / f"daily_{current_year}_{current_month:02d}.parquet"
        rows = _query_date_range(conn, month_start, month_end)

        if rows:
            df = _to_df(rows)
            _write_parquet(df, fname)
            logger.info(f"  {fname.name}: {len(df):,} 条 ✓")
            del df
        else:
            logger.info(f"  {fname.name}: 当月无数据")

        # ── 2. 补建缺失的 chunk ──
        missing = _find_missing_chunks(conn, current_year, current_month)
        if missing:
            logger.info(f"  发现 {len(missing)} 个缺失chunk，补建中...")
            for chunk_info in missing:
                _build_chunk(conn, chunk_info)
        else:
            logger.info("  历史chunk完整，无需补建")

    finally:
        conn.close()

    return _get_stats()


def _find_missing_chunks(conn, current_year: int, current_month: int) -> list:
    """
    检查哪些 chunk 文件缺失需要补建。
    兼容旧格式: 如果旧的5年段文件已覆盖该年份，则不算缺失。
    """
    # 查 PG 中最早的数据年份
    cur = conn.cursor()
    cur.execute("SELECT EXTRACT(YEAR FROM MIN(trade_date))::int FROM stock_daily WHERE close > 0")
    row = cur.fetchone()
    cur.close()

    if not row or not row[0]:
        return []

    min_year = row[0]
    missing = []

    # 检查哪些年份已被旧格式文件覆盖
    covered_years = set()
    for f in CACHE_DIR.glob("daily_*_*.parquet"):
        parts = f.stem.split("_")
        if len(parts) == 3:
            try:
                a, b = int(parts[1]), int(parts[2])
                if b > 12:  # 旧格式: daily_2020_2024
                    for y in range(a, b + 1):
                        covered_years.add(y)
            except ValueError:
                pass

    # 检查历史年份文件 (非当年)
    for year in range(min_year, current_year):
        fname = CACHE_DIR / f"daily_{year}.parquet"
        if not fname.exists() and year not in covered_years:
            missing.append({"type": "year", "year": year})

    # 检查当年的历史月份文件
    for month in range(1, current_month):
        fname = CACHE_DIR / f"daily_{current_year}_{month:02d}.parquet"
        if not fname.exists() and current_year not in covered_years:
            missing.append({"type": "month", "year": current_year, "month": month})

    return missing


def _build_chunk(conn, chunk_info: dict):
    """构建单个 chunk 文件"""
    if chunk_info["type"] == "year":
        year = chunk_info["year"]
        start = f"{year}-01-01"
        end = f"{year + 1}-01-01"
        fname = CACHE_DIR / f"daily_{year}.parquet"
        label = str(year)
    else:
        year = chunk_info["year"]
        month = chunk_info["month"]
        start = f"{year}-{month:02d}-01"
        if month == 12:
            end = f"{year + 1}-01-01"
        else:
            end = f"{year}-{month + 1:02d}-01"
        fname = CACHE_DIR / f"daily_{year}_{month:02d}.parquet"
        label = f"{year}-{month:02d}"

    logger.info(f"    补建 {label}...")

    # 对于大范围用流式，小范围直接查
    chunk_rows = settings.effective_parquet_chunk_rows
    rows = _query_date_range_streaming(conn, start, end, chunk_size=chunk_rows)

    if rows:
        df = _to_df(rows)
        _write_parquet(df, fname)
        logger.info(f"    {fname.name}: {len(df):,} 条 ✓")
        del df
    else:
        logger.info(f"    {label}: 无数据，跳过")


# ══════════════════════════════════════════════════════════════
# 全量导出: 首次建库 / 修复
# ══════════════════════════════════════════════════════════════

def _export_full(start_date: str, end_date: str) -> dict:
    """
    全量导出: 历史按年，当年按月。带进度日志。
    """
    import gc

    today = date.today()
    current_year = today.year
    current_month = today.month

    start_year = int(start_date[:4])
    end_year = int(end_date[:4])

    conn = psycopg2.connect(settings.database_url)
    chunk_rows = settings.effective_parquet_chunk_rows

    # 计算总任务数
    total_tasks = (min(end_year, current_year) - start_year) + current_month
    done = 0

    logger.info(f"全量导出: {start_year} ~ {end_year} (共约{total_tasks}个chunk)")

    try:
        # ── 历史年份: 按年 ──
        for year in range(start_year, min(end_year + 1, current_year)):
            fname = CACHE_DIR / f"daily_{year}.parquet"
            start = f"{year}-01-01"
            end = f"{year + 1}-01-01"

            done += 1
            logger.info(f"  [{done}/{total_tasks}] {year}年...")

            rows = _query_date_range_streaming(conn, start, end, chunk_size=chunk_rows)
            if rows:
                df = _to_df(rows)
                _write_parquet(df, fname)
                logger.info(f"    {fname.name}: {len(df):,} 条 ✓")
                del df, rows
            else:
                logger.info(f"    {year}: 无数据")

            gc.collect()

        # ── 当年: 按月 ──
        for month in range(1, current_month + 1):
            fname = CACHE_DIR / f"daily_{current_year}_{month:02d}.parquet"
            start = f"{current_year}-{month:02d}-01"
            if month == 12:
                end_m = f"{current_year + 1}-01-01"
            else:
                end_m = f"{current_year}-{month + 1:02d}-01"

            done += 1
            logger.info(f"  [{done}/{total_tasks}] {current_year}-{month:02d}...")

            rows = _query_date_range(conn, start, end_m)
            if rows:
                df = _to_df(rows)
                _write_parquet(df, fname)
                logger.info(f"    {fname.name}: {len(df):,} 条 ✓")
                del df
            else:
                logger.info(f"    {current_year}-{month:02d}: 无数据")

    finally:
        conn.close()

    return _get_stats()


# ══════════════════════════════════════════════════════════════
# 统计
# ══════════════════════════════════════════════════════════════

def _get_stats() -> dict:
    """统计缓存目录信息"""
    import pyarrow.parquet as pq

    files = sorted(CACHE_DIR.glob("daily_*.parquet"))
    total_rows = 0
    for f in files:
        try:
            meta = pq.read_metadata(str(f))
            total_rows += meta.num_rows
        except Exception:
            pass
    total_size = sum(f.stat().st_size for f in files) / 1024 / 1024
    return {"files": len(files), "rows": total_rows, "size_mb": round(total_size, 1)}


# ══════════════════════════════════════════════════════════════
# 读取
# ══════════════════════════════════════════════════════════════

def load_daily(
    start_date: str = None,
    end_date: str = None,
    columns: list[str] = None,
    codes: list[str] = None,
) -> pd.DataFrame:
    """
    从 Parquet 缓存快速加载日K线数据。

    自动匹配新旧两种文件命名:
    - 旧格式: daily_2020_2024.parquet (5年段)
    - 新格式: daily_2020.parquet (按年) / daily_2026_07.parquet (按月)

    Args:
        start_date: 起始日期 (如 "2023-01-01")
        end_date: 结束日期
        columns: 需要的列 (默认全部)
        codes: 指定股票代码列表 (None=全市场)

    Returns:
        DataFrame，通常 <1秒加载完成
    """
    if not CACHE_DIR.exists():
        raise FileNotFoundError(
            f"Parquet 缓存不存在: {CACHE_DIR}\n"
            "请先执行: python main.py export-cache"
        )

    t0 = time.time()
    parquet_files = sorted(CACHE_DIR.glob("daily_*.parquet"))
    if not parquet_files:
        raise FileNotFoundError("无 Parquet 文件，请先执行导出")

    # 智能过滤: 根据文件名判断是否可能包含目标日期范围
    needed_files = []
    for f in parquet_files:
        if _file_might_contain(f, start_date, end_date):
            needed_files.append(f)

    if not needed_files:
        needed_files = parquet_files  # fallback: 全读

    # 读取并合并
    # 确保 trade_date 和 code 始终被读取(过滤需要)
    read_cols = columns if columns else None
    if read_cols is not None:
        required = set()
        if start_date or end_date:
            required.add("trade_date")
        if codes:
            required.add("code")
        read_cols = list(set(read_cols) | required)

    frames = []
    for f in needed_files:
        try:
            df = pd.read_parquet(f, columns=read_cols, engine="pyarrow")
            frames.append(df)
        except Exception:
            continue  # 跳过损坏文件

    if not frames:
        return pd.DataFrame()

    df = pd.concat(frames, ignore_index=True)

    # 过滤日期范围
    if start_date:
        df = df[df["trade_date"] >= pd.Timestamp(start_date)]
    if end_date:
        df = df[df["trade_date"] <= pd.Timestamp(end_date)]

    # 过滤股票
    if codes:
        df = df[df["code"].isin(codes)]

    elapsed = time.time() - t0
    logger.debug(f"Parquet 加载: {len(df):,}条, {elapsed:.2f}秒")
    return df


def _file_might_contain(f: Path, start_date: str = None, end_date: str = None) -> bool:
    """根据文件名快速判断是否可能包含目标日期范围"""
    stem = f.stem  # e.g. "daily_2020", "daily_2026_07", "daily_2020_2024"
    parts = stem.split("_")

    if len(parts) == 2:
        # daily_2020 → 整年
        try:
            file_year = int(parts[1])
        except ValueError:
            return True
        if start_date and file_year < int(start_date[:4]) - 1:
            return False
        if end_date and file_year > int(end_date[:4]):
            return False
        return True

    elif len(parts) == 3:
        try:
            a, b = int(parts[1]), int(parts[2])
        except ValueError:
            return True

        if b > 12:
            # daily_2020_2024 → 旧格式5年段
            file_start_year, file_end_year = a, b
            if start_date and file_end_year < int(start_date[:4]):
                return False
            if end_date and file_start_year > int(end_date[:4]):
                return False
            return True
        else:
            # daily_2026_07 → 按月 (year=a, month=b)
            file_year, file_month = a, b
            if start_date:
                start_year = int(start_date[:4])
                start_month = int(start_date[5:7])
                # 文件月份 < 起始月份 → 不需要
                if file_year < start_year or (file_year == start_year and file_month < start_month):
                    return False
            if end_date:
                end_year = int(end_date[:4])
                end_month = int(end_date[5:7])
                if file_year > end_year or (file_year == end_year and file_month > end_month):
                    return False
            return True

    return True


def load_daily_with_derived(
    start_date: str = None,
    end_date: str = None,
    codes: list[str] = None,
    min_days: int = 60,
) -> pd.DataFrame:
    """
    加载日K线 + 预计算常用派生字段。

    额外字段:
    - daily_ret: 日收益率
    - log_ret: 对数收益率
    - vwap: 成交均价 (amount/volume)

    Args:
        min_days: 过滤交易日数不足的股票

    Returns:
        带派生字段的 DataFrame
    """
    import numpy as np

    df = load_daily(start_date=start_date, end_date=end_date, codes=codes)
    if df.empty:
        return df

    df = df.sort_values(["code", "trade_date"]).reset_index(drop=True)

    # 日收益率
    df["daily_ret"] = df.groupby("code")["close"].pct_change()
    # 对数收益率
    df["log_ret"] = np.log(df["close"] / df.groupby("code")["close"].shift(1))
    # 成交均价
    df["vwap"] = df["amount"] / df["volume"].replace(0, np.nan)

    # 过滤数据不足的
    if min_days > 0:
        counts = df.groupby("code").size()
        valid = counts[counts >= min_days].index
        df = df[df["code"].isin(valid)]

    return df.reset_index(drop=True)


# ══════════════════════════════════════════════════════════════
# 缓存状态
# ══════════════════════════════════════════════════════════════

def get_cache_info() -> dict:
    """查看缓存状态"""
    if not CACHE_DIR.exists():
        return {"exists": False}

    files = sorted(CACHE_DIR.glob("daily_*.parquet"))
    if not files:
        return {"exists": False}

    total_size = sum(f.stat().st_size for f in files) / 1024 / 1024

    # 读取最新文件的日期范围（容错：跳过损坏文件）
    latest_date = None
    for f in reversed(files):
        try:
            last_df = pd.read_parquet(f, columns=["trade_date"], engine="pyarrow")
            if not last_df.empty:
                latest_date = last_df["trade_date"].max()
                break
        except Exception:
            continue

    return {
        "exists": True,
        "files": len(files),
        "size_mb": round(total_size, 1),
        "latest_date": str(latest_date)[:10] if latest_date else None,
        "path": str(CACHE_DIR),
    }
