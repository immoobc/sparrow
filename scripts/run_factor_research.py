"""因子研究脚本 — 20日反转因子回测

完整的量化因子研究流程:
1. 从数据库快速加载历史行情
2. 计算因子值（过去20日收益率）
3. 计算IC/ICIR（评估因子预测能力）
4. 分层回测（验证因子能否赚钱）
5. 输出报告

用法: python -m scripts.run_factor_research
"""

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd

from src.config import settings
from src.logger import logger


def fast_load_data(start_date="2023-01-01", end_date="2025-12-31") -> pd.DataFrame:
    """从 Parquet 缓存快速加载行情"""
    from src.storage.cache import load_daily

    logger.info(f"加载行情: {start_date} ~ {end_date}")
    t0 = time.time()
    df = load_daily(start_date, end_date)

    # 过滤数据量不足的股票
    counts = df.groupby("code").size()
    valid = counts[counts >= 60].index
    df = df[df["code"].isin(valid)]

    logger.info(f"加载完成: {df['code'].nunique()}只, {len(df):,}条, {time.time()-t0:.1f}秒")
    return df


def calc_factor_and_future(df: pd.DataFrame, lookback=20, hold_days=20):
    """一次遍历同时算因子值和未来收益（高效）"""
    logger.info(f"计算因子(回看{lookback}日) + 未来收益({hold_days}日)...")
    t0 = time.time()

    df = df.sort_values(["code", "trade_date"]).copy()
    df["factor_value"] = df.groupby("code")["close"].pct_change(lookback)
    df["future_ret"] = df.groupby("code")["close"].shift(-hold_days) / df["close"] - 1

    # 只保留有效行
    valid = df.dropna(subset=["factor_value", "future_ret"]).copy()
    logger.info(f"因子计算完成: {valid['code'].nunique()}只, {len(valid):,}条, {time.time()-t0:.1f}秒")
    return valid


def calc_ic(df: pd.DataFrame, sample_every=5):
    """计算IC序列（每N天取一个截面）"""
    logger.info("计算IC...")
    dates = sorted(df["trade_date"].unique())[::sample_every]

    ic_list = []
    for dt in dates:
        cross = df[df["trade_date"] == dt]
        if len(cross) < 50:
            continue
        rank_ic = cross["factor_value"].corr(cross["future_ret"], method="spearman")
        ic_list.append({"trade_date": dt, "rank_ic": rank_ic})

    ic_df = pd.DataFrame(ic_list)
    return ic_df


def quantile_backtest(df: pd.DataFrame, n_groups=5, rebalance_every=20):
    """分层回测"""
    logger.info(f"分层回测: {n_groups}组, 每{rebalance_every}天调仓")
    t0 = time.time()

    # 计算日收益率
    df = df.sort_values(["code", "trade_date"]).copy()
    df["daily_ret"] = df.groupby("code")["close"].pct_change()

    # 调仓日列表
    all_dates = sorted(df["trade_date"].unique())
    rebalance_dates = all_dates[::rebalance_every]

    # 日收益率 pivot table
    ret_pivot = df.pivot_table(index="trade_date", columns="code", values="daily_ret")

    group_nav = {f"Q{i+1}": [1.0] for i in range(n_groups)}
    nav_dates = [rebalance_dates[0]]

    for idx in range(len(rebalance_dates) - 1):
        rb_date = rebalance_dates[idx]
        next_rb = rebalance_dates[idx + 1]

        # 当日截面
        cross = df[df["trade_date"] == rb_date][["code", "factor_value"]].dropna()
        if len(cross) < n_groups * 20:
            for g in group_nav:
                group_nav[g].append(group_nav[g][-1])
            nav_dates.append(next_rb)
            continue

        # 分组
        try:
            cross["group"] = pd.qcut(
                cross["factor_value"], q=n_groups,
                labels=[f"Q{i+1}" for i in range(n_groups)],
                duplicates="drop"
            )
        except ValueError:
            for g in group_nav:
                group_nav[g].append(group_nav[g][-1])
            nav_dates.append(next_rb)
            continue

        # 持仓期收益
        hold_period = ret_pivot.loc[
            (ret_pivot.index > rb_date) & (ret_pivot.index <= next_rb)
        ]

        for g_idx in range(n_groups):
            g_name = f"Q{g_idx+1}"
            g_codes = cross[cross["group"] == g_name]["code"].tolist()
            valid_codes = [c for c in g_codes if c in hold_period.columns]

            if valid_codes and not hold_period.empty:
                period_ret = hold_period[valid_codes].mean(axis=1)
                cum_ret = (1 + period_ret).prod() - 1
            else:
                cum_ret = 0

            group_nav[g_name].append(group_nav[g_name][-1] * (1 + cum_ret))

        nav_dates.append(next_rb)

    logger.info(f"回测完成, {time.time()-t0:.1f}秒")

    # 计算指标
    n_years = (nav_dates[-1] - nav_dates[0]).days / 365.25 if len(nav_dates) > 1 else 1

    results = {}
    for g in group_nav:
        final_nav = group_nav[g][-1]
        ann_ret = (final_nav ** (1 / max(n_years, 0.1)) - 1) * 100
        rets = pd.Series(group_nav[g]).pct_change().dropna()
        sharpe = (rets.mean() / rets.std() * np.sqrt(12)) if rets.std() > 0 else 0
        max_dd = (pd.Series(group_nav[g]) / pd.Series(group_nav[g]).cummax() - 1).min() * 100
        results[g] = {
            "annual_ret": round(ann_ret, 2),
            "sharpe": round(sharpe, 2),
            "max_drawdown": round(max_dd, 2),
            "final_nav": round(final_nav, 4),
        }

    return results, n_years, len(rebalance_dates)


def main():
    print("\n" + "=" * 60)
    print("  Sparrow 量化因子研究 — 20日反转因子")
    print("=" * 60)
    print()
    print("  假设: 过去20天涨幅最大的股票, 未来20天倾向于跑输")
    print("        过去20天跌幅最大的股票, 未来20天倾向于反弹")
    print()

    # Step 1: 加载数据
    df = fast_load_data("2023-01-01", "2025-12-31")
    if df.empty:
        print("无数据!")
        return

    # Step 2: 计算因子 + 未来收益
    valid_df = calc_factor_and_future(df, lookback=20, hold_days=20)

    # Step 3: IC 分析
    ic_df = calc_ic(valid_df, sample_every=5)
    rank_ic = ic_df["rank_ic"]

    ic_mean = rank_ic.mean()
    ic_std = rank_ic.std()
    icir = ic_mean / ic_std if ic_std > 0 else 0
    ic_pos_pct = (rank_ic < 0).mean() * 100  # 反转因子IC应为负

    # Step 4: 分层回测
    bt_results, n_years, n_rebalance = quantile_backtest(valid_df, n_groups=5, rebalance_every=20)

    # Step 5: 报告
    print("\n" + "─" * 60)
    print("  IC 分析 (因子预测能力)")
    print("─" * 60)
    print(f"  Rank IC 均值:     {ic_mean:.4f}")
    print(f"  Rank IC 标准差:   {ic_std:.4f}")
    print(f"  ICIR:             {icir:.4f}")
    print(f"  IC<0 占比:        {ic_pos_pct:.1f}% (反转因子应>50%)")
    print(f"  截面期数:         {len(ic_df)}")

    if abs(icir) > 0.5:
        print("  判断:             ✓ 因子有效且稳定")
    elif abs(icir) > 0.3:
        print("  判断:             △ 有一定效果")
    else:
        print("  判断:             ✗ 效果不显著")

    print(f"\n{'─'*60}")
    print(f"  分层回测 ({n_years:.1f}年, {n_rebalance}次调仓, 5组等权)")
    print("─" * 60)
    print(f"  {'组别':6s} {'年化收益':>10s} {'夏普比率':>10s} {'最大回撤':>10s} {'终值净值':>10s}")
    print("  " + "-" * 50)

    for g in sorted(bt_results.keys()):
        r = bt_results[g]
        label = ""
        if g == "Q1":
            label = " ← 做多(过去跌最多)"
        elif g == "Q5":
            label = " ← 做空(过去涨最多)"
        print(f"  {g:6s} {r['annual_ret']:>9.2f}% {r['sharpe']:>9.2f} {r['max_drawdown']:>9.2f}% {r['final_nav']:>10.4f}{label}")

    # 多空
    ls_ret = bt_results["Q1"]["annual_ret"] - bt_results["Q5"]["annual_ret"]
    print(f"\n  多空年化收益 (Q1 - Q5): {ls_ret:.2f}%")

    # 单调性
    rets = [bt_results[f"Q{i+1}"]["annual_ret"] for i in range(5)]
    is_mono = all(rets[i] >= rets[i+1] for i in range(4))
    print(f"  收益单调性:       {'✓ 完美单调' if is_mono else '△ 非严格单调'}")

    print("\n" + "─" * 60)
    print("  结论")
    print("─" * 60)
    if ls_ret > 5 and abs(icir) > 0.3:
        print("  ✓ 20日反转因子在A股有效!")
        print(f"    每20天调仓, 买入过去跌最多的一组, 年化超额 ~{ls_ret:.0f}%")
        print("    下一步: 加入市值中性约束 + 行业中性, 构建实盘组合")
    elif ls_ret > 0:
        print("  △ 因子有一定效果但不够强")
        print("    建议: 尝试不同回看期(5/10/60日), 或与其他因子复合")
    else:
        print("  ✗ 在此时间段因子无效")
        print("    可能原因: 市场环境变化, 需检查子区间表现")

    print("=" * 60 + "\n")


if __name__ == "__main__":
    main()
