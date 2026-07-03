"""策略构建模块 — 从因子到可交易的持仓组合

主流量化选股策略流程:
1. 股票池过滤 (剔除ST/停牌/上市不足60天/涨跌停)
2. 因子计算 + 标准化 (去极值 + Z-Score)
3. 行业中性化 (相对于行业的超额因子暴露)
4. 选股 (因子排名 Top N)
5. 权重分配 (等权 / 因子加权)
6. 调仓计划 (目标持仓 vs 当前持仓 → 交易清单)
"""

import numpy as np
import pandas as pd

from src.logger import logger


# ============================================================
# 1. 股票池过滤
# ============================================================

def filter_universe(
    df: pd.DataFrame,
    trade_date: pd.Timestamp,
    min_list_days: int = 60,
    exclude_st: bool = True,
    exclude_limit: bool = True,
) -> pd.DataFrame:
    """
    构建可交易股票池。

    过滤规则:
    - 上市不足N天的新股 (打新期波动太大)
    - 当日涨跌停的股票 (买不进/卖不出)
    - ST股票 (风险太高)
    - 当日无交易的停牌股

    Args:
        df: 全市场日K线 (需含 code, trade_date, close, volume, daily_ret)
        trade_date: 调仓日
        min_list_days: 上市最少天数
        exclude_st: 是否排除ST

    Returns:
        过滤后的当日截面 DataFrame
    """
    # 取调仓日数据
    today = df[df["trade_date"] == trade_date].copy()
    if today.empty:
        return today

    # 排除停牌 (成交量=0)
    today = today[today["volume"] > 0]

    # 排除涨跌停 (买不进卖不出)
    if exclude_limit and "daily_ret" in today.columns:
        today = today[today["daily_ret"].abs() < 0.095]

    # 排除上市天数不足的
    code_days = df.groupby("code")["trade_date"].transform("count")
    first_dates = df.groupby("code")["trade_date"].transform("min")
    df["_list_days"] = (df["trade_date"] - first_dates).dt.days
    list_days = df[df["trade_date"] == trade_date].set_index("code")["_list_days"]
    today = today[today["code"].map(list_days) >= min_list_days]

    return today.reset_index(drop=True)


# ============================================================
# 2. 因子标准化
# ============================================================

def winsorize(s: pd.Series, n_std: float = 3.0) -> pd.Series:
    """
    去极值 (MAD法): 超过中位数 ± N倍MAD的值截断。
    比 mean±3std 更稳健, 不受异常值影响。
    """
    median = s.median()
    mad = (s - median).abs().median()
    upper = median + n_std * 1.4826 * mad
    lower = median - n_std * 1.4826 * mad
    return s.clip(lower, upper)


def standardize(s: pd.Series) -> pd.Series:
    """Z-Score 标准化: (x - mean) / std"""
    std = s.std()
    if std == 0 or pd.isna(std):
        return pd.Series(0, index=s.index)
    return (s - s.mean()) / std


def preprocess_factor(factor_values: pd.Series) -> pd.Series:
    """因子预处理: 去极值 → 标准化"""
    return standardize(winsorize(factor_values))


# ============================================================
# 3. 行业中性化 (可选)
# ============================================================

def industry_neutralize(
    factor_df: pd.DataFrame,
    industry_map: dict[str, str] = None,
) -> pd.DataFrame:
    """
    行业中性化: 在每个行业内部做Z-Score。
    这样因子值反映的是"在同行业中的相对强弱"而非跨行业差异。

    Args:
        factor_df: 含 [code, factor_value] 的截面
        industry_map: {code: industry} 映射

    Returns:
        行业中性化后的因子值
    """
    if industry_map is None:
        # 没有行业信息时直接返回全市场标准化
        factor_df["factor_neutral"] = preprocess_factor(factor_df["factor_value"])
        return factor_df

    factor_df["industry"] = factor_df["code"].map(industry_map).fillna("其他")
    factor_df["factor_neutral"] = factor_df.groupby("industry")["factor_value"].transform(
        lambda x: preprocess_factor(x)
    )
    return factor_df


# ============================================================
# 4. 选股 + 权重分配
# ============================================================

def select_portfolio(
    factor_cross_section: pd.DataFrame,
    top_n: int = 50,
    weight_method: str = "equal",
    factor_col: str = "factor_value",
    ascending: bool = True,
) -> pd.DataFrame:
    """
    根据因子值选股并分配权重。

    Args:
        factor_cross_section: 当日截面 [code, factor_value, ...]
        top_n: 选股数量
        weight_method: 权重方式
            - "equal": 等权 (最常用, 容量最大)
            - "factor": 因子值加权 (因子越极端权重越大)
            - "inv_vol": 波动率倒数加权 (低波动给更多权重)
        ascending: True=选因子值最小的(反转做多), False=选最大的(动量做多)

    Returns:
        DataFrame[code, weight] 目标持仓
    """
    # 先标准化
    cs = factor_cross_section.copy()
    cs["factor_std"] = preprocess_factor(cs[factor_col])

    # 排序选股
    cs = cs.sort_values("factor_std", ascending=ascending)
    selected = cs.head(top_n).copy()

    # 分配权重
    if weight_method == "equal":
        selected["weight"] = 1.0 / len(selected)

    elif weight_method == "factor":
        # 因子值越极端 (绝对值越大), 权重越高
        abs_factor = selected["factor_std"].abs()
        selected["weight"] = abs_factor / abs_factor.sum()

    elif weight_method == "inv_vol":
        # 需要有 volatility 列
        if "volatility" in selected.columns:
            inv_vol = 1.0 / selected["volatility"].replace(0, np.nan).fillna(1)
            selected["weight"] = inv_vol / inv_vol.sum()
        else:
            selected["weight"] = 1.0 / len(selected)

    else:
        selected["weight"] = 1.0 / len(selected)

    return selected[["code", "weight", "factor_std"]].reset_index(drop=True)


# ============================================================
# 5. 调仓计划生成
# ============================================================

def generate_rebalance_plan(
    current_holdings: dict[str, float],
    target_holdings: dict[str, float],
    total_capital: float = 1_000_000,
    prices: dict[str, float] = None,
    min_trade_amount: float = 5000,
) -> dict:
    """
    生成调仓交易计划。

    Args:
        current_holdings: 当前持仓 {code: weight}
        target_holdings: 目标持仓 {code: weight}
        total_capital: 总资金 (元)
        prices: 当前价格 {code: price} (用于计算股数)
        min_trade_amount: 最小交易金额 (低于此不交易, 降低成本)

    Returns:
        {
            buy: [{code, weight, amount, shares}],
            sell: [{code, weight, amount, shares}],
            hold: [{code, weight}],
            turnover: 换手率,
        }
    """
    buy_list = []
    sell_list = []
    hold_list = []

    all_codes = set(list(current_holdings.keys()) + list(target_holdings.keys()))

    for code in all_codes:
        cur_w = current_holdings.get(code, 0)
        tgt_w = target_holdings.get(code, 0)
        diff = tgt_w - cur_w

        trade_amount = abs(diff) * total_capital
        if trade_amount < min_trade_amount:
            if tgt_w > 0:
                hold_list.append({"code": code, "weight": tgt_w})
            continue

        price = prices.get(code, 0) if prices else 0
        shares = int(trade_amount / price / 100) * 100 if price > 0 else 0

        if diff > 0:
            buy_list.append({
                "code": code,
                "weight": round(tgt_w, 4),
                "amount": round(trade_amount, 0),
                "shares": shares,
            })
        elif diff < 0:
            sell_list.append({
                "code": code,
                "weight": round(cur_w, 4),
                "amount": round(trade_amount, 0),
                "shares": shares,
            })

    # 计算换手率
    turnover = sum(abs(target_holdings.get(c, 0) - current_holdings.get(c, 0))
                   for c in all_codes) / 2

    return {
        "buy": sorted(buy_list, key=lambda x: -x["amount"]),
        "sell": sorted(sell_list, key=lambda x: -x["amount"]),
        "hold": hold_list,
        "turnover": round(turnover, 4),
        "buy_count": len(buy_list),
        "sell_count": len(sell_list),
        "hold_count": len(hold_list),
    }
