"""实盘信号生成器 — 将V3策略转化为可执行的交易计划

每月运行一次（或每20个交易日），输出:
1. 本期目标持仓清单（30只股票 + 买入价 + 权重）
2. 相比上期的调仓计划（买什么、卖什么、不动什么）
3. 每只股票的止损价（买入价×0.8）
4. 风控提醒（暴跌预警等）

使用方式:
    from src.strategy.live_signal import generate_live_signal, print_operation_plan
    signal = generate_live_signal(capital=100000)
    print_operation_plan(signal)
"""

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import settings
from src.logger import logger
from src.storage.cache import load_daily
from src.strategy.smart_strategy import (
    SmartStrategyConfig, detect_market_regime,
    get_adaptive_weights, compute_multi_factor_score
)


SIGNAL_DIR = Path(settings.data_dir) / "signals"


def generate_live_signal(
    capital: float = 100_000,
    config: SmartStrategyConfig = None,
) -> dict:
    """
    生成当前可执行的交易信号。

    Args:
        capital: 你打算用多少钱来运行这个策略（元）
        config: 策略配置（默认使用V3最优配置）

    Returns:
        {
            date: 信号日期,
            market_regime: 当前市场状态,
            target_portfolio: [
                {code, score, close, weight, amount, shares, stop_price, take_price}
            ],
            rebalance_plan: {buy: [...], sell: [...], hold: [...]},
            risk_alerts: [...],
            operation_guide: {...}
        }
    """
    if config is None:
        config = SmartStrategyConfig()

    today = date.today()
    # 加载足够的历史数据用于计算因子
    # 低配: 缩短到90天 + 采样1000只(信号生成需要更大池子)
    from src.config import settings as _sig_settings
    if _sig_settings.is_low_memory:
        start = (today - timedelta(days=90)).isoformat()
    else:
        start = (today - timedelta(days=200)).isoformat()

    logger.info(f"生成实盘信号 [{today}]")

    # 加载数据
    df = load_daily(start_date=start)
    if df.empty:
        logger.error("无数据")
        return {"error": "数据为空，请先点击「一键更新数据」"}

    # 低配: 采样以控制内存
    if _sig_settings.is_low_memory:
        all_codes = df["code"].unique()
        if len(all_codes) > 1000:
            rng = np.random.default_rng(seed=int(today.toordinal()))
            sampled = rng.choice(all_codes, size=1000, replace=False)
            df = df[df["code"].isin(sampled)]

    df = df.sort_values(["code", "trade_date"]).copy()

    # 计算所有指标
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

    # 取最新交易日截面（确保有足够股票）
    latest_date = df["trade_date"].max()
    cross = df[df["trade_date"] == latest_date].dropna(
        subset=["ret_20d", "vol_20d", "avg_amount_20d"]
    ).copy()

    # 如果最新日数据太少，往前找
    if len(cross) < config.top_n * 3:
        recent_dates = sorted(df["trade_date"].unique(), reverse=True)
        for d in recent_dates[:10]:
            cross = df[df["trade_date"] == d].dropna(
                subset=["ret_20d", "vol_20d", "avg_amount_20d"]
            ).copy()
            if len(cross) >= config.top_n * 3:
                latest_date = d
                break

    logger.info(f"最新交易日: {latest_date.date()}, 截面 {len(cross)} 只")

    # 过滤
    cross = cross[cross["volume"] > 0]
    cross = cross[cross["daily_ret"].abs() < 0.095]  # 排除涨跌停
    cross = cross[cross["close"] > 1.0]               # 排除仙股
    cross = cross[cross["avg_amount_20d"] >= 2_000_000]  # 流动性

    logger.info(f"过滤后: {len(cross)} 只")

    # 判断市场状态
    regime, above_pct = detect_market_regime(df, latest_date, config)

    # 获取因子权重
    weights = get_adaptive_weights(regime, config)

    # 多因子评分
    cross["score"] = compute_multi_factor_score(cross, weights)
    cross = cross.dropna(subset=["score"])

    if len(cross) < config.top_n:
        return {"error": f"可选股票不足(仅{len(cross)}只)，请确保数据已更新到最新"}

    # 选Top N（含替补，确保有足够买得起的股票）
    top_stocks = cross.nlargest(config.top_n * 3, "score")

    # 从评分最高的开始，选出能买得起1手(100股)的
    target_portfolio = []
    per_stock_capital = capital * config.base_position / config.top_n

    for _, row in top_stocks.iterrows():
        if len(target_portfolio) >= config.top_n:
            break
        price = float(row["close"])
        shares = int(per_stock_capital / price / 100) * 100
        if shares < 100:
            continue  # 买不起1手，跳过选下一只
        actual_amount = shares * price
        stop_price = round(price * (1 + config.stock_stop_loss), 2)
        take_price = round(price * (1 + config.stock_take_profit), 2)

        target_portfolio.append({
            "code": row["code"],
            "score": round(float(row["score"]), 4),
            "close": round(price, 2),
            "weight": 0,  # 后面统一计算
            "target_amount": round(per_stock_capital, 0),
            "shares": shares,
            "actual_amount": round(actual_amount, 0),
            "stop_price": stop_price,
            "take_price": take_price,
            "ret_20d": round(float(row["ret_20d"]) * 100, 1),
            "vol_20d": round(float(row["vol_20d"]) * 100, 2),
        })

    if not target_portfolio:
        return {"error": "资金不足，无法构建有效持仓。建议增加资金或减少持仓股数。"}

    # 重新计算权重
    for p in target_portfolio:
        p["weight"] = round(1.0 / len(target_portfolio), 4)

    selected_codes = [p["code"] for p in target_portfolio]

    # 加载上一期持仓对比
    prev_signal = _load_prev_signal()
    prev_codes = set(prev_signal.get("codes", []))
    new_codes = set(selected_codes)

    buy_codes = sorted(new_codes - prev_codes)
    sell_codes = sorted(prev_codes - new_codes)
    hold_codes = sorted(new_codes & prev_codes)

    rebalance_plan = {
        "buy": buy_codes,
        "sell": sell_codes,
        "hold": hold_codes,
        "buy_count": len(buy_codes),
        "sell_count": len(sell_codes),
        "hold_count": len(hold_codes),
        "turnover_pct": round(len(buy_codes) / max(len(new_codes), 1) * 100, 1),
    }

    # 风控提醒
    risk_alerts = []
    if above_pct < 0.3:
        risk_alerts.append("⚠️ 市场极弱（仅30%股票在均线上方），注意控制仓位")
    if cross["daily_ret"].mean() < -0.03:
        risk_alerts.append("🔴 今日全市场平均跌幅超3%，考虑暂缓买入")

    # 操作指南
    operation_guide = {
        "capital": capital,
        "position_pct": config.base_position * 100,
        "stock_capital": round(capital * config.base_position, 0),
        "cash_reserve": round(capital * (1 - config.base_position), 0),
        "per_stock": round(per_stock_capital, 0),
        "stop_loss_pct": config.stock_stop_loss * 100,
        "take_profit_pct": config.stock_take_profit * 100,
        "next_rebalance_days": config.hold_days,
        "total_stocks": len(target_portfolio),
    }

    result = {
        "date": str(latest_date.date()),
        "market_regime": regime,
        "market_above_ma_pct": round(above_pct * 100, 1),
        "factor_weights": weights,
        "target_portfolio": target_portfolio,
        "rebalance_plan": rebalance_plan,
        "risk_alerts": risk_alerts,
        "operation_guide": operation_guide,
    }

    # 保存信号
    _save_signal(result, selected_codes)

    return result


def _save_signal(signal: dict, codes: list):
    """保存信号"""
    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    d = signal.get("date", "unknown")

    save_data = {
        "date": signal["date"],
        "codes": codes,
        "portfolio": signal["target_portfolio"],
        "rebalance": signal["rebalance_plan"],
        "regime": signal["market_regime"],
    }

    path = SIGNAL_DIR / f"signal_{d}.json"
    path.write_text(json.dumps(save_data, ensure_ascii=False, indent=2))

    latest_path = SIGNAL_DIR / "latest.json"
    latest_path.write_text(json.dumps(save_data, ensure_ascii=False, indent=2))
    logger.info(f"信号保存: {path}")


def _load_prev_signal() -> dict:
    """加载上一期信号"""
    latest = SIGNAL_DIR / "latest.json"
    if latest.exists():
        return json.loads(latest.read_text())
    return {}


def print_operation_plan(signal: dict):
    """打印可执行的操作计划"""
    if "error" in signal:
        print(f"❌ {signal['error']}")
        return

    guide = signal["operation_guide"]
    regime_cn = {"bull": "🟢 牛市", "neutral": "🟡 震荡", "bear": "🔴 熊市"}

    print("\n" + "=" * 60)
    print(f"  📋 实盘操作计划 — {signal['date']}")
    print("=" * 60)

    print(f"\n  市场状态: {regime_cn.get(signal['market_regime'], '未知')}")
    print(f"  均线上方: {signal['market_above_ma_pct']}%")

    if signal["risk_alerts"]:
        print(f"\n  ⚠️ 风控提醒:")
        for alert in signal["risk_alerts"]:
            print(f"    {alert}")

    print(f"\n  💰 资金分配:")
    print(f"    总资金: ¥{guide['capital']:,.0f}")
    print(f"    股票仓位: {guide['position_pct']:.0f}% = ¥{guide['stock_capital']:,.0f}")
    print(f"    现金保留: {100-guide['position_pct']:.0f}% = ¥{guide['cash_reserve']:,.0f}")
    print(f"    每只股票: ¥{guide['per_stock']:,.0f} (共{guide['total_stocks']}只)")

    rb = signal["rebalance_plan"]
    print(f"\n  📊 调仓计划:")
    print(f"    买入 {rb['buy_count']} 只 | 卖出 {rb['sell_count']} 只 | 持有 {rb['hold_count']} 只")
    print(f"    换手率: {rb['turnover_pct']}%")

    print(f"\n  🛒 目标持仓 (Top 10):")
    print(f"    {'#':>3s} {'代码':8s} {'现价':>8s} {'数量':>6s} {'金额':>8s} {'止损价':>7s} {'止盈价':>7s}")
    print(f"    {'-'*52}")
    for p in signal["target_portfolio"][:10]:
        print(f"    {p['code']:8s} ¥{p['close']:>7.2f} {p['shares']:>5d}股 "
              f"¥{p['actual_amount']:>7.0f} ¥{p['stop_price']:>6.2f} ¥{p['take_price']:>6.2f}")

    print(f"\n  ⏰ 下次调仓: {guide['next_rebalance_days']}个交易日后")
    print(f"  🛡️ 止损规则: 任一股票跌破买入价{guide['stop_loss_pct']:.0f}%立即卖出")
    print(f"  🎯 止盈规则: 任一股票涨超{guide['take_profit_pct']:.0f}%锁定利润")

    print("\n" + "=" * 60 + "\n")
