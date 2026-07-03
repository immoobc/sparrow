"""行业分析器 — 判断行业热度、所处阶段、资金流向

核心功能:
1. 行业涨跌排名（今日/本周/本月/近3月）
2. 行业热度判断（初始期/中期/尾期）
3. 行业资金流向（哪些行业在被买入/卖出）
4. 行业龙头股推荐

判断行业所处阶段的方法:
- 初始期: 行业近20日涨幅>10%，但60日涨幅<15%（刚启动）
- 中期加速: 20日涨幅>5%，60日涨幅>20%（主升浪）
- 尾期过热: 20日涨幅<5%或开始回落，但60日涨幅>30%（可能见顶）
- 低迷/底部: 近60日跌幅>10%（可能是布局机会）
"""

from datetime import date, timedelta

import numpy as np
import pandas as pd

from src.logger import logger


def get_sector_ranking(sector_daily_df: pd.DataFrame, period: str = "today") -> pd.DataFrame:
    """
    行业涨跌幅排名。

    Args:
        sector_daily_df: sector_daily表数据(需含sector_code, trade_date, change_pct)
        period: "today"/"5d"/"20d"/"60d"

    Returns:
        DataFrame[行业, 涨跌幅, 上涨数, 下跌数, 龙头]
    """
    if sector_daily_df.empty:
        return pd.DataFrame()

    if period == "today":
        latest = sector_daily_df[sector_daily_df["trade_date"] == sector_daily_df["trade_date"].max()]
        return latest.sort_values("change_pct", ascending=False)
    else:
        # 计算区间涨跌幅
        days = {"5d": 5, "20d": 20, "60d": 60}.get(period, 20)
        dates = sorted(sector_daily_df["trade_date"].unique())
        if len(dates) < days:
            return pd.DataFrame()

        recent = sector_daily_df[sector_daily_df["trade_date"] >= dates[-days]]
        # 累计涨跌幅
        cumulative = recent.groupby("sector_code").agg(
            cum_pct=("change_pct", lambda x: ((1 + x/100).prod() - 1) * 100),
            sector_name=("sector_name", "first") if "sector_name" in recent.columns else ("sector_code", "first"),
        ).reset_index()
        return cumulative.sort_values("cum_pct", ascending=False)


def analyze_sector_phase(
    sector_code: str,
    sector_daily_df: pd.DataFrame,
) -> dict:
    """
    判断单个行业当前所处阶段。

    阶段判定逻辑:
    - 🚀 初始期(刚启动): 20日涨>10%, 60日涨<15% → 值得关注
    - 📈 中期(主升浪): 20日涨>5%, 60日涨>20% → 趋势确认，跟随
    - ⚠️ 尾期(过热): 20日涨<5%或回落, 60日涨>30% → 谨慎，可能见顶
    - 📉 回调期: 20日跌>5% → 观望，等企稳
    - 🧊 低迷/底部: 60日跌>10% → 可能是未来的布局机会

    Returns:
        {phase, phase_name, reason, ret_20d, ret_60d, suggestion}
    """
    df = sector_daily_df[sector_daily_df["sector_code"] == sector_code].copy()
    if df.empty or len(df) < 20:
        return {"phase": "unknown", "phase_name": "数据不足"}

    df = df.sort_values("trade_date")
    pcts = df["change_pct"].values

    # 计算区间涨跌幅
    if len(pcts) >= 20:
        ret_20d = ((1 + pcts[-20:]/100).prod() - 1) * 100
    else:
        ret_20d = 0

    if len(pcts) >= 60:
        ret_60d = ((1 + pcts[-60:]/100).prod() - 1) * 100
    else:
        ret_60d = ((1 + pcts/100).prod() - 1) * 100

    # 判断阶段
    if ret_20d > 10 and ret_60d < 15:
        phase = "initial"
        phase_name = "🚀 初始期(刚启动)"
        suggestion = "值得关注！行业刚开始走强，可以小仓位试水。"
    elif ret_20d > 5 and ret_60d > 20:
        phase = "mid"
        phase_name = "📈 主升浪(中期)"
        suggestion = "趋势已确认。可以跟随但不追高，回调时买入。"
    elif ret_20d < 5 and ret_60d > 30:
        phase = "late"
        phase_name = "⚠️ 尾期(可能过热)"
        suggestion = "行业涨幅已大，追高风险高。如已持有可继续持有，不建议新买入。"
    elif ret_20d < -5:
        phase = "pullback"
        phase_name = "📉 回调期"
        suggestion = "正在下跌，等稳定后再考虑。不接飞刀。"
    elif ret_60d < -10:
        phase = "bottom"
        phase_name = "🧊 低迷/筑底"
        suggestion = "行业处于底部区域，可能是未来1-3个月的布局机会（但要有耐心）。"
    else:
        phase = "neutral"
        phase_name = "🟡 震荡"
        suggestion = "无明确方向，观望为主。"

    return {
        "phase": phase,
        "phase_name": phase_name,
        "ret_20d": round(ret_20d, 1),
        "ret_60d": round(ret_60d, 1),
        "suggestion": suggestion,
    }


def get_hot_sectors(sector_daily_df: pd.DataFrame, top_n: int = 10) -> list[dict]:
    """
    找出当前最热门的行业（综合近期涨幅+资金态势）。

    Returns:
        [{sector_code, sector_name, ret_5d, ret_20d, phase, suggestion}, ...]
    """
    if sector_daily_df.empty:
        return []

    sectors = sector_daily_df["sector_code"].unique()
    results = []

    for code in sectors:
        phase_info = analyze_sector_phase(code, sector_daily_df)
        if phase_info["phase"] == "unknown":
            continue

        # 获取行业名称
        name_rows = sector_daily_df[sector_daily_df["sector_code"] == code]
        name = name_rows["sector_name"].iloc[0] if "sector_name" in name_rows.columns else code

        results.append({
            "sector_code": code,
            "sector_name": name,
            "ret_20d": phase_info["ret_20d"],
            "ret_60d": phase_info["ret_60d"],
            "phase": phase_info["phase_name"],
            "suggestion": phase_info["suggestion"],
        })

    # 按20日涨幅排序
    results.sort(key=lambda x: x["ret_20d"], reverse=True)
    return results[:top_n]


def get_sector_momentum_score(sector_daily_df: pd.DataFrame) -> list[dict]:
    """
    计算各行业的中长期动量评分（适合月频操作者）。

    综合考虑:
    - 20日趋势强度（短期动量）
    - 60日趋势强度（中期动量）
    - 动量加速度（近20日 vs 前20日，判断加速还是减速）
    - 多空比（近20日上涨天数占比）

    Returns:
        [{sector_code, sector_name, ret_5d, ret_20d, ret_60d, momentum_score,
          acceleration, win_rate_20d, phase, suggestion}, ...]
    """
    if sector_daily_df.empty:
        return []

    sectors = sector_daily_df["sector_code"].unique()
    dates = sorted(sector_daily_df["trade_date"].unique())
    results = []

    for code in sectors:
        df = sector_daily_df[sector_daily_df["sector_code"] == code].sort_values("trade_date")
        if len(df) < 20:
            continue

        pcts = df["change_pct"].values
        name = df["sector_name"].iloc[0] if "sector_name" in df.columns else code

        # 各区间收益
        ret_5d = ((1 + pcts[-5:] / 100).prod() - 1) * 100 if len(pcts) >= 5 else 0
        ret_20d = ((1 + pcts[-20:] / 100).prod() - 1) * 100
        ret_60d = ((1 + pcts[-60:] / 100).prod() - 1) * 100 if len(pcts) >= 60 else ((1 + pcts / 100).prod() - 1) * 100

        # 动量加速度: 近20日收益 - 前20日收益（正值=加速，负值=减速）
        if len(pcts) >= 40:
            ret_prev_20d = ((1 + pcts[-40:-20] / 100).prod() - 1) * 100
            acceleration = ret_20d - ret_prev_20d
        else:
            acceleration = 0

        # 近20日胜率（上涨天数占比）
        win_rate_20d = (pcts[-20:] > 0).sum() / 20 * 100

        # 综合动量评分 (0-100)
        # 20日收益权重40% + 60日收益权重30% + 加速度权重20% + 胜率权重10%
        score_20d = min(max(ret_20d * 4, -50), 50)  # 归一化到 -50~50
        score_60d = min(max(ret_60d * 1.5, -50), 50)
        score_acc = min(max(acceleration * 3, -30), 30)
        score_wr = (win_rate_20d - 50) * 0.6  # 50%为中性

        momentum_score = 50 + score_20d * 0.4 + score_60d * 0.3 + score_acc * 0.2 + score_wr * 0.1
        momentum_score = min(max(momentum_score, 0), 100)

        # 阶段判断
        phase_info = analyze_sector_phase(code, sector_daily_df)

        results.append({
            "sector_code": code,
            "sector_name": name,
            "ret_5d": round(ret_5d, 2),
            "ret_20d": round(ret_20d, 2),
            "ret_60d": round(ret_60d, 2),
            "momentum_score": round(momentum_score, 1),
            "acceleration": round(acceleration, 2),
            "win_rate_20d": round(win_rate_20d, 1),
            "phase": phase_info["phase_name"],
            "phase_key": phase_info["phase"],
            "suggestion": phase_info["suggestion"],
        })

    results.sort(key=lambda x: x["momentum_score"], reverse=True)
    return results


def get_sector_rotation_signals(sector_daily_df: pd.DataFrame) -> dict:
    """
    生成行业轮动信号（月频决策用）。

    逻辑:
    - "建议关注": 动量加速 + 处于初始期/主升浪前段
    - "建议规避": 动量减速 + 处于尾期/回调期
    - "等待确认": 底部区域但尚未启动

    Returns:
        {"buy_candidates": [...], "avoid": [...], "watch": [...], "summary": str}
    """
    all_sectors = get_sector_momentum_score(sector_daily_df)
    if not all_sectors:
        return {"buy_candidates": [], "avoid": [], "watch": [], "summary": "数据不足"}

    buy_candidates = []
    avoid = []
    watch = []

    for s in all_sectors:
        # 买入候选: 动量评分>65 + 加速度>0 + 处于初始期或主升浪
        if (s["momentum_score"] > 65 and s["acceleration"] > 0
                and s["phase_key"] in ("initial", "mid")):
            buy_candidates.append(s)
        # 规避: 处于尾期/回调 + 动量在衰减
        elif s["phase_key"] in ("late", "pullback") or (s["momentum_score"] < 35 and s["acceleration"] < -2):
            avoid.append(s)
        # 底部观察: 低迷但跌幅收窄（加速度>0说明跌速放缓）
        elif s["phase_key"] == "bottom" and s["acceleration"] > 0:
            watch.append(s)

    # 生成总结
    n_buy = len(buy_candidates)
    n_avoid = len(avoid)
    if n_buy >= 3:
        summary = f"当前有{n_buy}个行业处于上升通道且动量加速，市场热点较多，轮动活跃。"
    elif n_buy >= 1:
        summary = f"当前仅{n_buy}个行业具备进攻性，热点集中，建议聚焦而非分散。"
    elif n_avoid > len(all_sectors) * 0.4:
        summary = "多数行业动量衰减或回调中，整体偏防御，建议控制仓位等待。"
    else:
        summary = "行业分化不明显，无明确主线，建议观望为主。"

    return {
        "buy_candidates": buy_candidates[:5],
        "avoid": avoid[:5],
        "watch": watch[:5],
        "summary": summary,
    }


def load_sector_data_from_db() -> pd.DataFrame:
    """从数据库加载行业日行情数据"""
    from src.storage.database import SessionLocal
    from sqlalchemy import text

    db = SessionLocal()
    try:
        result = db.execute(text("""
            SELECT sd.sector_code, sd.trade_date, sd.change_pct,
                   sd.up_count, sd.down_count,
                   sd.leader_code, sd.leader_name, sd.leader_pct,
                   si.sector_name
            FROM sector_daily sd
            JOIN sector_info si ON sd.sector_code = si.sector_code
            ORDER BY sd.trade_date DESC, sd.change_pct DESC
        """))
        rows = result.fetchall()
        if not rows:
            return pd.DataFrame()

        df = pd.DataFrame(rows, columns=[
            "sector_code", "trade_date", "change_pct",
            "up_count", "down_count",
            "leader_code", "leader_name", "leader_pct",
            "sector_name"
        ])
        df["trade_date"] = pd.to_datetime(df["trade_date"])
        df["change_pct"] = df["change_pct"].astype(float)
        return df
    finally:
        db.close()
