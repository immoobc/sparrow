"""持仓追踪 — 管理你的实际持仓并给出建议

功能:
1. 记录你的持仓（基金/ETF）
2. 追踪场内ETF的技术指标
3. 给出加减仓建议
"""

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import settings
from src.logger import logger
from src.storage.cache import load_daily

PORTFOLIO_FILE = Path(settings.data_dir) / "my_portfolio.json"

# 你的持仓定义
DEFAULT_PORTFOLIO = {
    "positions": [
        {
            "code": "513050",
            "name": "中概互联网ETF",
            "type": "ETF",
            "market": "场内",
            "cost": 5000,
            "note": "中国科技股(腾讯/阿里/美团)",
        },
        {
            "code": "014642",
            "name": "摩根新兴动力混合C",
            "type": "基金",
            "market": "场外",
            "cost": 5000,
            "note": "新兴市场股票",
        },
        {
            "code": "539002",
            "name": "建信新兴市场优选A",
            "type": "基金",
            "market": "场外",
            "cost": 1000,
            "note": "海外新兴市场QDII",
        },
    ],
    "total_invested": 11000,
    "updated": str(date.today()),
}


def load_portfolio() -> dict:
    """加载持仓配置"""
    if PORTFOLIO_FILE.exists():
        return json.loads(PORTFOLIO_FILE.read_text())
    # 首次使用，保存默认配置
    save_portfolio(DEFAULT_PORTFOLIO)
    return DEFAULT_PORTFOLIO


def save_portfolio(portfolio: dict):
    """保存持仓配置"""
    PORTFOLIO_FILE.parent.mkdir(parents=True, exist_ok=True)
    PORTFOLIO_FILE.write_text(json.dumps(portfolio, ensure_ascii=False, indent=2))


def get_etf_analysis(code: str = "513050") -> dict:
    """
    场内ETF技术分析。
    
    用日K线计算:
    - 当前价格 vs MA5/MA20/MA60/MA120
    - RSI(14)
    - 近20日涨跌幅
    - 量能变化
    
    Returns:
        技术分析结果 + 操作建议
    """
    df = load_daily(
        start_date=(date.today() - timedelta(days=300)).isoformat(),
        codes=[code],
    )

    if df.empty or len(df) < 60:
        return {"error": f"{code} 数据不足"}

    df = df.sort_values("trade_date").reset_index(drop=True)

    # 均线
    for n in [5, 20, 60, 120]:
        df[f"ma{n}"] = df["close"].rolling(n).mean()

    # RSI(14)
    delta = df["close"].diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    df["rsi"] = 100 - 100 / (1 + gain / loss.replace(0, np.nan))

    # 成交量均线
    df["vol_ma20"] = df["volume"].rolling(20).mean()

    latest = df.iloc[-1]
    prev = df.iloc[-2] if len(df) > 1 else latest

    price = float(latest["close"])
    ma5 = float(latest["ma5"]) if pd.notna(latest["ma5"]) else price
    ma20 = float(latest["ma20"]) if pd.notna(latest["ma20"]) else price
    ma60 = float(latest["ma60"]) if pd.notna(latest["ma60"]) else price
    ma120 = float(latest["ma120"]) if pd.notna(latest["ma120"]) else price
    rsi = float(latest["rsi"]) if pd.notna(latest["rsi"]) else 50

    # 近20日涨跌幅
    if len(df) >= 20:
        ret_20d = (price / float(df.iloc[-20]["close"]) - 1) * 100
    else:
        ret_20d = 0

    # 量能
    vol_ratio = float(latest["volume"]) / float(latest["vol_ma20"]) if latest["vol_ma20"] > 0 else 1

    # 位置判断
    above_ma = sum([
        price > ma5,
        price > ma20,
        price > ma60,
        price > ma120,
    ])

    # 综合建议
    signals = []
    if rsi < 30:
        signals.append("RSI超卖(<30), 可能超跌反弹")
    elif rsi > 70:
        signals.append("RSI超买(>70), 注意回调风险")

    if price < ma120 * 0.9:
        signals.append("大幅低于120日均线, 处于相对低位")
    elif price > ma120 * 1.1:
        signals.append("大幅高于120日均线, 注意获利了结")

    if above_ma >= 3:
        position = "强势(站上多条均线)"
    elif above_ma >= 2:
        position = "偏强"
    elif above_ma >= 1:
        position = "偏弱"
    else:
        position = "弱势(跌破所有均线)"

    # 操作建议
    if rsi < 30 and price < ma60:
        action = "✅ 可以加仓"
        reason = "超卖+低于均线，属于恐慌区域，适合逆向加仓"
    elif rsi < 40 and price < ma120:
        action = "✅ 适合定投"
        reason = "估值偏低，按计划定投即可"
    elif rsi > 70 and price > ma20 * 1.1:
        action = "⚠️ 暂停加仓"
        reason = "短期涨幅较大，等回调再加"
    elif rsi > 80:
        action = "🔴 考虑减仓"
        reason = "严重超买，可分批止盈"
    else:
        action = "🟡 持有观望"
        reason = "处于正常波动区间，不需要操作"

    return {
        "code": code,
        "date": str(latest["trade_date"])[:10],
        "price": round(price, 3),
        "ma5": round(ma5, 3),
        "ma20": round(ma20, 3),
        "ma60": round(ma60, 3),
        "ma120": round(ma120, 3),
        "rsi": round(rsi, 1),
        "ret_20d": round(ret_20d, 2),
        "vol_ratio": round(vol_ratio, 2),
        "position": position,
        "above_ma_count": above_ma,
        "signals": signals,
        "action": action,
        "reason": reason,
        # K线数据(用于画图)
        "kline_df": df.tail(120),
    }
