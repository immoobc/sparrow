"""本地 Parquet 缓存层 — 回测/因子研究的高速数据读取

架构:
    PG (权威存储) → export_to_parquet() → data/parquet/*.parquet
                                              ↓
    因子研究/回测 ← load_daily() ← 直接读 Parquet (<1秒)

使用:
    from src.storage.cache import load_daily, export_to_parquet

    # 首次或数据更新后执行一次导出
    export_to_parquet()

    # 之后回测直接用 load_daily()
    df = load_daily("2023-01-01", "2025-12-31")  # <1秒
"""

import time
from datetime import date
from io import StringIO
from pathlib import Path

import pandas as pd
import psycopg2

from src.config import settings
from src.logger import logger

# 缓存目录
CACHE_DIR = Path(settings.data_dir) / "parquet"


def export_to_parquet(
    start_date: str = "1990-01-01",
    end_date: str = None,
    chunk_years: int = 5,
    incremental: bool = True,
) -> dict:
    """
    从 PostgreSQL 导出日K线到 Parquet 文件。
    按年段分文件存储，方便增量更新。

    Args:
        start_date: 起始日期
        end_date: 结束日期（默认今天）
        chunk_years: 每个文件覆盖多少年
        incremental: 增量模式（只更新包含最近数据的文件，跳过历史文件）

    Returns:
        {files: 文件数, rows: 总行数, size_mb: 磁盘大小, elapsed: 耗时}
    """
    if end_date is None:
        end_date = date.today().isoformat()

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    logger.info(f"导出 Parquet: {start_date} ~ {end_date}" + (" (增量)" if incremental else " (全量)"))
    t0 = time.time()

    conn = psycopg2.connect(settings.database_url)
    cur = conn.cursor()

    total_rows = 0
    files = []

    # 按年段导出
    start_year = int(start_date[:4])
    end_year = int(end_date[:4])

    # 增量模式: 只重建最近的1个chunk(包含今年的那个5年段)
    if incremental:
        current_chunk_start = (end_year // chunk_years) * chunk_years
        incremental_start = current_chunk_start
    else:
        incremental_start = start_year

    for y in range(start_year, end_year + 1, chunk_years):
        y_start = f"{y}-01-01"
        y_end = f"{min(y + chunk_years, end_year + 1)}-01-01"
        fname = CACHE_DIR / f"daily_{y}_{y + chunk_years - 1}.parquet"

        # 增量模式: 跳过已存在的历史文件
        if incremental and y < incremental_start and fname.exists():
            # 文件已存在且不在更新范围内，直接统计
            existing_df = pd.read_parquet(fname, columns=["code"], engine="pyarrow")
            total_rows += len(existing_df)
            files.append(fname)
            continue

        buf = StringIO()
        cur.copy_expert(f"""
            COPY (
                SELECT code, trade_date, open, high, low, close, volume, amount
                FROM stock_daily
                WHERE trade_date >= '{y_start}' AND trade_date < '{y_end}'
                  AND close > 0
                ORDER BY code, trade_date
            ) TO STDOUT WITH CSV HEADER
        """, buf)
        buf.seek(0)

        df = pd.read_csv(buf, dtype={"code": str})
        if df.empty:
            continue

        df["code"] = df["code"].str.strip().str.zfill(6)
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        for col in ["open", "high", "low", "close", "amount"]:
            df[col] = df[col].astype("float32")
        df["volume"] = df["volume"].astype("int64")

        df.to_parquet(fname, index=False, engine="pyarrow", compression="zstd")
        total_rows += len(df)
        files.append(fname)
        logger.info(f"  {fname.name}: {len(df):,} 条")

    conn.close()

    total_size = sum(f.stat().st_size for f in files) / 1024 / 1024
    elapsed = time.time() - t0
    logger.info(
        f"导出完成: {len(files)}个文件, {total_rows:,}条, "
        f"{total_size:.1f}MB, {elapsed:.1f}秒"
    )

    return {
        "files": len(files),
        "rows": total_rows,
        "size_mb": round(total_size, 1),
        "elapsed": round(elapsed, 1),
    }


def load_daily(
    start_date: str = None,
    end_date: str = None,
    columns: list[str] = None,
    codes: list[str] = None,
) -> pd.DataFrame:
    """
    从 Parquet 缓存快速加载日K线数据。

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

    # 根据文件名过滤需要的年段
    needed_files = parquet_files
    if start_date:
        start_year = int(start_date[:4])
        needed_files = [
            f for f in needed_files
            if int(f.stem.split("_")[2]) >= start_year - 5
        ]

    # 读取并合并
    frames = []
    read_cols = columns if columns else None
    for f in needed_files:
        df = pd.read_parquet(f, columns=read_cols, engine="pyarrow")
        frames.append(df)

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
    - turnover_proxy: 成交量变化率

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


def get_cache_info() -> dict:
    """查看缓存状态"""
    if not CACHE_DIR.exists():
        return {"exists": False}

    files = sorted(CACHE_DIR.glob("daily_*.parquet"))
    total_size = sum(f.stat().st_size for f in files) / 1024 / 1024

    # 读取最新文件的日期范围
    if files:
        last_df = pd.read_parquet(files[-1], columns=["trade_date"], engine="pyarrow")
        latest_date = last_df["trade_date"].max()
    else:
        latest_date = None

    return {
        "exists": True,
        "files": len(files),
        "size_mb": round(total_size, 1),
        "latest_date": str(latest_date)[:10] if latest_date else None,
        "path": str(CACHE_DIR),
    }
