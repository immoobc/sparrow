"""市场温度计 — 告诉你现在市场贵不贵，该加仓还是该等

核心指标:
1. 估值温度: 全市场PE中位数在历史中的百分位 (0%=最便宜, 100%=最贵)
2. 情绪温度: 成交量、北向资金、涨跌比综合
3. 趋势温度: 均线位置 (在均线上方=趋势向上)

综合信号:
- 温度 < 30: 🟢 低估区间，积极加仓
- 温度 30-50: 🟡 合理偏低，正常定投
- 温度 50-70: 🟠 合理偏高，减少加仓
- 温度 > 70: 🔴 高估区间，停止加仓/考虑减仓
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.logger import logger
from src.storage.cache import load_daily


def calc_valuation_temperature(df: pd.DataFrame = None) -> dict:
    """
    计算估值温度: 当前全市场PE中位数在近5年中的百分位。

    用"股价/近1年涨幅还原EPS"的简化方式估算PE:
    实际用 close / (close - 1年前close) 近似盈利增速判断贵不贵。
    更直接的方式: 用全市场中位数市净率(PB)替代——股价/净资产比。
    
    简化版: 用"全市场收盘价中位数 / 250日均线" 作为估值代理。
    >1.2 = 贵, <0.8 = 便宜。
    """
    if df is None:
        df = load_daily(
            start_date=(date.today() - timedelta(days=1300)).isoformat()
        )

    if df.empty:
        return {"temperature": 50, "signal": "无数据"}

    df = df.sort_values(["code", "trade_date"]).copy()

    # 计算每只股票的"价格/250日均线"比值
    df["ma250"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(250, min_periods=200).mean()
    )
    df["price_to_ma"] = df["close"] / df["ma250"]

    # 每天取全市场中位数
    daily_median = df.groupby("trade_date")["price_to_ma"].median()
    daily_median = daily_median.dropna().sort_index()

    if daily_median.empty:
        return {"temperature": 50, "signal": "数据不足"}

    # 当前值
    current = daily_median.iloc[-1]
    current_date = daily_median.index[-1]

    # 在历史中的百分位
    temperature = (daily_median < current).mean() * 100

    return {
        "temperature": round(temperature, 1),
        "current_ratio": round(current, 3),
        "date": str(current_date)[:10],
        "history_min": round(daily_median.min(), 3),
        "history_max": round(daily_median.max(), 3),
        "history_median": round(daily_median.median(), 3),
    }


def calc_momentum_temperature(df: pd.DataFrame = None) -> dict:
    """
    趋势温度: 多少比例的股票站在20日均线上方。
    >60% = 市场强势, <40% = 市场弱势。
    """
    if df is None:
        df = load_daily(
            start_date=(date.today() - timedelta(days=100)).isoformat()
        )

    if df.empty:
        return {"temperature": 50}

    df = df.sort_values(["code", "trade_date"]).copy()
    df["ma20"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(20, min_periods=15).mean()
    )
    df["above_ma20"] = df["close"] > df["ma20"]

    # 最新一天
    latest_date = df["trade_date"].max()
    latest = df[df["trade_date"] == latest_date]

    above_pct = latest["above_ma20"].mean() * 100

    # 历史百分位
    daily_pct = df.groupby("trade_date")["above_ma20"].mean() * 100
    temperature = (daily_pct < above_pct).mean() * 100

    return {
        "temperature": round(temperature, 1),
        "above_ma20_pct": round(above_pct, 1),
        "date": str(latest_date)[:10],
    }


def calc_volume_temperature(df: pd.DataFrame = None) -> dict:
    """
    成交量温度: 当前成交量 vs 近1年均量。
    放量 = 情绪高涨, 缩量 = 观望/低迷。
    """
    if df is None:
        df = load_daily(
            start_date=(date.today() - timedelta(days=300)).isoformat()
        )

    if df.empty:
        return {"temperature": 50}

    # 全市场每日总成交额
    daily_amount = df.groupby("trade_date")["amount"].sum().sort_index()

    if len(daily_amount) < 20:
        return {"temperature": 50}

    current = daily_amount.iloc[-1]
    ma60 = daily_amount.rolling(60).mean().iloc[-1]
    ratio = current / ma60 if ma60 > 0 else 1

    # 在历史中的百分位
    temperature = (daily_amount < current).mean() * 100

    return {
        "temperature": round(temperature, 1),
        "volume_ratio": round(ratio, 2),
        "current_amount_yi": round(current / 1e8, 0),
        "date": str(daily_amount.index[-1])[:10],
    }


def get_market_temperature() -> dict:
    """
    综合市场温度计。

    Returns:
        {
            overall: 综合温度 (0-100),
            valuation: 估值温度,
            momentum: 趋势温度,
            volume: 成交量温度,
            signal: 操作建议文字,
            action: "加仓"/"定投"/"观望"/"减仓",
        }
    """
    logger.info("计算市场温度...")

    # 一次加载数据，复用
    df = load_daily(
        start_date=(date.today() - timedelta(days=1300)).isoformat()
    )

    val = calc_valuation_temperature(df)
    mom = calc_momentum_temperature(df)
    vol = calc_volume_temperature(df)

    # 综合: 估值权重50% + 趋势30% + 成交量20%
    overall = (
        val["temperature"] * 0.5 +
        mom["temperature"] * 0.3 +
        vol["temperature"] * 0.2
    )

    # 信号判定
    if overall < 25:
        action = "积极加仓"
        signal = "🟢 市场低估+低迷，是加仓的好时机。建议加大定投金额或一次性加仓。"
    elif overall < 40:
        action = "正常定投"
        signal = "🟢 市场偏低估，正常定投即可，不用着急但也别错过。"
    elif overall < 55:
        action = "正常定投"
        signal = "🟡 市场估值合理，按计划定投，不追高不恐慌。"
    elif overall < 70:
        action = "减少加仓"
        signal = "🟠 市场偏贵，减少新增投入，持有为主。"
    elif overall < 85:
        action = "停止加仓"
        signal = "🔴 市场高估，停止加仓。已有持仓可继续持有，但不再追加。"
    else:
        action = "考虑减仓"
        signal = "🔴 市场过热，考虑分批减仓止盈，保护利润。"

    return {
        "overall": round(overall, 1),
        "valuation": val,
        "momentum": mom,
        "volume": vol,
        "signal": signal,
        "action": action,
        "date": val.get("date", str(date.today())),
    }
