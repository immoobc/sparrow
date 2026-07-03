"""策略级回测引擎 — 模拟真实交易过程

与因子分层回测的区别:
- 分层回测: 验证因子是否有效 (学术研究)
- 策略回测: 模拟你真实能赚多少钱 (含交易成本/滑点/持仓约束)

本模块实现:
1. 按固定频率调仓 (每N个交易日)
2. 每次调仓: 计算因子 → 选股 → 等权分配
3. 模拟持仓期内的逐日收益
4. 扣除交易成本 (双边千3)
5. 输出净值曲线 + 绩效指标
"""

import time
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from src.logger import logger
from src.strategy.portfolio import preprocess_factor, select_portfolio


@dataclass
class BacktestConfig:
    """回测配置"""
    start_date: str = "2023-01-01"
    end_date: str = "2025-12-31"
    lookback: int = 20              # 因子回看天数
    hold_days: int = 20             # 持仓天数 (=调仓频率)
    top_n: int = 50                 # 持仓股票数
    weight_method: str = "equal"    # 权重方式
    commission: float = 0.0015      # 单边手续费 (万5佣金+千1印花税≈千1.5)
    slippage: float = 0.001         # 滑点 (千1)
    benchmark: str = "equal_weight" # 基准: equal_weight=等权全市场
    min_list_days: int = 60         # 上市最少天数


@dataclass
class BacktestResult:
    """回测结果"""
    nav: pd.Series = None                   # 净值曲线
    benchmark_nav: pd.Series = None         # 基准净值
    trades: list = field(default_factory=list)  # 交易记录
    # 绩效指标
    annual_return: float = 0
    annual_volatility: float = 0
    sharpe_ratio: float = 0
    max_drawdown: float = 0
    max_dd_start: str = ""
    max_dd_end: str = ""
    calmar_ratio: float = 0
    win_rate: float = 0
    total_trades: int = 0
    avg_turnover: float = 0
    total_cost: float = 0
    excess_return: float = 0        # 超额年化收益
    excess_sharpe: float = 0        # 超额夏普


def run_strategy_backtest(df: pd.DataFrame, config: BacktestConfig = None) -> BacktestResult:
    """
    运行完整策略回测。

    Args:
        df: 全市场日K线 (从 cache.load_daily 获取)
        config: 回测配置

    Returns:
        BacktestResult
    """
    if config is None:
        config = BacktestConfig()

    t0 = time.time()
    logger.info(
        f"策略回测: {config.start_date}~{config.end_date}, "
        f"Top{config.top_n}, 每{config.hold_days}天调仓"
    )

    # 准备数据
    df = df.sort_values(["code", "trade_date"]).copy()
    df["daily_ret"] = df.groupby("code")["close"].pct_change()

    # 计算因子: 过去N日收益率
    df["factor"] = df.groupby("code")["close"].pct_change(config.lookback)

    # 过滤日期范围
    date_mask = (df["trade_date"] >= pd.Timestamp(config.start_date)) & \
                (df["trade_date"] <= pd.Timestamp(config.end_date))
    df_bt = df[date_mask].copy()

    # 所有交易日
    all_dates = sorted(df_bt["trade_date"].unique())
    if len(all_dates) < config.hold_days * 2:
        logger.error("数据不足")
        return BacktestResult()

    # 调仓日
    rebalance_dates = all_dates[::config.hold_days]

    # 日收益率 pivot
    ret_pivot = df_bt.pivot_table(index="trade_date", columns="code", values="daily_ret")

    # ── 回测循环 ──────────────────────────────────────────
    portfolio_nav = [1.0]
    benchmark_nav = [1.0]
    nav_dates = [all_dates[0]]
    holdings = {}  # 当前持仓 {code: weight}
    total_cost = 0
    turnover_list = []
    period_returns = []

    for i in range(len(rebalance_dates) - 1):
        rb_date = rebalance_dates[i]
        next_rb = rebalance_dates[min(i + 1, len(rebalance_dates) - 1)]

        # 1. 获取当日截面 + 因子值
        cross = df_bt[df_bt["trade_date"] == rb_date].dropna(subset=["factor"]).copy()

        # 2. 过滤: 停牌/涨跌停/新股
        cross = cross[cross["volume"] > 0]
        cross = cross[cross["daily_ret"].abs() < 0.095]

        if len(cross) < config.top_n * 2:
            continue

        # 3. 选股 (反转因子: ascending=True 选过去跌最多的)
        target_port = select_portfolio(
            cross.rename(columns={"factor": "factor_value"}),
            top_n=config.top_n,
            weight_method=config.weight_method,
            ascending=True,
        )
        new_holdings = dict(zip(target_port["code"], target_port["weight"]))

        # 4. 计算换手 + 交易成本
        turnover = sum(
            abs(new_holdings.get(c, 0) - holdings.get(c, 0))
            for c in set(list(holdings.keys()) + list(new_holdings.keys()))
        ) / 2
        cost = turnover * (config.commission * 2 + config.slippage * 2)
        total_cost += cost
        turnover_list.append(turnover)

        # 5. 计算持仓期收益
        hold_period = ret_pivot.loc[
            (ret_pivot.index > rb_date) & (ret_pivot.index <= next_rb)
        ]

        if hold_period.empty:
            continue

        # 组合日收益
        port_codes = [c for c in new_holdings.keys() if c in hold_period.columns]
        if not port_codes:
            continue

        weights = pd.Series({c: new_holdings[c] for c in port_codes})
        weights = weights / weights.sum()  # 归一化

        port_daily_ret = (hold_period[port_codes] * weights).sum(axis=1)
        # 第一天扣除交易成本
        port_daily_ret.iloc[0] -= cost

        # 基准: 全市场等权
        bm_daily_ret = hold_period.mean(axis=1)

        # 累计净值
        for dt, ret in port_daily_ret.items():
            portfolio_nav.append(portfolio_nav[-1] * (1 + ret))
            nav_dates.append(dt)

        for dt, ret in bm_daily_ret.items():
            benchmark_nav.append(benchmark_nav[-1] * (1 + ret))

        period_ret = (1 + port_daily_ret).prod() - 1
        period_returns.append(period_ret)
        holdings = new_holdings

    # ── 计算绩效 ──────────────────────────────────────────
    result = BacktestResult()
    result.nav = pd.Series(portfolio_nav, index=nav_dates[:len(portfolio_nav)])
    result.benchmark_nav = pd.Series(benchmark_nav[:len(portfolio_nav)])

    n_days = len(portfolio_nav) - 1
    n_years = n_days / 252 if n_days > 0 else 1

    # 年化收益
    final_nav = portfolio_nav[-1]
    result.annual_return = round((final_nav ** (1/n_years) - 1) * 100, 2)

    # 年化波动率
    daily_rets = pd.Series(portfolio_nav).pct_change().dropna()
    result.annual_volatility = round(daily_rets.std() * np.sqrt(252) * 100, 2)

    # 夏普比率 (无风险利率2%)
    rf_daily = 0.02 / 252
    excess_daily = daily_rets - rf_daily
    result.sharpe_ratio = round(
        excess_daily.mean() / excess_daily.std() * np.sqrt(252), 2
    ) if excess_daily.std() > 0 else 0

    # 最大回撤
    nav_series = pd.Series(portfolio_nav)
    cummax = nav_series.cummax()
    drawdown = (nav_series - cummax) / cummax
    result.max_drawdown = round(drawdown.min() * 100, 2)
    dd_end_idx = drawdown.idxmin()
    dd_start_idx = nav_series[:dd_end_idx].idxmax()
    if dd_end_idx < len(nav_dates) and dd_start_idx < len(nav_dates):
        result.max_dd_start = str(nav_dates[dd_start_idx])[:10]
        result.max_dd_end = str(nav_dates[dd_end_idx])[:10]

    # Calmar比率
    result.calmar_ratio = round(
        result.annual_return / abs(result.max_drawdown), 2
    ) if result.max_drawdown != 0 else 0

    # 胜率
    result.win_rate = round(
        sum(1 for r in period_returns if r > 0) / max(len(period_returns), 1) * 100, 1
    )

    # 换手率
    result.avg_turnover = round(np.mean(turnover_list) * 100, 1) if turnover_list else 0
    result.total_cost = round(total_cost * 100, 2)
    result.total_trades = len(rebalance_dates) - 1

    # 超额收益
    bm_final = benchmark_nav[-1] if benchmark_nav else 1
    bm_annual = (bm_final ** (1/n_years) - 1) * 100
    result.excess_return = round(result.annual_return - bm_annual, 2)

    elapsed = time.time() - t0
    logger.info(f"回测完成, {elapsed:.1f}秒")

    return result
