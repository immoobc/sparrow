"""分层回测引擎 — 验证因子有效性的核心工具

分层回测逻辑:
1. 每个调仓日，按因子值排序所有股票
2. 分成5组(quintile): Q1=因子值最低, Q5=因子值最高
3. 假设等权持有每组，计算每组未来N日的收益
4. 如果Q1持续跑赢Q5（反转因子），说明因子有效

核心指标:
- IC (Information Coefficient): 因子值与未来收益的截面相关系数
- IR (Information Ratio): IC的均值/标准差，>0.5算不错
- 多空收益: Q1(做多) - Q5(做空)的年化收益
"""

import numpy as np
import pandas as pd

from src.logger import logger


def factor_ic_analysis(factor_df: pd.DataFrame, future_ret_df: pd.DataFrame) -> pd.DataFrame:
    """
    计算因子IC序列（每个截面日的秩相关系数）。

    Args:
        factor_df: [code, trade_date, factor_value]
        future_ret_df: [code, trade_date, future_ret]

    Returns:
        DataFrame[trade_date, ic, rank_ic]
    """
    # 合并因子值和未来收益
    merged = factor_df.merge(future_ret_df, on=["code", "trade_date"], how="inner")

    ic_list = []
    for dt, group in merged.groupby("trade_date"):
        if len(group) < 30:  # 截面股票数太少，跳过
            continue
        # Rank IC (Spearman相关系数)
        rank_ic = group["factor_value"].corr(group["future_ret"], method="spearman")
        # Pearson IC
        ic = group["factor_value"].corr(group["future_ret"])
        ic_list.append({"trade_date": dt, "ic": ic, "rank_ic": rank_ic})

    ic_df = pd.DataFrame(ic_list)
    return ic_df


def ic_summary(ic_df: pd.DataFrame) -> dict:
    """
    IC 统计摘要。

    关键判断标准:
    - IC均值绝对值 > 0.03: 因子有预测能力
    - ICIR绝对值 > 0.5: 因子稳定有效
    - IC > 0 占比 > 60%: 方向一致性好
    """
    if ic_df.empty:
        return {}

    rank_ic = ic_df["rank_ic"]
    return {
        "ic_mean": round(rank_ic.mean(), 4),
        "ic_std": round(rank_ic.std(), 4),
        "icir": round(rank_ic.mean() / rank_ic.std(), 4) if rank_ic.std() > 0 else 0,
        "ic_positive_pct": round((rank_ic > 0).mean() * 100, 1),
        "ic_abs_gt003": round((rank_ic.abs() > 0.03).mean() * 100, 1),
        "count": len(ic_df),
    }


def quantile_backtest(
    factor_df: pd.DataFrame,
    daily_df: pd.DataFrame,
    n_groups: int = 5,
    hold_days: int = 20,
    rebalance_freq: int = 20,
) -> dict:
    """
    分层回测: 按因子值分组，计算每组的收益表现。

    Args:
        factor_df: [code, trade_date, factor_value]
        daily_df: 全市场日K线
        n_groups: 分组数（默认5组）
        hold_days: 持仓天数
        rebalance_freq: 调仓频率（交易日）

    Returns:
        {
            group_returns: DataFrame[日期, Q1~Q5的累计收益],
            annual_returns: {Q1: xx%, Q2: ...},
            long_short: 多空年化收益,
            sharpe: 各组夏普比率,
        }
    """
    logger.info(f"分层回测: {n_groups}组, 持仓{hold_days}天, 调仓{rebalance_freq}天")

    # 获取所有调仓日（每隔rebalance_freq个交易日）
    all_dates = sorted(factor_df["trade_date"].unique())
    rebalance_dates = all_dates[::rebalance_freq]

    # 预计算: 每只股票每天的收益率
    daily_df = daily_df.sort_values(["code", "trade_date"]).copy()
    daily_df["daily_ret"] = daily_df.groupby("code")["close"].pct_change()

    # 构建日期→收益率的 pivot
    ret_pivot = daily_df.pivot(index="trade_date", columns="code", values="daily_ret")

    group_nav = {f"Q{i+1}": [1.0] for i in range(n_groups)}  # 每组净值曲线
    nav_dates = [rebalance_dates[0]]

    for idx in range(len(rebalance_dates) - 1):
        rb_date = rebalance_dates[idx]
        next_rb = rebalance_dates[idx + 1]

        # 获取当日因子截面
        cross_section = factor_df[factor_df["trade_date"] == rb_date].copy()
        if len(cross_section) < n_groups * 10:
            continue

        # 按因子值分组
        cross_section["group"] = pd.qcut(
            cross_section["factor_value"],
            q=n_groups,
            labels=[f"Q{i+1}" for i in range(n_groups)],
            duplicates="drop",
        )

        # 计算每组在持仓期内的等权收益
        hold_period = ret_pivot.loc[
            (ret_pivot.index > rb_date) & (ret_pivot.index <= next_rb)
        ]

        for g in range(n_groups):
            g_name = f"Q{g+1}"
            g_codes = cross_section[cross_section["group"] == g_name]["code"].tolist()

            if not g_codes or hold_period.empty:
                group_nav[g_name].append(group_nav[g_name][-1])
                continue

            # 等权组合在持仓期的累计收益
            valid_codes = [c for c in g_codes if c in hold_period.columns]
            if not valid_codes:
                group_nav[g_name].append(group_nav[g_name][-1])
                continue

            period_ret = hold_period[valid_codes].mean(axis=1)
            cum_ret = (1 + period_ret).prod() - 1
            group_nav[g_name].append(group_nav[g_name][-1] * (1 + cum_ret))

        nav_dates.append(next_rb)

    # 计算绩效指标
    nav_df = pd.DataFrame(group_nav, index=nav_dates[:len(group_nav["Q1"])])
    n_years = (nav_dates[-1] - nav_dates[0]).days / 365.25 if len(nav_dates) > 1 else 1

    annual_returns = {}
    sharpe_ratios = {}
    for g in group_nav:
        final_nav = group_nav[g][-1]
        ann_ret = (final_nav ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
        annual_returns[g] = round(ann_ret, 2)

        # 夏普比率（假设无风险利率2%）
        rets = pd.Series(group_nav[g]).pct_change().dropna()
        if rets.std() > 0:
            sharpe = (rets.mean() - 0.02/252) / rets.std() * np.sqrt(252)
            sharpe_ratios[g] = round(sharpe, 2)
        else:
            sharpe_ratios[g] = 0

    # 多空收益 = Q1(做多) - Q5(做空)
    long_short = annual_returns.get("Q1", 0) - annual_returns.get(f"Q{n_groups}", 0)

    return {
        "nav_df": nav_df,
        "annual_returns": annual_returns,
        "long_short_annual": round(long_short, 2),
        "sharpe": sharpe_ratios,
        "n_years": round(n_years, 1),
        "n_rebalance": len(rebalance_dates),
    }


def print_backtest_report(ic_stats: dict, bt_result: dict, factor_name: str = "因子"):
    """打印完整的回测报告"""
    print("\n" + "=" * 60)
    print(f"  {factor_name} — 回测报告")
    print("=" * 60)

    print(f"\n{'─'*60}")
    print("  IC 分析 (因子预测能力)")
    print(f"{'─'*60}")
    print(f"  Rank IC 均值:     {ic_stats.get('ic_mean', 'N/A')}")
    print(f"  Rank IC 标准差:   {ic_stats.get('ic_std', 'N/A')}")
    print(f"  ICIR:             {ic_stats.get('icir', 'N/A')}")
    print(f"  IC>0 占比:        {ic_stats.get('ic_positive_pct', 'N/A')}%")
    print(f"  截面期数:         {ic_stats.get('count', 'N/A')}")

    # IC判断
    icir = ic_stats.get("icir", 0)
    if abs(icir) > 0.5:
        verdict = "✓ 因子有效且稳定"
    elif abs(icir) > 0.3:
        verdict = "△ 因子有一定效果，但不太稳定"
    else:
        verdict = "✗ 因子效果不显著"
    print(f"  判断:             {verdict}")

    print(f"\n{'─'*60}")
    print("  分层回测 (分5组，等权持仓)")
    print(f"{'─'*60}")
    print(f"  回测年数:         {bt_result.get('n_years', 'N/A')} 年")
    print(f"  调仓次数:         {bt_result.get('n_rebalance', 'N/A')} 次")
    print()
    print("  组别     年化收益     夏普比率")
    print("  " + "-" * 35)
    for g in sorted(bt_result.get("annual_returns", {}).keys()):
        ann = bt_result["annual_returns"][g]
        sharpe = bt_result.get("sharpe", {}).get(g, 0)
        marker = " ←最低因子值(做多)" if g == "Q1" else (" ←最高因子值(做空)" if g == "Q5" else "")
        print(f"  {g}     {ann:>8.2f}%    {sharpe:>6.2f}{marker}")

    print(f"\n  多空年化收益(Q1-Q5): {bt_result.get('long_short_annual', 'N/A')}%")

    # 单调性判断
    rets = bt_result.get("annual_returns", {})
    if rets:
        vals = [rets[k] for k in sorted(rets.keys())]
        is_monotone = all(vals[i] >= vals[i+1] for i in range(len(vals)-1))
        if is_monotone:
            print("  单调性:           ✓ 完美单调递减（因子越小，收益越高）")
        else:
            print("  单调性:           △ 非严格单调，但趋势存在")

    print("=" * 60 + "\n")
