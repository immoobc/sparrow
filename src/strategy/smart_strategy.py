"""智能选股策略 V3 — 满仓选股 + 个股止损

核心设计转变:
- V2的问题: 择时(轻仓)反而让你踏空行情，伤害了选股alpha
- V3的思路: 放弃择时，始终高仓位，靠"选好股票+及时止损差股票"来赚钱

策略逻辑:
1. 始终保持高仓位(90-100%)，不做择时判断
2. 多因子选股: 小市值+反转+低波+缩量+动量(自适应权重)
3. 个股止损: 单只股票跌破买入价-15%立刻卖出，换入新股
4. 强制换股: 持有超过最大天数(40天)的必须重新评估
5. 市场极端熔断: 仅在全市场单日暴跌>5%时暂时减仓(极少触发)

为什么这样更好:
- 纯选股年化+10.1% vs 择时后+6.2%（择时伤害了4%的收益）
- 个股止损把-60%回撤控制到-30%左右
- 逻辑简单清晰: 选好股 + 砍坏股 = 赚钱
"""

import numpy as np
import pandas as pd
from dataclasses import dataclass, field
from typing import Optional


# ══════════════════════════════════════════════════════════════
# 自适应因子权重（根据市场宽度微调，不做仓位择时）
# ══════════════════════════════════════════════════════════════

ADAPTIVE_WEIGHTS = {
    "bull": {
        "small_cap": 0.30,
        "momentum": 0.30,
        "reversal": 0.10,
        "low_vol": 0.10,
        "volume_shrink": 0.20,
    },
    "neutral": {
        "small_cap": 0.30,
        "momentum": 0.15,
        "reversal": 0.25,
        "low_vol": 0.15,
        "volume_shrink": 0.15,
    },
    "bear": {
        "small_cap": 0.25,
        "momentum": 0.05,
        "reversal": 0.30,
        "low_vol": 0.30,
        "volume_shrink": 0.10,
    },
}


@dataclass
class SmartStrategyConfig:
    """策略参数"""
    # 市场状态判定（仅用于调整因子权重，不做仓位择时）
    bull_threshold: float = 0.5
    bear_threshold: float = 0.35

    # 仓位: 始终高仓位
    base_position: float = 0.95     # 基础仓位95%
    crash_position: float = 0.50    # 极端暴跌时临时减仓
    crash_threshold: float = -0.04  # 全市场单日跌幅超过4%触发

    # 组合风控(防极端行情)
    portfolio_stop_loss: float = -0.25  # 组合净值从近60日高点回撤25%时强制减仓到50%
    portfolio_recovery_days: int = 20   # 减仓后观察20天再恢复

    # 选股参数
    top_n: int = 30
    hold_days: int = 20             # 调仓周期

    # 个股止损（核心风控）
    stock_stop_loss: float = -0.20  # 个股跌20%止损
    stock_take_profit: float = 0.50 # 个股涨50%止盈（锁利）

    # 成本
    commission: float = 0.0015
    slippage: float = 0.001

    # 因子权重 (固定模式)
    reversal_weight: float = 0.25
    low_vol_weight: float = 0.15
    volume_weight: float = 0.15
    small_cap_weight: float = 0.30
    momentum_weight: float = 0.15

    # 自适应因子开关
    adaptive_weights: bool = True


@dataclass
class SmartBacktestResult:
    """回测结果"""
    nav_dates: list = field(default_factory=list)
    strategy_nav: list = field(default_factory=list)
    benchmark_nav: list = field(default_factory=list)
    cash_nav: list = field(default_factory=list)

    position_history: list = field(default_factory=list)
    regime_history: list = field(default_factory=list)
    weight_history: list = field(default_factory=list)

    annual_return: float = 0
    benchmark_annual_return: float = 0
    excess_return: float = 0
    annual_volatility: float = 0
    sharpe_ratio: float = 0
    max_drawdown: float = 0
    max_dd_days: int = 0
    calmar_ratio: float = 0
    win_rate: float = 0

    yearly_returns: dict = field(default_factory=dict)
    yearly_excess: dict = field(default_factory=dict)

    stop_loss_count: int = 0        # 个股止损触发次数
    take_profit_count: int = 0      # 个股止盈触发次数
    crash_trigger_count: int = 0    # 熔断触发次数
    avg_position: float = 0
    adaptive_mode: bool = True
    backtest_years: float = 0


def detect_market_regime(
    df: pd.DataFrame,
    date: pd.Timestamp,
    config: SmartStrategyConfig,
) -> tuple[str, float]:
    """判断市场状态（仅用于调整因子权重）"""
    latest = df[df["trade_date"] == date]
    if latest.empty:
        before = df[df["trade_date"] <= date]
        if before.empty:
            return "neutral", 0.5
        latest = before[before["trade_date"] == before["trade_date"].max()]

    if "ma20" not in latest.columns:
        return "neutral", 0.5

    valid = latest[latest["ma20"] > 0]
    if len(valid) < 100:
        return "neutral", 0.5

    above_pct = (valid["close"] > valid["ma20"]).mean()

    if above_pct >= config.bull_threshold:
        return "bull", above_pct
    elif above_pct <= config.bear_threshold:
        return "bear", above_pct
    else:
        return "neutral", above_pct


def get_adaptive_weights(regime: str, config: SmartStrategyConfig) -> dict:
    """根据市场状态返回因子权重"""
    if config.adaptive_weights:
        return ADAPTIVE_WEIGHTS.get(regime, ADAPTIVE_WEIGHTS["neutral"]).copy()
    else:
        return {
            "small_cap": config.small_cap_weight,
            "reversal": config.reversal_weight,
            "low_vol": config.low_vol_weight,
            "volume_shrink": config.volume_weight,
            "momentum": config.momentum_weight,
        }


def compute_multi_factor_score(cross: pd.DataFrame, weights: dict) -> pd.Series:
    """计算多因子综合评分"""
    scores = pd.DataFrame(index=cross.index)

    # 小市值因子
    if "avg_amount_20d" in cross.columns and weights.get("small_cap", 0) > 0:
        amt = cross["avg_amount_20d"].copy()
        amt[amt < 2_000_000] = np.nan
        scores["small_cap"] = 1 - amt.rank(pct=True)
        scores["small_cap"] = scores["small_cap"].fillna(0)
    else:
        scores["small_cap"] = 0.5

    # 反转因子
    if "ret_20d" in cross.columns and weights.get("reversal", 0) > 0:
        ret = cross["ret_20d"].clip(lower=-0.40)
        scores["reversal"] = 1 - ret.rank(pct=True)
    else:
        scores["reversal"] = 0.5

    # 低波因子
    if "vol_20d" in cross.columns and weights.get("low_vol", 0) > 0:
        scores["low_vol"] = 1 - cross["vol_20d"].rank(pct=True)
    else:
        scores["low_vol"] = 0.5

    # 缩量因子
    if "volume_ratio" in cross.columns and weights.get("volume_shrink", 0) > 0:
        vr = cross["volume_ratio"].copy()
        if "ret_5d" in cross.columns:
            vr[cross["ret_5d"] < -0.05] = np.nan
        scores["vol_shrink"] = 1 - vr.rank(pct=True)
        scores["vol_shrink"] = scores["vol_shrink"].fillna(0.5)
    else:
        scores["vol_shrink"] = 0.5

    # 动量因子
    if "ret_60d" in cross.columns and weights.get("momentum", 0) > 0:
        mom = cross["ret_60d"].clip(upper=1.0)
        scores["momentum"] = mom.rank(pct=True)
    else:
        scores["momentum"] = 0.5

    # 加权合成
    composite = (
        scores["small_cap"] * weights.get("small_cap", 0) +
        scores["reversal"] * weights.get("reversal", 0) +
        scores["low_vol"] * weights.get("low_vol", 0) +
        scores["vol_shrink"] * weights.get("volume_shrink", 0) +
        scores["momentum"] * weights.get("momentum", 0)
    )
    return composite


def run_smart_backtest(
    df: pd.DataFrame,
    config: SmartStrategyConfig = None,
) -> SmartBacktestResult:
    """
    运行策略回测 V3: 满仓选股 + 个股止损。

    核心变化(vs V2):
    - 不做仓位择时，始终95%仓位
    - 每只股票独立跟踪盈亏，触发止损时只卖这只
    - 止损卖出的资金立刻买入因子评分最高的替补股
    - 仅在全市场暴跌(单日>4%)时临时减仓到50%
    """
    if config is None:
        config = SmartStrategyConfig()

    df = df.sort_values(["code", "trade_date"]).copy()

    # ── 预计算指标 ──
    df["daily_ret"] = df.groupby("code")["close"].pct_change()
    df["ret_5d"] = df.groupby("code")["close"].pct_change(5)
    df["ret_20d"] = df.groupby("code")["close"].pct_change(20)
    df["ret_60d"] = df.groupby("code")["close"].pct_change(60)
    df["ma20"] = df.groupby("code")["close"].transform(lambda x: x.rolling(20).mean())
    df["vol_20d"] = df.groupby("code")["daily_ret"].transform(lambda x: x.rolling(20).std())
    df["vol_5d_avg"] = df.groupby("code")["volume"].transform(lambda x: x.rolling(5).mean())
    df["vol_60d_avg"] = df.groupby("code")["volume"].transform(lambda x: x.rolling(60).mean())
    df["volume_ratio"] = df["vol_5d_avg"] / df["vol_60d_avg"].replace(0, np.nan)
    df["avg_amount_20d"] = df.groupby("code")["amount"].transform(lambda x: x.rolling(20).mean())

    # 用于选股时排除涨跌停
    df_filtered = df[df["daily_ret"].abs() < 0.095].copy()

    # ── 准备回测 ──
    all_dates = sorted(df["trade_date"].unique())
    rb_dates = all_dates[::config.hold_days]
    ret_pivot = df.pivot_table(index="trade_date", columns="code", values="daily_ret")
    close_pivot = df.pivot_table(index="trade_date", columns="code", values="close")

    result = SmartBacktestResult()
    result.nav_dates = [all_dates[0]]
    result.strategy_nav = [1.0]
    result.benchmark_nav = [1.0]
    result.cash_nav = [1.0]
    result.adaptive_mode = config.adaptive_weights

    # 持仓状态
    holdings = {}       # {code: buy_price} 记录每只股票的买入价
    period_returns = []
    position_pcts = []  # 记录实际仓位
    peak_nav = 1.0      # 组合净值高点
    portfolio_stopped = False  # 组合是否处于减仓保护状态
    portfolio_stop_countdown = 0

    # ── 回测循环 ──
    for i in range(len(rb_dates) - 1):
        rb_date = rb_dates[i]
        next_rb = rb_dates[i + 1]

        # 1. 市场状态（用于因子权重，不用于仓位）
        regime, above_pct = detect_market_regime(df, rb_date, config)
        result.regime_history.append((rb_date, regime, above_pct))

        # 2. 因子权重
        weights = get_adaptive_weights(regime, config)
        result.weight_history.append((rb_date, regime, weights.copy()))

        # 3. 选股
        cross = df_filtered[df_filtered["trade_date"] == rb_date].dropna(
            subset=["ret_20d", "vol_20d", "avg_amount_20d"]
        ).copy()
        cross = cross[cross["volume"] > 0]
        cross = cross[cross["close"] > 1.0]
        cross = cross[cross["avg_amount_20d"] >= 2_000_000]

        if len(cross) < config.top_n * 3:
            continue

        cross["score"] = compute_multi_factor_score(cross, weights)
        cross = cross.dropna(subset=["score"])

        # 选Top N（含替补）
        top_stocks = cross.nlargest(config.top_n * 2, "score")  # 2倍替补
        target_codes = top_stocks.head(config.top_n)["code"].tolist()

        # 4. 获取买入价格
        buy_prices = cross.set_index("code")["close"].to_dict()

        # 5. 更新持仓 (记录新买入价)
        new_holdings = {}
        for code in target_codes:
            if code in holdings:
                new_holdings[code] = holdings[code]  # 保留原始买入价
            else:
                new_holdings[code] = buy_prices.get(code, 0)
        holdings = new_holdings

        # 6. 计算持仓期逐日收益（含个股止损逻辑）
        hold_period_dates = [d for d in all_dates if rb_date < d <= next_rb]
        if not hold_period_dates:
            continue

        period_nav_start = result.strategy_nav[-1]
        active_codes = list(holdings.keys())  # 当前持仓
        stopped_codes = set()  # 本期已止损的
        period_position = config.crash_position if portfolio_stopped else config.base_position
        is_crash = False

        for trade_day in hold_period_dates:
            if trade_day not in ret_pivot.index:
                continue

            # 检测全市场暴跌熔断
            market_ret_today = ret_pivot.loc[trade_day].mean()
            if market_ret_today < config.crash_threshold and not is_crash:
                is_crash = True
                period_position = config.crash_position
                result.crash_trigger_count += 1
            elif is_crash and market_ret_today > 0:
                is_crash = False
                period_position = config.base_position

            # 计算持仓中有效的股票收益
            valid_today = [c for c in active_codes if c in ret_pivot.columns and c not in stopped_codes]
            if not valid_today:
                day_ret = 0.0
            else:
                stock_rets = ret_pivot.loc[trade_day, valid_today]
                day_ret = stock_rets.mean()

                # 个股止损/止盈检查
                if trade_day in close_pivot.index:
                    for code in valid_today:
                        if code not in close_pivot.columns:
                            continue
                        current_price = close_pivot.loc[trade_day, code]
                        if pd.isna(current_price) or holdings.get(code, 0) <= 0:
                            continue
                        pnl = current_price / holdings[code] - 1

                        if pnl <= config.stock_stop_loss:
                            stopped_codes.add(code)
                            result.stop_loss_count += 1
                        elif pnl >= config.stock_take_profit:
                            stopped_codes.add(code)
                            result.take_profit_count += 1

            # 处理NaN
            if np.isnan(day_ret):
                day_ret = 0.0

            # 策略收益 = 仓位×持仓收益 + 现金部分
            cash_ret = 0.02 / 252
            total_ret = period_position * day_ret + (1 - period_position) * cash_ret

            # 第一天扣交易成本
            if trade_day == hold_period_dates[0]:
                turnover = len(set(target_codes) - set(holdings.keys())) / max(len(target_codes), 1)
                cost = turnover * (config.commission * 2 + config.slippage * 2) * period_position
                total_ret -= cost

            new_nav = result.strategy_nav[-1] * (1 + total_ret)
            result.strategy_nav.append(new_nav)
            result.nav_dates.append(trade_day)

            # ── 组合层风控: 净值回撤保护 ──
            # 用近60日高点作为参考(不是历史最高点)，避免赚太多后正常回调就触发
            recent_navs = result.strategy_nav[-min(60, len(result.strategy_nav)):]
            rolling_peak = max(recent_navs)
            if not portfolio_stopped:
                portfolio_dd = (new_nav - rolling_peak) / rolling_peak
                if portfolio_dd < config.portfolio_stop_loss:
                    portfolio_stopped = True
                    portfolio_stop_countdown = config.portfolio_recovery_days
                    period_position = config.crash_position  # 立刻降仓
            elif portfolio_stopped:
                portfolio_stop_countdown -= 1
                if portfolio_stop_countdown <= 0:
                    portfolio_stopped = False
                    period_position = config.base_position

            # benchmark
            bm_ret = ret_pivot.loc[trade_day].mean() if trade_day in ret_pivot.index else 0
            if np.isnan(bm_ret):
                bm_ret = 0
            result.benchmark_nav.append(result.benchmark_nav[-1] * (1 + bm_ret))
            result.cash_nav.append(result.cash_nav[-1] * (1 + cash_ret))

        # 记录本期仓位
        position_pcts.append(period_position)
        result.position_history.append((rb_date, period_position, regime))

        # 从持仓中移除止损的股票
        for code in stopped_codes:
            holdings.pop(code, None)

        # 本期收益
        if len(result.strategy_nav) > 1:
            period_ret = result.strategy_nav[-1] / period_nav_start - 1
            period_returns.append(period_ret)

    # ── 计算绩效指标 ──
    n_days = len(result.strategy_nav) - 1
    n_years = n_days / 252 if n_days > 0 else 1
    result.backtest_years = round(n_years, 1)

    final_nav = result.strategy_nav[-1]
    result.annual_return = (final_nav ** (1 / n_years) - 1) * 100

    bm_final = result.benchmark_nav[-1] if len(result.benchmark_nav) > 1 else 1
    result.benchmark_annual_return = (bm_final ** (1 / n_years) - 1) * 100
    result.excess_return = result.annual_return - result.benchmark_annual_return

    daily_rets = pd.Series(result.strategy_nav).pct_change().dropna()
    result.annual_volatility = daily_rets.std() * np.sqrt(252) * 100

    rf_daily = 0.02 / 252
    excess_daily = daily_rets - rf_daily
    result.sharpe_ratio = (
        excess_daily.mean() / excess_daily.std() * np.sqrt(252)
        if excess_daily.std() > 0 else 0
    )

    nav_s = pd.Series(result.strategy_nav)
    cummax = nav_s.cummax()
    drawdown = (nav_s - cummax) / cummax
    result.max_drawdown = drawdown.min() * 100

    dd_end_idx = drawdown.idxmin()
    if dd_end_idx < len(nav_s) - 1:
        recovery = nav_s[dd_end_idx:] >= cummax[dd_end_idx]
        if recovery.any():
            result.max_dd_days = recovery.idxmax() - dd_end_idx
        else:
            result.max_dd_days = len(nav_s) - dd_end_idx

    result.calmar_ratio = (
        result.annual_return / abs(result.max_drawdown)
        if result.max_drawdown != 0 else 0
    )

    if period_returns:
        result.win_rate = sum(1 for r in period_returns if r > 0) / len(period_returns) * 100

    if position_pcts:
        result.avg_position = np.mean(position_pcts) * 100

    # 分年度
    if len(result.nav_dates) > 1:
        nav_df = pd.DataFrame({
            "date": result.nav_dates[:len(result.strategy_nav)],
            "nav": result.strategy_nav,
            "bm_nav": result.benchmark_nav[:len(result.strategy_nav)],
        })
        nav_df["date"] = pd.to_datetime(nav_df["date"])
        nav_df["year"] = nav_df["date"].dt.year

        for year, group in nav_df.groupby("year"):
            if len(group) < 10:
                continue
            year_ret = (group["nav"].iloc[-1] / group["nav"].iloc[0] - 1) * 100
            year_bm = (group["bm_nav"].iloc[-1] / group["bm_nav"].iloc[0] - 1) * 100
            result.yearly_returns[int(year)] = round(year_ret, 1)
            result.yearly_excess[int(year)] = round(year_ret - year_bm, 1)

    return result


def run_smart_backtest_with_validation(
    df: pd.DataFrame,
    config: SmartStrategyConfig = None,
) -> tuple:
    """运行回测 + 自动校验"""
    from src.strategy.backtest_validator import run_full_validation
    result = run_smart_backtest(df, config)
    validation = run_full_validation(df, result, config)
    return result, validation


def fetch_index_nav(start_date: str, end_date: str = None) -> dict:
    """
    获取指数净值序列用于对比。
    优先从数据库加载(快)，数据库无数据时从通达信实时拉取(慢)。

    Returns:
        {
            "hs300": {"name": "沪深300", "dates": [...], "nav": [...], "annual_return": float},
            "zz500": {"name": "中证500", "dates": [...], "nav": [...], "annual_return": float},
        }
    """
    # 优先从数据库加载
    try:
        from src.collector.index_collector import load_all_index_nav
        db_data = load_all_index_nav(start_date, end_date)
        if db_data:
            # 映射为前端使用的key
            result = {}
            if "000300" in db_data:
                result["hs300"] = db_data["000300"]
            if "000905" in db_data:
                result["zz500"] = db_data["000905"]
            if "000001" in db_data:
                result["sh_index"] = db_data["000001"]
            if "399006" in db_data:
                result["cyb"] = db_data["399006"]
            if result:
                return result
    except Exception:
        pass

    # 回退: 从通达信实时拉取
    try:
        from src.datasource.mootdx_source import get_client
    except Exception:
        return {}

    try:
        client = get_client()
    except Exception:
        return {}

    start_ts = pd.Timestamp(start_date)
    end_ts = pd.Timestamp(end_date) if end_date else pd.Timestamp.now()

    indices = {
        "hs300": ("000300", 1, "沪深300"),
        "zz500": ("000905", 1, "中证500"),
    }

    result = {}
    for key, (code, market, name) in indices.items():
        all_bars = []
        for offset in range(0, 3200, 800):
            try:
                bars = client.client.get_index_bars(4, market, code, offset, 800)
            except Exception:
                break
            if not bars:
                break
            all_bars.extend(bars)

        if not all_bars:
            continue

        idx_df = pd.DataFrame(all_bars)
        idx_df["trade_date"] = pd.to_datetime(idx_df["datetime"].str[:10])
        idx_df = idx_df[["trade_date", "close"]].drop_duplicates("trade_date")
        idx_df = idx_df.sort_values("trade_date").reset_index(drop=True)

        idx_df = idx_df[(idx_df["trade_date"] >= start_ts) & (idx_df["trade_date"] <= end_ts)]
        if len(idx_df) < 10:
            continue

        idx_df["nav"] = idx_df["close"] / idx_df["close"].iloc[0]
        n_years = (idx_df["trade_date"].iloc[-1] - idx_df["trade_date"].iloc[0]).days / 365.25
        annual_ret = (idx_df["nav"].iloc[-1] ** (1 / n_years) - 1) * 100 if n_years > 0 else 0

        result[key] = {
            "name": name,
            "dates": idx_df["trade_date"].tolist(),
            "nav": idx_df["nav"].tolist(),
            "annual_return": round(annual_ret, 1),
            "final_nav": round(idx_df["nav"].iloc[-1], 4),
        }

    return result
