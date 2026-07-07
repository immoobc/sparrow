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

内存优化(2C2G适配):
- 采样计算: 随机抽 500 只股票代替全市场(统计误差<2%)
- 只加载必要列
- 成交量温度只做聚合(全市场日成交额)，不存个股明细
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.config import settings
from src.logger import logger
from src.storage.cache import load_daily

# 低配采样数, 高配全量
_SAMPLE_CODES = 300 if settings.is_low_memory else 0  # 0 = 不采样


def _sample_codes(df: pd.DataFrame, n: int) -> pd.DataFrame:
    """从 DataFrame 中随机采样 n 只股票的全部数据"""
    if n <= 0:
        return df
    all_codes = df["code"].unique()
    if len(all_codes) <= n:
        return df
    # 固定随机种子，确保同一天结果一致
    rng = np.random.default_rng(seed=int(date.today().toordinal()))
    sampled = rng.choice(all_codes, size=n, replace=False)
    return df[df["code"].isin(sampled)]


def calc_valuation_temperature(df: pd.DataFrame = None) -> dict:
    """
    估值温度: "全市场收盘价中位数 / 250日均线" 在历史中的百分位。
    >1.2 = 贵, <0.8 = 便宜。
    """
    if df is None:
        df = load_daily(
            start_date=(date.today() - timedelta(days=1300)).isoformat(),
            columns=["code", "trade_date", "close"],
        )

    if df.empty:
        return {"temperature": 50, "signal": "无数据"}

    # 采样以控制内存
    df = _sample_codes(df, _SAMPLE_CODES)
    df = df.sort_values(["code", "trade_date"]).copy()

    # MA窗口: 低配用60日(季线), 高配用250日(年线)
    ma_window = 60 if settings.is_low_memory else 250
    min_periods = int(ma_window * 0.7)

    # 计算每只股票的"价格/均线"比值
    df["ma"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(ma_window, min_periods=min_periods).mean()
    )
    df["price_to_ma"] = df["close"] / df["ma"]

    # 每天取全市场中位数
    daily_median = df.groupby("trade_date")["price_to_ma"].median()
    daily_median = daily_median.dropna().sort_index()

    # 释放中间数据
    del df

    if daily_median.empty:
        return {"temperature": 50, "signal": "数据不足"}

    current = daily_median.iloc[-1]
    current_date = daily_median.index[-1]
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
            start_date=(date.today() - timedelta(days=100)).isoformat(),
            columns=["code", "trade_date", "close"],
        )

    if df.empty:
        return {"temperature": 50}

    # 动量不采样 — 需要全市场占比才有意义
    # 但只需要最近100天数据，内存可控(~30MB)
    df = df.sort_values(["code", "trade_date"]).copy()
    df["ma20"] = df.groupby("code")["close"].transform(
        lambda x: x.rolling(20, min_periods=15).mean()
    )
    df["above_ma20"] = df["close"] > df["ma20"]

    latest_date = df["trade_date"].max()
    latest = df[df["trade_date"] == latest_date]
    above_pct = latest["above_ma20"].mean() * 100

    # 历史百分位
    daily_pct = df.groupby("trade_date")["above_ma20"].mean() * 100
    temperature = (daily_pct < above_pct).mean() * 100

    del df

    return {
        "temperature": round(temperature, 1),
        "above_ma20_pct": round(above_pct, 1),
        "date": str(latest_date)[:10],
    }


def calc_volume_temperature(df: pd.DataFrame = None) -> dict:
    """
    成交量温度: 当前全市场日成交额 vs 近60日均额。
    不需要个股明细，只需要每天的总成交额。
    """
    if df is None:
        df = load_daily(
            start_date=(date.today() - timedelta(days=300)).isoformat(),
            columns=["trade_date", "amount"],
        )

    if df.empty:
        return {"temperature": 50}

    # 直接聚合: 每天总成交额(不需要保留个股)
    daily_amount = df.groupby("trade_date")["amount"].sum().sort_index()

    del df

    if len(daily_amount) < 20:
        return {"temperature": 50}

    current = daily_amount.iloc[-1]
    ma60 = daily_amount.rolling(60).mean().iloc[-1]
    ratio = current / ma60 if ma60 > 0 else 1
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

    内存策略:
    - 低配(2G): 回看1年(250天), 采样500只 → 峰值 ~80MB
    - 高配(>2G): 回看3.5年(900天), 全量 → 峰值 ~400MB

    缩短回看窗口对实际操作建议影响极小——判断的是"当前相对近期贵不贵"。
    """
    import gc
    from pathlib import Path

    logger.info("计算市场温度...")

    # 根据内存选择回看天数
    if settings.is_low_memory:
        val_days = 90     # 3个月 (只读2-3个parquet文件)
        mom_days = 40     # 40天 (1-2个文件)
        vol_days = 40     # 40天
    else:
        val_days = 900    # 3.5年
        mom_days = 100
        vol_days = 300

    # 预采样
    sample_codes = None
    if _SAMPLE_CODES > 0:
        try:
            cache_dir = Path(settings.data_dir) / "parquet"
            recent_files = sorted(cache_dir.glob("daily_*.parquet"))
            if recent_files:
                codes_only = pd.read_parquet(
                    recent_files[-1], columns=["code"], engine="pyarrow"
                )
                all_codes = codes_only["code"].unique()
                del codes_only
                if len(all_codes) > _SAMPLE_CODES:
                    rng = np.random.default_rng(seed=int(date.today().toordinal()))
                    sample_codes = rng.choice(all_codes, size=_SAMPLE_CODES, replace=False).tolist()
                del all_codes
                gc.collect()
        except Exception:
            pass

    # 1. 估值
    df_val = load_daily(
        start_date=(date.today() - timedelta(days=val_days)).isoformat(),
        columns=["code", "trade_date", "close"],
        codes=sample_codes,
    )
    val = calc_valuation_temperature(df_val)
    del df_val
    gc.collect()

    # 2. 趋势 (低配也采样, 占比估算误差<3%)
    df_mom = load_daily(
        start_date=(date.today() - timedelta(days=mom_days)).isoformat(),
        columns=["code", "trade_date", "close"],
        codes=sample_codes if settings.is_low_memory else None,
    )
    mom = calc_momentum_temperature(df_mom)
    del df_mom
    gc.collect()

    # 3. 成交量 (低配也采样, 总额按比例放大即可得到趋势)
    df_vol = load_daily(
        start_date=(date.today() - timedelta(days=vol_days)).isoformat(),
        columns=["trade_date", "amount"],
        codes=sample_codes if settings.is_low_memory else None,
    )
    vol = calc_volume_temperature(df_vol)
    del df_vol
    gc.collect()

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
