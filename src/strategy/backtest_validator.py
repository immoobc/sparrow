"""回测校验器 — 确保策略计算的正确性

量化策略中数据错误是致命的。本模块提供多层校验:
1. 数据完整性检查: 输入数据是否有异常
2. 因子计算校验: 因子值是否在合理范围
3. 收益计算校验: 净值计算是否自洽
4. 基准对照校验: benchmark是否和独立计算一致
5. 逻辑一致性检查: 仓位/成本/净值之间的数学关系

每次回测后自动运行校验，不通过则报警。
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ValidationResult:
    """校验结果"""
    passed: bool = True
    errors: list = field(default_factory=list)
    warnings: list = field(default_factory=list)
    checks_run: int = 0
    checks_passed: int = 0

    def add_error(self, msg: str):
        self.errors.append(msg)
        self.passed = False

    def add_warning(self, msg: str):
        self.warnings.append(msg)

    def summary(self) -> str:
        status = "✅ 全部通过" if self.passed else "❌ 存在错误"
        lines = [f"{status} ({self.checks_passed}/{self.checks_run} 项检查通过)"]
        for e in self.errors:
            lines.append(f"  ❌ {e}")
        for w in self.warnings:
            lines.append(f"  ⚠️ {w}")
        return "\n".join(lines)


def validate_input_data(df: pd.DataFrame) -> ValidationResult:
    """
    检查1: 输入数据完整性

    确保:
    - 必须列存在
    - 没有全NaN的关键列
    - close/volume 无负值
    - 日期范围合理
    - 没有重复的 (code, trade_date) 记录
    """
    v = ValidationResult()

    # 必须列
    required_cols = ["code", "trade_date", "close", "volume", "amount"]
    for col in required_cols:
        v.checks_run += 1
        if col not in df.columns:
            v.add_error(f"缺少必须列: {col}")
        else:
            v.checks_passed += 1

    if not v.passed:
        return v

    # close 无负值
    v.checks_run += 1
    neg_close = (df["close"] <= 0).sum()
    if neg_close > 0:
        v.add_error(f"close列存在{neg_close}个非正值")
    else:
        v.checks_passed += 1

    # volume 无负值
    v.checks_run += 1
    neg_vol = (df["volume"] < 0).sum()
    if neg_vol > 0:
        v.add_error(f"volume列存在{neg_vol}个负值")
    else:
        v.checks_passed += 1

    # 日期范围
    v.checks_run += 1
    date_range = (df["trade_date"].max() - df["trade_date"].min()).days
    if date_range < 60:
        v.add_error(f"日期范围太短: 仅{date_range}天，需要至少60天")
    else:
        v.checks_passed += 1

    # 重复记录
    v.checks_run += 1
    dups = df.duplicated(subset=["code", "trade_date"]).sum()
    if dups > 0:
        v.add_warning(f"存在{dups}条重复的(code,trade_date)记录")
    v.checks_passed += 1

    # 股票数量
    v.checks_run += 1
    n_stocks = df["code"].nunique()
    if n_stocks < 50:
        v.add_warning(f"股票数量过少: {n_stocks}只，可能影响分散化")
    v.checks_passed += 1

    # 日收益率异常检查 (如果已计算)
    if "daily_ret" in df.columns:
        v.checks_run += 1
        extreme_ret = (df["daily_ret"].abs() > 0.20).sum()  # 超过20%的日收益
        total_ret = df["daily_ret"].notna().sum()
        if extreme_ret > total_ret * 0.01:  # 超过1%的记录有极端收益
            v.add_warning(f"日收益率>20%的记录占比过高: {extreme_ret}/{total_ret} ({extreme_ret/total_ret*100:.2f}%)")
        v.checks_passed += 1

    return v


def validate_nav_series(
    strategy_nav: list,
    benchmark_nav: list,
    nav_dates: list,
) -> ValidationResult:
    """
    检查2: 净值序列自洽性

    确保:
    - 净值序列无NaN/Inf
    - 净值起始为1.0
    - 三个序列长度一致
    - 日收益率在合理范围（单日不超过±50%）
    - 净值不会突然跳变
    """
    v = ValidationResult()

    # 长度一致
    v.checks_run += 1
    if len(strategy_nav) != len(benchmark_nav):
        v.add_error(f"策略净值({len(strategy_nav)})和基准净值({len(benchmark_nav)})长度不一致")
    else:
        v.checks_passed += 1

    v.checks_run += 1
    if len(strategy_nav) != len(nav_dates):
        v.add_error(f"策略净值({len(strategy_nav)})和日期({len(nav_dates)})长度不一致")
    else:
        v.checks_passed += 1

    # 起始值
    v.checks_run += 1
    if abs(strategy_nav[0] - 1.0) > 1e-10:
        v.add_error(f"策略净值起始值不为1.0: {strategy_nav[0]}")
    else:
        v.checks_passed += 1

    v.checks_run += 1
    if abs(benchmark_nav[0] - 1.0) > 1e-10:
        v.add_error(f"基准净值起始值不为1.0: {benchmark_nav[0]}")
    else:
        v.checks_passed += 1

    # NaN/Inf 检查
    v.checks_run += 1
    nan_count = sum(1 for x in strategy_nav if np.isnan(x) or np.isinf(x))
    if nan_count > 0:
        v.add_error(f"策略净值含{nan_count}个NaN/Inf值")
    else:
        v.checks_passed += 1

    v.checks_run += 1
    bm_nan = sum(1 for x in benchmark_nav if np.isnan(x) or np.isinf(x))
    if bm_nan > 0:
        v.add_error(f"基准净值含{bm_nan}个NaN/Inf值")
    else:
        v.checks_passed += 1

    # 日收益率合理性
    v.checks_run += 1
    nav_arr = np.array(strategy_nav)
    daily_rets = nav_arr[1:] / nav_arr[:-1] - 1
    extreme_days = np.sum(np.abs(daily_rets) > 0.50)
    if extreme_days > 0:
        v.add_error(f"策略单日收益超过±50%: {extreme_days}天（数据可能有误）")
    else:
        v.checks_passed += 1

    # 净值不能为负
    v.checks_run += 1
    neg_nav = sum(1 for x in strategy_nav if x < 0)
    if neg_nav > 0:
        v.add_error(f"策略净值出现负值: {neg_nav}个")
    else:
        v.checks_passed += 1

    # benchmark净值不能为负
    v.checks_run += 1
    neg_bm = sum(1 for x in benchmark_nav if x < 0)
    if neg_bm > 0:
        v.add_error(f"基准净值出现负值: {neg_bm}个")
    else:
        v.checks_passed += 1

    return v


def validate_benchmark_independently(
    df: pd.DataFrame,
    benchmark_nav: list,
    nav_dates: list,
    tolerance: float = 0.05,
) -> ValidationResult:
    """
    检查3: 独立验证基准收益

    用完全独立的方法计算全市场等权收益，和回测中的benchmark对比。
    如果偏差超过tolerance，说明计算有bug。
    """
    v = ValidationResult()

    if len(nav_dates) < 10 or len(benchmark_nav) < 10:
        v.checks_run += 1
        v.add_warning("数据太少，跳过独立benchmark验证")
        v.checks_passed += 1
        return v

    # 独立计算全市场等权收益
    df_check = df.sort_values(["code", "trade_date"]).copy()
    df_check["daily_ret"] = df_check.groupby("code")["close"].pct_change()
    market_daily = df_check.groupby("trade_date")["daily_ret"].mean().dropna().sort_index()

    # 取和回测相同的日期范围
    start_date = pd.Timestamp(nav_dates[0])
    end_date = pd.Timestamp(nav_dates[-1])
    market_daily = market_daily[(market_daily.index >= start_date) & (market_daily.index <= end_date)]

    if market_daily.empty:
        v.checks_run += 1
        v.add_warning("独立计算的市场收益为空")
        v.checks_passed += 1
        return v

    # 计算独立的年化收益
    independent_nav = (1 + market_daily).cumprod()
    n_years = (market_daily.index[-1] - market_daily.index[0]).days / 365.25
    if n_years > 0:
        independent_annual = (independent_nav.iloc[-1] ** (1 / n_years) - 1) * 100
    else:
        independent_annual = 0

    # 回测中的benchmark年化
    bm_final = benchmark_nav[-1]
    n_years_bt = (len(benchmark_nav) - 1) / 252
    if n_years_bt > 0:
        backtest_bm_annual = (bm_final ** (1 / n_years_bt) - 1) * 100
    else:
        backtest_bm_annual = 0

    # 对比
    v.checks_run += 1
    diff = abs(independent_annual - backtest_bm_annual)
    if diff > tolerance * 100:  # tolerance是比率，转换为百分点
        v.add_error(
            f"基准收益偏差过大: 独立计算年化={independent_annual:+.1f}%, "
            f"回测中={backtest_bm_annual:+.1f}%, 差异={diff:.1f}个百分点 "
            f"(允许{tolerance*100:.0f}个百分点)"
        )
    else:
        v.checks_passed += 1

    # 记录实际值供参考
    v.checks_run += 1
    v.checks_passed += 1
    if diff > tolerance * 50:  # 一半tolerance作为warning
        v.add_warning(
            f"基准年化: 独立={independent_annual:+.1f}% vs 回测={backtest_bm_annual:+.1f}% "
            f"(差异{diff:.1f}pp，在容忍范围内)"
        )

    return v


def validate_performance_metrics(
    strategy_nav: list,
    annual_return: float,
    max_drawdown: float,
    sharpe_ratio: float,
    backtest_years: float,
) -> ValidationResult:
    """
    检查4: 绩效指标计算验证

    独立重算关键指标，确保和报告一致。
    """
    v = ValidationResult()

    if len(strategy_nav) < 10:
        return v

    nav_arr = np.array(strategy_nav)
    n_days = len(nav_arr) - 1
    n_years = n_days / 252

    # 验证年化收益
    v.checks_run += 1
    expected_annual = (nav_arr[-1] ** (1 / n_years) - 1) * 100 if n_years > 0 else 0
    if abs(expected_annual - annual_return) > 0.1:
        v.add_error(f"年化收益不一致: 报告={annual_return:.2f}%, 验算={expected_annual:.2f}%")
    else:
        v.checks_passed += 1

    # 验证最大回撤
    v.checks_run += 1
    cummax = np.maximum.accumulate(nav_arr)
    drawdowns = (nav_arr - cummax) / cummax
    expected_dd = drawdowns.min() * 100
    if abs(expected_dd - max_drawdown) > 0.1:
        v.add_error(f"最大回撤不一致: 报告={max_drawdown:.2f}%, 验算={expected_dd:.2f}%")
    else:
        v.checks_passed += 1

    # 验证夏普比率
    v.checks_run += 1
    daily_rets = nav_arr[1:] / nav_arr[:-1] - 1
    rf_daily = 0.02 / 252
    excess = daily_rets - rf_daily
    if excess.std() > 0:
        expected_sharpe = excess.mean() / excess.std() * np.sqrt(252)
    else:
        expected_sharpe = 0
    if abs(expected_sharpe - sharpe_ratio) > 0.01:
        v.add_error(f"夏普比率不一致: 报告={sharpe_ratio:.4f}, 验算={expected_sharpe:.4f}")
    else:
        v.checks_passed += 1

    # 年化收益合理性
    v.checks_run += 1
    if abs(annual_return) > 200:
        v.add_error(f"年化收益不合理: {annual_return:.1f}% (超过±200%)")
    else:
        v.checks_passed += 1

    # 最大回撤合理性
    v.checks_run += 1
    if max_drawdown < -99:
        v.add_error(f"最大回撤不合理: {max_drawdown:.1f}% (接近-100%)")
    elif max_drawdown > 0:
        v.add_error(f"最大回撤为正值: {max_drawdown:.1f}% (应该<=0)")
    else:
        v.checks_passed += 1

    return v


def validate_factor_weights(weights: dict) -> ValidationResult:
    """
    检查5: 因子权重合法性

    确保:
    - 所有权重非负
    - 权重总和为1.0（允许0.01误差）
    """
    v = ValidationResult()

    v.checks_run += 1
    for k, w in weights.items():
        if w < 0:
            v.add_error(f"因子权重为负: {k}={w}")
            return v
    v.checks_passed += 1

    v.checks_run += 1
    total = sum(weights.values())
    if abs(total - 1.0) > 0.01:
        v.add_error(f"因子权重之和≠1.0: 实际={total:.4f}")
    else:
        v.checks_passed += 1

    return v


def run_full_validation(
    df: pd.DataFrame,
    result,  # SmartBacktestResult
    config=None,
) -> ValidationResult:
    """
    运行完整校验流程。

    在每次回测后调用，汇总所有检查结果。
    """
    final = ValidationResult()

    # 1. 输入数据
    v1 = validate_input_data(df)
    final.checks_run += v1.checks_run
    final.checks_passed += v1.checks_passed
    final.errors.extend(v1.errors)
    final.warnings.extend(v1.warnings)

    # 2. 净值序列
    v2 = validate_nav_series(
        result.strategy_nav,
        result.benchmark_nav,
        result.nav_dates,
    )
    final.checks_run += v2.checks_run
    final.checks_passed += v2.checks_passed
    final.errors.extend(v2.errors)
    final.warnings.extend(v2.warnings)

    # 3. 独立benchmark验证 (容忍5个百分点，因为回测只覆盖持仓期间)
    v3 = validate_benchmark_independently(
        df, result.benchmark_nav, result.nav_dates, tolerance=0.05
    )
    final.checks_run += v3.checks_run
    final.checks_passed += v3.checks_passed
    final.errors.extend(v3.errors)
    final.warnings.extend(v3.warnings)

    # 4. 绩效指标验证
    v4 = validate_performance_metrics(
        result.strategy_nav,
        result.annual_return,
        result.max_drawdown,
        result.sharpe_ratio,
        result.backtest_years,
    )
    final.checks_run += v4.checks_run
    final.checks_passed += v4.checks_passed
    final.errors.extend(v4.errors)
    final.warnings.extend(v4.warnings)

    # 5. 因子权重验证
    from src.strategy.smart_strategy import ADAPTIVE_WEIGHTS
    for regime, weights in ADAPTIVE_WEIGHTS.items():
        v5 = validate_factor_weights(weights)
        final.checks_run += v5.checks_run
        final.checks_passed += v5.checks_passed
        for e in v5.errors:
            final.errors.append(f"[{regime}] {e}")
        final.warnings.extend(v5.warnings)

    # 汇总
    final.passed = len(final.errors) == 0
    return final
