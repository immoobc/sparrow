"""因子计算引擎 — 从数据库读取行情，计算因子值"""

from datetime import date

import pandas as pd
from sqlalchemy import text

from src.storage.database import SessionLocal
from src.logger import logger


def load_daily_data(
    start_date: str = "2015-01-01",
    end_date: str = None,
    min_days: int = 60,
) -> pd.DataFrame:
    """
    从数据库加载全市场日K线数据。

    Args:
        start_date: 起始日期
        end_date: 结束日期（默认今天）
        min_days: 每只股票最少要有多少天数据才保留

    Returns:
        DataFrame[code, trade_date, open, high, low, close, volume, amount]
    """
    if end_date is None:
        end_date = date.today().isoformat()

    logger.info(f"加载行情数据: {start_date} ~ {end_date}")

    db = SessionLocal()
    try:
        result = db.execute(text("""
            SELECT code, trade_date, open, high, low, close, volume, amount
            FROM stock_daily
            WHERE trade_date >= :start AND trade_date <= :end
              AND close > 0 AND volume > 0
            ORDER BY code, trade_date
        """), {"start": start_date, "end": end_date})

        rows = result.fetchall()
        columns = ["code", "trade_date", "open", "high", "low", "close", "volume", "amount"]
        df = pd.DataFrame(rows, columns=columns)
    finally:
        db.close()

    # 类型转换
    df["trade_date"] = pd.to_datetime(df["trade_date"])
    for col in ["open", "high", "low", "close", "amount"]:
        df[col] = df[col].astype(float)
    df["volume"] = df["volume"].astype(float)

    # 过滤数据量不足的股票
    counts = df.groupby("code").size()
    valid_codes = counts[counts >= min_days].index
    df = df[df["code"].isin(valid_codes)].reset_index(drop=True)

    logger.info(f"加载完成: {df['code'].nunique()} 只股票, {len(df):,} 条记录")
    return df


def calc_return_factor(df: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """
    计算动量/反转因子: 过去N日收益率。

    因子定义: ret_N = close_today / close_{N日前} - 1

    Args:
        df: 全市场日K线 DataFrame
        lookback: 回看天数

    Returns:
        DataFrame[code, trade_date, factor_value]
    """
    logger.info(f"计算 {lookback}日反转因子...")

    df = df.sort_values(["code", "trade_date"]).copy()
    df["factor_value"] = df.groupby("code")["close"].pct_change(lookback)
    factor_df = df.dropna(subset=["factor_value"])[["code", "trade_date", "factor_value"]].copy()

    logger.info(f"因子计算完成: {factor_df['code'].nunique()} 只, {len(factor_df):,} 条")
    return factor_df


def calc_future_return(df: pd.DataFrame, hold_days: int = 20) -> pd.DataFrame:
    """
    计算未来N日收益率（用于评估因子有效性）。

    Args:
        df: 全市场日K线
        hold_days: 持仓天数

    Returns:
        DataFrame[code, trade_date, future_ret]
    """
    df = df.sort_values(["code", "trade_date"]).copy()
    df["future_ret"] = df.groupby("code")["close"].shift(-hold_days) / df["close"] - 1
    return df.dropna(subset=["future_ret"])[["code", "trade_date", "future_ret"]].copy()


def calc_turnover_factor(df: pd.DataFrame, lookback: int = 20) -> pd.DataFrame:
    """
    计算换手率因子: 过去N日平均换手率（成交量/流通股本的代理）。
    用 volume 的滚动均值作为活跃度因子。

    Args:
        df: 全市场日K线
        lookback: 回看天数

    Returns:
        DataFrame[code, trade_date, factor_value]
    """
    results = []
    for code, group in df.groupby("code"):
        group = group.sort_values("trade_date").reset_index(drop=True)
        group["factor_value"] = group["volume"].rolling(lookback).mean()
        # 对数化处理（量级差异太大）
        group["factor_value"] = group["factor_value"].apply(
            lambda x: pd.np.log(x) if x and x > 0 else None
        )
        valid = group.dropna(subset=["factor_value"])[["code", "trade_date", "factor_value"]]
        results.append(valid)

    return pd.concat(results, ignore_index=True)
