"""完整策略回测 — 20日反转选股策略

流程:
1. 加载行情数据 (Parquet, <1秒)
2. 运行策略回测 (模拟交易, 含成本)
3. 输出绩效报告

用法: python -m scripts.run_strategy
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from src.storage.cache import load_daily
from src.strategy.strategy_backtest import BacktestConfig, run_strategy_backtest
from src.logger import logger


def print_strategy_report(result, config):
    """打印策略绩效报告"""
    print("\n" + "=" * 60)
    print("  Sparrow 策略回测报告")
    print("  20日反转选股策略")
    print("=" * 60)

    print(f"\n{'─'*60}")
    print("  策略参数")
    print(f"{'─'*60}")
    print(f"  回测区间:     {config.start_date} ~ {config.end_date}")
    print(f"  因子:         过去{config.lookback}日收益率 (反转)")
    print(f"  持仓数:       Top {config.top_n} (因子最低=过去跌最多)")
    print(f"  调仓频率:     每 {config.hold_days} 个交易日")
    print(f"  权重:         {config.weight_method}")
    print(f"  交易成本:     单边 {config.commission*100:.2f}% + 滑点 {config.slippage*100:.2f}%")

    print(f"\n{'─'*60}")
    print("  收益指标")
    print(f"{'─'*60}")
    print(f"  年化收益:     {result.annual_return:>8.2f}%")
    print(f"  年化波动率:   {result.annual_volatility:>8.2f}%")
    print(f"  夏普比率:     {result.sharpe_ratio:>8.2f}")
    print(f"  Calmar比率:   {result.calmar_ratio:>8.2f}")
    print(f"  超额收益(vs等权): {result.excess_return:>+.2f}%")

    print(f"\n{'─'*60}")
    print("  风险指标")
    print(f"{'─'*60}")
    print(f"  最大回撤:     {result.max_drawdown:>8.2f}%")
    print(f"  回撤区间:     {result.max_dd_start} ~ {result.max_dd_end}")

    print(f"\n{'─'*60}")
    print("  交易统计")
    print(f"{'─'*60}")
    print(f"  调仓次数:     {result.total_trades}")
    print(f"  胜率:         {result.win_rate:.1f}%")
    print(f"  平均换手:     {result.avg_turnover:.1f}%")
    print(f"  累计成本:     {result.total_cost:.2f}%")

    print(f"\n{'─'*60}")
    print("  综合评价")
    print(f"{'─'*60}")

    # 评分
    scores = []
    if result.sharpe_ratio > 1.0:
        scores.append("✓ 夏普>1, 风险收益比优秀")
    elif result.sharpe_ratio > 0.5:
        scores.append("△ 夏普0.5-1, 可接受")
    else:
        scores.append("✗ 夏普<0.5, 风险收益比不佳")

    if result.max_drawdown > -25:
        scores.append("✓ 最大回撤可控(<25%)")
    elif result.max_drawdown > -35:
        scores.append("△ 回撤较大(25-35%)")
    else:
        scores.append("✗ 回撤过大(>35%)")

    if result.excess_return > 5:
        scores.append(f"✓ 超额收益显著(+{result.excess_return:.1f}%)")
    elif result.excess_return > 0:
        scores.append(f"△ 有超额但不大(+{result.excess_return:.1f}%)")
    else:
        scores.append(f"✗ 无超额收益({result.excess_return:.1f}%)")

    if result.win_rate > 55:
        scores.append(f"✓ 胜率{result.win_rate:.0f}%, 稳定性好")

    for s in scores:
        print(f"  {s}")

    print("\n" + "=" * 60)

    # 净值曲线概要
    if result.nav is not None:
        print(f"\n  净值曲线 (起点=1.0):")
        nav = result.nav
        n = len(nav)
        checkpoints = [0, n//4, n//2, 3*n//4, n-1]
        for idx in checkpoints:
            if idx < n:
                d = nav.index[idx]
                v = nav.iloc[idx]
                print(f"    {str(d)[:10]}  NAV = {v:.4f}")
        print()


def main():
    # 加载数据
    logger.info("加载数据...")
    df = load_daily("2022-06-01", "2025-12-31")  # 多加载半年供因子回看用
    logger.info(f"数据: {df['code'].nunique()}只, {len(df):,}条")

    # 配置策略
    config = BacktestConfig(
        start_date="2023-01-01",
        end_date="2025-12-31",
        lookback=20,
        hold_days=20,
        top_n=50,
        weight_method="equal",
        commission=0.0015,
        slippage=0.001,
    )

    # 运行回测
    result = run_strategy_backtest(df, config)

    # 输出报告
    print_strategy_report(result, config)


if __name__ == "__main__":
    main()
