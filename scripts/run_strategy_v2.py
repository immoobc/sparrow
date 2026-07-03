"""策略V2 — 加入实战约束的20日反转策略

V1的问题:
- 换手太高 → 加入持仓延续规则(已有持仓不轻易卖)
- 回撤太大 → 加入市值过滤(剔除小市值垃圾股)
- 无超额 → 组合因子(反转+波动率)

V2优化:
1. 市值过滤: 只在市值前2000名中选股 (剔除微盘股/壳)
2. 换手控制: 新组合与旧组合重叠度>40%才强制换仓
3. 持仓分散: Top 30 持仓 (更分散)
4. 调仓频率: 10天 (更灵活)

用法: python -m scripts.run_strategy_v2
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

from src.storage.cache import load_daily
from src.logger import logger


def run_v2_backtest(
    df: pd.DataFrame,
    start_date: str = "2023-01-01",
    end_date: str = "2025-12-31",
    lookback: int = 20,
    hold_days: int = 10,
    top_n: int = 30,
    mcap_top: int = 2000,
    commission: float = 0.0015,
    slippage: float = 0.001,
):
    """
    V2 策略回测: 市值过滤 + 反转因子 + 波动率约束

    改进点:
    - 只在市值前2000名中选股 (用成交额代理市值)
    - 排除近20日波动率最高的10% (避免暴涨暴跌股)
    - 换手控制: 已在组合中且因子排名仍在前100的不卖
    """
    t0 = time.time()
    logger.info(f"V2策略回测: {start_date}~{end_date}")

    df = df.sort_values(["code", "trade_date"]).copy()

    # 计算因子和辅助指标
    df["daily_ret"] = df.groupby("code")["close"].pct_change()
    df["factor"] = df.groupby("code")["close"].pct_change(lookback)
    df["volatility"] = df.groupby("code")["daily_ret"].transform(
        lambda x: x.rolling(lookback).std()
    )
    # 用20日平均成交额代理市值排名 (大票成交额高)
    df["avg_amount"] = df.groupby("code")["amount"].transform(
        lambda x: x.rolling(lookback).mean()
    )

    # 过滤日期
    df_bt = df[
        (df["trade_date"] >= pd.Timestamp(start_date)) &
        (df["trade_date"] <= pd.Timestamp(end_date))
    ].copy()

    all_dates = sorted(df_bt["trade_date"].unique())
    rebalance_dates = all_dates[::hold_days]

    # 日收益率矩阵
    ret_pivot = df_bt.pivot_table(index="trade_date", columns="code", values="daily_ret")

    # 回测
    portfolio_nav = [1.0]
    benchmark_nav = [1.0]
    nav_dates = [all_dates[0]]
    holdings = set()
    total_cost = 0
    period_returns = []

    for i in range(len(rebalance_dates) - 1):
        rb_date = rebalance_dates[i]
        next_rb = rebalance_dates[i + 1]

        # 当日截面
        cross = df_bt[df_bt["trade_date"] == rb_date].dropna(
            subset=["factor", "volatility", "avg_amount"]
        ).copy()

        # 过滤
        cross = cross[cross["volume"] > 0]                    # 停牌
        cross = cross[cross["daily_ret"].abs() < 0.095]       # 涨跌停

        if len(cross) < top_n * 3:
            continue

        # 市值过滤: 只保留成交额前N名
        cross = cross.nlargest(mcap_top, "avg_amount")

        # 排除波动率最高的10%
        vol_threshold = cross["volatility"].quantile(0.9)
        cross = cross[cross["volatility"] <= vol_threshold]

        # 按因子排序选股 (反转: 选过去跌最多的)
        cross = cross.sort_values("factor", ascending=True)

        # 换手控制: 已持仓且排名在前100的保留
        keep_codes = set()
        if holdings:
            still_good = cross.head(top_n * 3)["code"].tolist()
            keep_codes = holdings & set(still_good)

        # 新选: 因子排名最前的, 优先保留旧持仓
        new_from_top = cross[~cross["code"].isin(keep_codes)].head(
            top_n - len(keep_codes)
        )["code"].tolist()

        new_holdings = set(list(keep_codes) + new_from_top)
        if len(new_holdings) < 10:
            continue

        # 换手率
        if holdings:
            turnover = len(new_holdings.symmetric_difference(holdings)) / (2 * max(len(holdings), len(new_holdings)))
        else:
            turnover = 1.0
        cost = turnover * (commission * 2 + slippage * 2)
        total_cost += cost

        # 持仓期收益 (等权)
        hold_period = ret_pivot.loc[
            (ret_pivot.index > rb_date) & (ret_pivot.index <= next_rb)
        ]

        port_codes = [c for c in new_holdings if c in hold_period.columns]
        if not port_codes or hold_period.empty:
            holdings = new_holdings
            continue

        port_daily_ret = hold_period[port_codes].mean(axis=1)
        port_daily_ret.iloc[0] -= cost  # 扣成本

        bm_daily_ret = hold_period.mean(axis=1)

        for dt, ret in port_daily_ret.items():
            portfolio_nav.append(portfolio_nav[-1] * (1 + ret))
            nav_dates.append(dt)

        for dt, ret in bm_daily_ret.items():
            benchmark_nav.append(benchmark_nav[-1] * (1 + ret))

        period_returns.append((1 + port_daily_ret).prod() - 1)
        holdings = new_holdings

    # 绩效计算
    n_days = len(portfolio_nav) - 1
    n_years = n_days / 252 if n_days > 0 else 1

    final_nav = portfolio_nav[-1]
    annual_ret = (final_nav ** (1/n_years) - 1) * 100

    daily_rets = pd.Series(portfolio_nav).pct_change().dropna()
    annual_vol = daily_rets.std() * np.sqrt(252) * 100
    sharpe = (daily_rets.mean() - 0.02/252) / daily_rets.std() * np.sqrt(252) if daily_rets.std() > 0 else 0

    nav_s = pd.Series(portfolio_nav)
    max_dd = ((nav_s / nav_s.cummax()) - 1).min() * 100

    bm_final = benchmark_nav[-1] if len(benchmark_nav) > 1 else 1
    bm_annual = (bm_final ** (1/n_years) - 1) * 100
    excess = annual_ret - bm_annual

    win_rate = sum(1 for r in period_returns if r > 0) / max(len(period_returns), 1) * 100

    elapsed = time.time() - t0
    logger.info(f"V2回测完成, {elapsed:.1f}秒")

    return {
        "annual_return": round(annual_ret, 2),
        "annual_vol": round(annual_vol, 2),
        "sharpe": round(sharpe, 2),
        "max_drawdown": round(max_dd, 2),
        "calmar": round(annual_ret / abs(max_dd), 2) if max_dd != 0 else 0,
        "excess_return": round(excess, 2),
        "win_rate": round(win_rate, 1),
        "total_cost": round(total_cost * 100, 2),
        "total_trades": len(rebalance_dates) - 1,
        "final_nav": round(final_nav, 4),
        "bm_nav": round(bm_final, 4),
        "n_years": round(n_years, 1),
        "nav": portfolio_nav,
        "nav_dates": nav_dates,
    }


def main():
    # 加载数据
    logger.info("加载数据...")
    df = load_daily("2022-06-01", "2025-12-31")
    logger.info(f"数据: {df['code'].nunique()}只, {len(df):,}条")

    # V2 策略
    result = run_v2_backtest(
        df,
        start_date="2023-01-01",
        end_date="2025-12-31",
        lookback=20,
        hold_days=10,
        top_n=30,
        mcap_top=2000,
    )

    # 报告
    print("\n" + "=" * 60)
    print("  Sparrow 策略回测报告 V2")
    print("  20日反转 + 市值过滤 + 换手控制")
    print("=" * 60)

    print(f"\n{'─'*60}")
    print("  策略参数")
    print(f"{'─'*60}")
    print(f"  回测区间:     2023-01-01 ~ 2025-12-31 ({result['n_years']}年)")
    print(f"  股票池:       成交额前2000名 + 剔除高波动10%")
    print(f"  选股:         反转因子Top30 + 旧持仓延续")
    print(f"  调仓:         每10个交易日")

    print(f"\n{'─'*60}")
    print("  绩效指标")
    print(f"{'─'*60}")
    print(f"  年化收益:       {result['annual_return']:>+8.2f}%")
    print(f"  年化波动率:     {result['annual_vol']:>8.2f}%")
    print(f"  夏普比率:       {result['sharpe']:>8.2f}")
    print(f"  Calmar比率:     {result['calmar']:>8.2f}")
    print(f"  最大回撤:       {result['max_drawdown']:>8.2f}%")
    print(f"  胜率:           {result['win_rate']:>8.1f}%")
    print(f"  超额收益:       {result['excess_return']:>+8.2f}% (vs 全市场等权)")
    print(f"  累计成本:       {result['total_cost']:>8.2f}%")
    print(f"  策略终值:       {result['final_nav']}")
    print(f"  基准终值:       {result['bm_nav']}")

    print(f"\n{'─'*60}")
    print("  对比V1 (裸反转因子)")
    print(f"{'─'*60}")
    print(f"  {'指标':12s} {'V1':>10s} {'V2':>10s} {'改善':>10s}")
    print(f"  {'-'*42}")
    # V1 数据
    v1 = {"ret": 8.78, "dd": -44.24, "sharpe": 0.36, "cost": 17.47}
    v2 = result
    print(f"  {'年化收益':12s} {v1['ret']:>+9.2f}% {v2['annual_return']:>+9.2f}% {v2['annual_return']-v1['ret']:>+9.2f}%")
    print(f"  {'最大回撤':12s} {v1['dd']:>9.2f}% {v2['max_drawdown']:>9.2f}% {v2['max_drawdown']-v1['dd']:>+9.2f}%")
    print(f"  {'夏普比率':12s} {v1['sharpe']:>9.2f}  {v2['sharpe']:>9.2f}  {v2['sharpe']-v1['sharpe']:>+9.2f}")
    print(f"  {'累计成本':12s} {v1['cost']:>9.2f}% {v2['total_cost']:>9.2f}% {v2['total_cost']-v1['cost']:>+9.2f}%")

    print("\n" + "=" * 60 + "\n")


if __name__ == "__main__":
    main()
