"""策略深度分析模块 — 全面验证策略有效性

包含:
1. 单因子有效性检验 (IC/ICIR/分层/衰减)
2. 因子相关性分析
3. 参数敏感性测试
4. 回测归因分析
5. 风险分析 (滚动回撤/月度分布/极端场景)
6. 样本内外分割验证
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field


# ══════════════════════════════════════════════════════════════
# 1. 单因子有效性检验
# ══════════════════════════════════════════════════════════════

def compute_single_factor_ic(
    df: pd.DataFrame,
    factor_col: str,
    hold_days: int = 20,
) -> dict:
    """
    计算单因子的IC/ICIR序列。

    IC = 因子值与未来收益的秩相关系数
    ICIR = IC均值 / IC标准差 (稳定性指标)

    Returns:
        {
            ic_series: [(date, ic), ...],
            ic_mean, ic_std, icir,
            positive_pct: IC>0的比例,
        }
    """
    df = df.sort_values(["code", "trade_date"]).copy()
    df["future_ret"] = df.groupby("code")["close"].shift(-hold_days) / df["close"] - 1

    all_dates = sorted(df["trade_date"].unique())
    sample_dates = all_dates[::hold_days]

    ic_series = []
    for d in sample_dates:
        cross = df[df["trade_date"] == d].dropna(subset=[factor_col, "future_ret"])
        cross = cross[cross["volume"] > 0]
        if len(cross) < 100:
            continue
        ic = cross[factor_col].corr(cross["future_ret"], method="spearman")
        if not np.isnan(ic):
            ic_series.append((d, ic))

    if not ic_series:
        return {"ic_series": [], "ic_mean": 0, "ic_std": 1, "icir": 0, "positive_pct": 0}

    ic_vals = [v for _, v in ic_series]
    ic_mean = np.mean(ic_vals)
    ic_std = np.std(ic_vals)
    icir = ic_mean / ic_std if ic_std > 0 else 0
    positive_pct = sum(1 for v in ic_vals if v > 0) / len(ic_vals) * 100

    return {
        "ic_series": ic_series,
        "ic_mean": round(ic_mean, 4),
        "ic_std": round(ic_std, 4),
        "icir": round(icir, 3),
        "positive_pct": round(positive_pct, 1),
    }


def compute_factor_quintile_returns(
    df: pd.DataFrame,
    factor_col: str,
    hold_days: int = 20,
    n_groups: int = 5,
    ascending: bool = True,
) -> dict:
    """
    计算因子分层收益（各组年化收益）。

    Returns:
        {
            group_annual_returns: {Q1: x%, Q2: y%, ...},
            spread: Q1-Q5年化差(多空超额),
            monotonic: 是否单调,
        }
    """
    df = df.sort_values(["code", "trade_date"]).copy()
    df["daily_ret"] = df.groupby("code")["close"].pct_change()

    all_dates = sorted(df["trade_date"].unique())
    rb_dates = all_dates[::hold_days]

    ret_pivot = df.pivot_table(index="trade_date", columns="code", values="daily_ret")

    group_navs = {f"Q{i+1}": [1.0] for i in range(n_groups)}

    for i in range(len(rb_dates) - 1):
        cross = df[df["trade_date"] == rb_dates[i]].dropna(subset=[factor_col])
        cross = cross[cross["volume"] > 0]
        if len(cross) < n_groups * 30:
            continue

        try:
            cross["group"] = pd.qcut(
                cross[factor_col], q=n_groups,
                labels=[f"Q{j+1}" for j in range(n_groups)],
                duplicates="drop"
            )
        except ValueError:
            continue

        hold = ret_pivot.loc[
            (ret_pivot.index > rb_dates[i]) & (ret_pivot.index <= rb_dates[i+1])
        ]
        if hold.empty:
            continue

        for g in range(n_groups):
            gn = f"Q{g+1}"
            codes = cross[cross["group"] == gn]["code"].tolist()
            valid = [c for c in codes if c in hold.columns]
            if valid:
                period_ret = (1 + hold[valid].mean(axis=1)).prod() - 1
            else:
                period_ret = 0
            group_navs[gn].append(group_navs[gn][-1] * (1 + period_ret))

    # 计算年化
    n_years = (len(rb_dates) - 1) * hold_days / 252
    if n_years <= 0:
        n_years = 1

    group_annual = {}
    for g in range(n_groups):
        gn = f"Q{g+1}"
        final = group_navs[gn][-1]
        group_annual[gn] = round((final ** (1 / n_years) - 1) * 100, 1)

    vals = list(group_annual.values())
    if ascending:
        spread = vals[0] - vals[-1]
        monotonic = all(vals[i] >= vals[i+1] for i in range(len(vals)-1))
    else:
        spread = vals[-1] - vals[0]
        monotonic = all(vals[i] <= vals[i+1] for i in range(len(vals)-1))

    return {
        "group_annual_returns": group_annual,
        "spread": round(spread, 1),
        "monotonic": monotonic,
        "n_years": round(n_years, 1),
    }


def analyze_all_factors(df: pd.DataFrame, hold_days: int = 20) -> dict:
    """
    对策略中使用的所有因子逐一检验。

    Returns:
        {factor_name: {ic_stats, quintile_stats, verdict}}
    """
    df = df.sort_values(["code", "trade_date"]).copy()
    df["daily_ret"] = df.groupby("code")["close"].pct_change()
    df["ret_20d"] = df.groupby("code")["close"].pct_change(20)
    df["ret_60d"] = df.groupby("code")["close"].pct_change(60)
    df["vol_20d"] = df.groupby("code")["daily_ret"].transform(lambda x: x.rolling(20).std())
    df["vol_5d_avg"] = df.groupby("code")["volume"].transform(lambda x: x.rolling(5).mean())
    df["vol_60d_avg"] = df.groupby("code")["volume"].transform(lambda x: x.rolling(60).mean())
    df["volume_ratio"] = df["vol_5d_avg"] / df["vol_60d_avg"].replace(0, np.nan)
    df["avg_amount_20d"] = df.groupby("code")["amount"].transform(lambda x: x.rolling(20).mean())

    factors = {
        "小市值(成交额)": {"col": "avg_amount_20d", "ascending": True},
        "20日反转": {"col": "ret_20d", "ascending": True},
        "60日动量": {"col": "ret_60d", "ascending": False},
        "20日低波": {"col": "vol_20d", "ascending": True},
        "缩量因子": {"col": "volume_ratio", "ascending": True},
    }

    results = {}
    for name, info in factors.items():
        col = info["col"]
        asc = info["ascending"]

        ic_stats = compute_single_factor_ic(df, col, hold_days)
        quintile_stats = compute_factor_quintile_returns(df, col, hold_days, ascending=asc)

        # 综合判定
        icir = abs(ic_stats["icir"])
        spread = abs(quintile_stats["spread"])
        if icir > 0.5 and spread > 10:
            verdict = "✅ 强有效"
        elif icir > 0.3 or spread > 5:
            verdict = "⚠️ 弱有效"
        else:
            verdict = "❌ 无效"

        results[name] = {
            "ic": ic_stats,
            "quintile": quintile_stats,
            "verdict": verdict,
            "ascending": asc,
        }

    return results


# ══════════════════════════════════════════════════════════════
# 2. 因子相关性分析
# ══════════════════════════════════════════════════════════════

def compute_factor_correlation(df: pd.DataFrame) -> pd.DataFrame:
    """
    计算各因子间的截面相关性（平均秩相关）。
    高相关（>0.5）的因子同时使用会冗余。

    Returns:
        相关性矩阵 DataFrame
    """
    df = df.sort_values(["code", "trade_date"]).copy()
    df["daily_ret"] = df.groupby("code")["close"].pct_change()
    df["ret_20d"] = df.groupby("code")["close"].pct_change(20)
    df["ret_60d"] = df.groupby("code")["close"].pct_change(60)
    df["vol_20d"] = df.groupby("code")["daily_ret"].transform(lambda x: x.rolling(20).std())
    df["vol_5d_avg"] = df.groupby("code")["volume"].transform(lambda x: x.rolling(5).mean())
    df["vol_60d_avg"] = df.groupby("code")["volume"].transform(lambda x: x.rolling(60).mean())
    df["volume_ratio"] = df["vol_5d_avg"] / df["vol_60d_avg"].replace(0, np.nan)
    df["avg_amount_20d"] = df.groupby("code")["amount"].transform(lambda x: x.rolling(20).mean())

    factor_cols = ["avg_amount_20d", "ret_20d", "ret_60d", "vol_20d", "volume_ratio"]
    factor_names = ["小市值", "反转", "动量", "低波", "缩量"]

    # 取最近一个日期的截面计算相关性
    latest = df["trade_date"].max()
    cross = df[df["trade_date"] == latest].dropna(subset=factor_cols)

    if len(cross) < 100:
        # 取倒数第20个日期
        dates = sorted(df["trade_date"].unique())
        if len(dates) > 20:
            cross = df[df["trade_date"] == dates[-20]].dropna(subset=factor_cols)

    if len(cross) < 50:
        return pd.DataFrame()

    corr = cross[factor_cols].corr(method="spearman")
    corr.index = factor_names
    corr.columns = factor_names
    return corr.round(3)


# ══════════════════════════════════════════════════════════════
# 3. 参数敏感性测试
# ══════════════════════════════════════════════════════════════

def run_sensitivity_test(
    df: pd.DataFrame,
    param_name: str = "hold_days",
    param_values: list = None,
) -> list[dict]:
    """
    测试改变单一参数时策略表现的变化。
    如果改参数±30%结果剧变（夏普从1.5变0.3）= 过拟合风险高。
    如果结果稳定变化 = 策略稳健。

    Returns:
        [{param_value, annual_return, sharpe, max_drawdown}, ...]
    """
    from src.strategy.smart_strategy import SmartStrategyConfig, run_smart_backtest

    if param_values is None:
        if param_name == "hold_days":
            param_values = [10, 15, 20, 25, 30, 40]
        elif param_name == "top_n":
            param_values = [15, 20, 30, 40, 50]
        elif param_name == "stock_stop_loss":
            param_values = [-0.10, -0.15, -0.20, -0.25, -0.30]
        else:
            param_values = [10, 20, 30, 40, 50]

    results = []
    for val in param_values:
        kwargs = {param_name: val}
        config = SmartStrategyConfig(**kwargs)
        bt = run_smart_backtest(df, config)
        results.append({
            "param_value": val,
            "annual_return": round(bt.annual_return, 1),
            "sharpe": round(bt.sharpe_ratio, 2),
            "max_drawdown": round(bt.max_drawdown, 1),
            "win_rate": round(bt.win_rate, 0),
        })

    return results


# ══════════════════════════════════════════════════════════════
# 4. 归因分析
# ══════════════════════════════════════════════════════════════

def compute_monthly_returns(strategy_nav: list, nav_dates: list) -> pd.DataFrame:
    """
    计算月度收益序列 + 统计特征。

    Returns:
        DataFrame: [year_month, monthly_return, cumulative]
    """
    if len(strategy_nav) < 20:
        return pd.DataFrame()

    nav_df = pd.DataFrame({
        "date": pd.to_datetime(nav_dates[:len(strategy_nav)]),
        "nav": strategy_nav,
    })
    nav_df["month"] = nav_df["date"].dt.to_period("M")

    monthly = nav_df.groupby("month").agg(
        start_nav=("nav", "first"),
        end_nav=("nav", "last"),
    )
    monthly["return_pct"] = (monthly["end_nav"] / monthly["start_nav"] - 1) * 100
    monthly = monthly.reset_index()
    monthly["month_str"] = monthly["month"].astype(str)

    return monthly[["month_str", "return_pct"]].rename(
        columns={"month_str": "月份", "return_pct": "月收益%"}
    )


def compute_drawdown_analysis(strategy_nav: list, nav_dates: list) -> dict:
    """
    回撤深度分析。

    Returns:
        {
            rolling_drawdown: [(date, dd%), ...],
            worst_drawdowns: [{start, end, depth, days}, ...] top5,
            underwater_days: 在水下(亏损状态)的天数占比,
        }
    """
    nav = np.array(strategy_nav)
    dates = nav_dates[:len(nav)]
    cummax = np.maximum.accumulate(nav)
    drawdown = (nav - cummax) / cummax

    # 滚动回撤序列
    rolling_dd = [(dates[i], round(drawdown[i] * 100, 2)) for i in range(len(dates))]

    # 找top5回撤事件
    events = []
    in_dd = False
    dd_start = 0
    for i in range(len(drawdown)):
        if drawdown[i] < -0.05 and not in_dd:
            in_dd = True
            dd_start = np.argmax(nav[:i+1])
        elif drawdown[i] >= 0 and in_dd:
            in_dd = False
            dd_bottom = np.argmin(drawdown[dd_start:i]) + dd_start
            events.append({
                "start": str(dates[dd_start])[:10],
                "bottom": str(dates[dd_bottom])[:10],
                "end": str(dates[i])[:10],
                "depth": round(drawdown[dd_bottom] * 100, 1),
                "days": i - dd_start,
            })

    if in_dd:
        dd_bottom = np.argmin(drawdown[dd_start:]) + dd_start
        events.append({
            "start": str(dates[dd_start])[:10],
            "bottom": str(dates[dd_bottom])[:10],
            "end": "至今",
            "depth": round(drawdown[dd_bottom] * 100, 1),
            "days": len(dates) - dd_start,
        })

    events.sort(key=lambda x: x["depth"])
    worst5 = events[:5]

    # 水下天数占比
    underwater_pct = round((drawdown < -0.01).sum() / len(drawdown) * 100, 1)

    return {
        "rolling_drawdown": rolling_dd,
        "worst_drawdowns": worst5,
        "underwater_pct": underwater_pct,
    }


# ══════════════════════════════════════════════════════════════
# 5. 样本内外验证
# ══════════════════════════════════════════════════════════════

def run_in_out_sample_test(
    df: pd.DataFrame,
    split_ratio: float = 0.7,
) -> dict:
    """
    样本内/外分割验证。
    用前70%数据的逻辑，在后30%数据上验证。

    如果样本外表现远差于样本内 = 过拟合。
    如果两者接近 = 策略稳健。

    Returns:
        {
            in_sample: {annual_return, sharpe, max_drawdown},
            out_sample: {annual_return, sharpe, max_drawdown},
            degradation: 样本外相对样本内的衰减比例,
        }
    """
    from src.strategy.smart_strategy import SmartStrategyConfig, run_smart_backtest

    all_dates = sorted(df["trade_date"].unique())
    split_idx = int(len(all_dates) * split_ratio)
    split_date = all_dates[split_idx]

    df_in = df[df["trade_date"] <= split_date].copy()
    df_out = df[df["trade_date"] > split_date].copy()

    config = SmartStrategyConfig()

    # 样本内
    r_in = run_smart_backtest(df_in, config)
    # 样本外
    r_out = run_smart_backtest(df_out, config)

    in_sample = {
        "period": f"{str(all_dates[0])[:10]} ~ {str(split_date)[:10]}",
        "annual_return": round(r_in.annual_return, 1),
        "sharpe": round(r_in.sharpe_ratio, 2),
        "max_drawdown": round(r_in.max_drawdown, 1),
    }
    out_sample = {
        "period": f"{str(split_date)[:10]} ~ {str(all_dates[-1])[:10]}",
        "annual_return": round(r_out.annual_return, 1),
        "sharpe": round(r_out.sharpe_ratio, 2),
        "max_drawdown": round(r_out.max_drawdown, 1),
    }

    # 衰减率
    if r_in.sharpe_ratio > 0:
        degradation = round((1 - r_out.sharpe_ratio / r_in.sharpe_ratio) * 100, 0)
    else:
        degradation = 0

    return {
        "in_sample": in_sample,
        "out_sample": out_sample,
        "degradation_pct": degradation,
        "split_date": str(split_date)[:10],
    }
