"""每日交易信号生成器 — 将策略落地为可执行的交易计划

每个交易日盘后运行，输出:
1. 当前因子截面排名
2. 目标持仓清单 (Top N)
3. 相比昨日持仓的调仓计划 (买入/卖出/持有)
"""

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import settings
from src.logger import logger
from src.storage.cache import load_daily


SIGNAL_DIR = Path(settings.data_dir) / "signals"


def generate_daily_signal(
    lookback: int = 20,
    top_n: int = 50,
    exclude_vol_pct: float = 0.1,
    min_amount: float = 5e6,
) -> dict:
    """
    生成当日交易信号。

    策略逻辑:
    - 因子: 过去20日收益率 (反转, 选最低的)
    - 过滤: 排除停牌/涨跌停/日均成交<500万/波动率最高10%
    - 选股: Top N 等权

    Args:
        lookback: 因子回看天数
        top_n: 持仓数
        exclude_vol_pct: 排除波动率最高的比例
        min_amount: 最小日均成交额(元)

    Returns:
        {
            date: 信号日期,
            target_portfolio: [{code, name, weight, factor, rank}],
            rebalance_plan: {buy: [...], sell: [...], hold: [...]},
            stats: {universe_size, factor_range, ...}
        }
    """
    today = date.today()
    # 需要多加载 lookback 天用于计算因子
    start = (today - timedelta(days=lookback * 2 + 30)).isoformat()

    logger.info(f"生成交易信号 [{today}]")

    # 加载数据
    df = load_daily(start_date=start)
    if df.empty:
        logger.error("无数据")
        return {}

    df = df.sort_values(["code", "trade_date"]).copy()
    df["daily_ret"] = df.groupby("code")["close"].pct_change()
    df["factor"] = df.groupby("code")["close"].pct_change(lookback)
    df["volatility"] = df.groupby("code")["daily_ret"].transform(
        lambda x: x.rolling(lookback).std()
    )
    df["avg_amount"] = df.groupby("code")["amount"].transform(
        lambda x: x.rolling(lookback).mean()
    )

    # 取最新交易日
    latest_date = df["trade_date"].max()
    cross = df[df["trade_date"] == latest_date].dropna(
        subset=["factor", "volatility", "avg_amount"]
    ).copy()

    logger.info(f"最新交易日: {latest_date.date()}, 截面 {len(cross)} 只")

    # 过滤
    cross = cross[cross["volume"] > 0]                       # 停牌
    cross = cross[cross["daily_ret"].abs() < 0.095]          # 涨跌停
    cross = cross[cross["avg_amount"] >= min_amount]          # 成交额

    # 排除高波动
    vol_threshold = cross["volatility"].quantile(1 - exclude_vol_pct)
    cross = cross[cross["volatility"] <= vol_threshold]

    logger.info(f"过滤后可选: {len(cross)} 只")

    # 排序选股
    cross = cross.sort_values("factor", ascending=True)
    selected = cross.head(top_n).copy()
    selected["weight"] = 1.0 / len(selected)
    selected["rank"] = range(1, len(selected) + 1)

    # 构建目标持仓
    target_portfolio = []
    for _, row in selected.iterrows():
        target_portfolio.append({
            "code": row["code"],
            "weight": round(row["weight"], 4),
            "factor": round(row["factor"], 4),
            "close": round(float(row["close"]), 2),
            "rank": int(row["rank"]),
        })

    # 加载昨日持仓（如果有）
    prev_signal = _load_prev_signal()
    prev_codes = set(prev_signal.get("codes", []))
    new_codes = set(selected["code"].tolist())

    buy_codes = new_codes - prev_codes
    sell_codes = prev_codes - new_codes
    hold_codes = new_codes & prev_codes

    rebalance = {
        "buy": sorted(buy_codes),
        "sell": sorted(sell_codes),
        "hold": sorted(hold_codes),
        "buy_count": len(buy_codes),
        "sell_count": len(sell_codes),
        "hold_count": len(hold_codes),
        "turnover_pct": round(len(buy_codes) / max(len(new_codes), 1) * 100, 1),
    }

    result = {
        "date": str(latest_date.date()),
        "target_portfolio": target_portfolio,
        "rebalance_plan": rebalance,
        "stats": {
            "universe_size": len(cross),
            "selected_count": len(selected),
            "factor_min": round(float(selected["factor"].min()), 4),
            "factor_max": round(float(selected["factor"].max()), 4),
            "factor_median": round(float(selected["factor"].median()), 4),
        },
    }

    # 保存信号
    _save_signal(result)
    return result


def _save_signal(signal: dict):
    """保存今日信号到文件"""
    SIGNAL_DIR.mkdir(parents=True, exist_ok=True)
    d = signal.get("date", "unknown")
    path = SIGNAL_DIR / f"signal_{d}.json"

    # 同时保存一个 latest 链接
    save_data = {
        "date": signal["date"],
        "codes": [p["code"] for p in signal["target_portfolio"]],
        "portfolio": signal["target_portfolio"],
        "rebalance": signal["rebalance_plan"],
    }
    path.write_text(json.dumps(save_data, ensure_ascii=False, indent=2))

    latest_path = SIGNAL_DIR / "latest.json"
    latest_path.write_text(json.dumps(save_data, ensure_ascii=False, indent=2))
    logger.info(f"信号保存: {path}")


def _load_prev_signal() -> dict:
    """加载上一次的信号"""
    latest = SIGNAL_DIR / "latest.json"
    if latest.exists():
        return json.loads(latest.read_text())
    return {}


def print_signal_report(signal: dict):
    """打印信号报告"""
    if not signal:
        print("无信号")
        return

    print("\n" + "=" * 60)
    print(f"  交易信号 — {signal['date']}")
    print("=" * 60)

    rb = signal["rebalance_plan"]
    print(f"\n  调仓摘要:")
    print(f"    买入 {rb['buy_count']} 只 | 卖出 {rb['sell_count']} 只 | 持有 {rb['hold_count']} 只")
    print(f"    换手率: {rb['turnover_pct']}%")

    if rb["buy"]:
        print(f"\n  买入清单 ({rb['buy_count']}只):")
        for code in rb["buy"][:20]:
            print(f"    {code}")

    if rb["sell"]:
        print(f"\n  卖出清单 ({rb['sell_count']}只):")
        for code in rb["sell"][:20]:
            print(f"    {code}")

    stats = signal["stats"]
    print(f"\n  因子统计:")
    print(f"    股票池: {stats['universe_size']} 只")
    print(f"    因子范围: [{stats['factor_min']}, {stats['factor_max']}]")

    print(f"\n  目标持仓 Top10:")
    print(f"    {'排名':4s} {'代码':8s} {'权重':>8s} {'因子值':>8s} {'收盘价':>8s}")
    for p in signal["target_portfolio"][:10]:
        print(f"    {p['rank']:<4d} {p['code']:8s} {p['weight']:>7.2%} {p['factor']:>8.4f} {p['close']:>8.2f}")

    print("\n" + "=" * 60 + "\n")
