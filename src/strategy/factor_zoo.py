"""因子图鉴 — 定义所有可研究的因子

每个因子包含:
- 名称/分类
- 通俗解释 (小白能懂)
- 数学定义
- 学术依据
- A股实证特点
- 适用场景/局限
- 计算函数
"""

import numpy as np
import pandas as pd


# ══════════════════════════════════════════════════════════════
# 因子定义库
# ══════════════════════════════════════════════════════════════

FACTOR_CATALOG = {
    "reversal_20d": {
        "name": "20日反转",
        "category": "价格动量",
        "short_desc": "过去20天跌最多的股票，未来更容易反弹",
        "full_desc": """
### 核心逻辑
散户主导的A股市场，情绪容易走极端。一只股票短期暴跌后，往往是恐慌过度导致的超跌，
之后有均值回归的需求。反之，短期暴涨的可能是游资炒作，容易冲高回落。

### 数学定义
```
因子值 = 今日收盘价 / 20天前收盘价 - 1
选股: 选因子值最低（跌最多）的那批
```

### 学术依据
- De Bondt & Thaler (1985): "过度反应假说"——投资者对坏消息过度反应导致超跌
- Jegadeesh (1990): 发现美股月度反转效应，1周~1个月内反转显著
- 中国市场: 陈信元(2004)验证A股月度反转强于美股，与散户占比正相关

### A股特点
- 1-4周反转最有效，超过2个月效果消失
- 小票反转强于大票（散户参与度高）
- 熊市中反转因子回撤大（系统性风险下全跌）
- ICIR ≈ -0.5，属于有效因子

### 适用场景
✅ 适合: 震荡市、结构性行情
⚠️ 不适合: 单边下跌的熊市（会一直"接飞刀"）
""",
        "params": {"lookback": 20},
        "ascending": True,  # True=选因子最低的
    },

    "momentum_60d": {
        "name": "60日动量",
        "category": "价格动量",
        "short_desc": "过去60天涨势最强的股票，未来大概率继续涨",
        "full_desc": """
### 核心逻辑
大资金（公募/保险/外资）买入一只股票不是一天完成的，需要持续建仓几个月。
所以股价上涨的趋势一旦形成，往往会持续一段时间。这就是"动量效应"——强者恒强。

### 数学定义
```
因子值 = 今日收盘价 / 60天前收盘价 - 1
选股: 选因子值最高（涨最多）的那批
```

### 学术依据
- Jegadeesh & Titman (1993): 经典论文，发现3-12个月动量效应普遍存在
- Carhart (1997): 将动量因子纳入四因子模型（Fama-French三因子+动量）
- Asness et al. (2013): "Value and Momentum Everywhere"——动量效应在全球市场普遍存在

### A股特点
- 中期（2-6个月）动量在A股有效但弱于美股
- 与反转因子负相关——两者在不同时间尺度有效
- 大票动量强于小票（机构持续买入推动）
- 2015/2021 之类的牛市中动量暴利，但见顶时回撤也极大
- ICIR ≈ 0.3-0.4

### 适用场景
✅ 适合: 牛市、趋势明确的行情
⚠️ 不适合: 震荡市（反复假突破）、牛转熊的拐点
""",
        "params": {"lookback": 60},
        "ascending": False,  # False=选因子最高的
    },

    "volatility_20d": {
        "name": "20日波动率",
        "category": "风险因子",
        "short_desc": "波动越小的股票，长期收益反而更好（低波异象）",
        "full_desc": """
### 核心逻辑
理论上高风险应该高回报，但现实中恰恰相反——低波动的股票长期跑赢高波动的。
这叫"低波异象"。原因: 高波动的票往往是被炒作的垃圾股，涨得快跌得更快；
低波动的往往是质地好的白马股，稳步向上。

### 数学定义
```
因子值 = 过去20日收益率的标准差
选股: 选因子值最低（波动最小）的那批
```

### 学术依据
- Ang et al. (2006): 发现美股高波动组显著跑输低波动组
- Baker et al. (2011): "低波动异象"全面实证
- Blitz & van Vliet (2007): 全球市场验证

### A股特点
- A股低波异象非常显著（ICIR绝对值可达0.6+）
- 低波动因子与市值因子高度相关（大票波动低）
- 熊市中低波动股票防守性强，回撤小
- 牛市中低波动会跑输（不够刺激）

### 适用场景
✅ 适合: 追求稳健收益、控制回撤
⚠️ 不适合: 想赚快钱的激进策略
""",
        "params": {"lookback": 20},
        "ascending": True,  # True=选波动最低的
    },

    "volume_shrink": {
        "name": "缩量因子",
        "category": "成交量",
        "short_desc": "成交量萎缩+股价不跌=筹码锁定，后续容易上涨",
        "full_desc": """
### 核心逻辑
一只股票在下跌过程中如果成交量持续萎缩，说明卖盘枯竭（想卖的都卖完了）。
这种情况叫"缩量企稳"——筹码被锁定在坚定持有者手中，一旦有增量资金进来，
很容易推动股价上涨。

### 数学定义
```
因子值 = 最近5日平均成交量 / 过去60日平均成交量
选股: 选因子值最低（缩量最严重）的那批
附加条件: 近5日跌幅<5%（确认"不跌"，排除暴跌中的缩量）
```

### 学术依据
- Campbell, Grossman & Wang (1993): 成交量携带信息
- Gervais, Kaniel & Mingelgrin (2001): 异常高量后收益显著
- 技术分析经典: "缩量止跌→放量上涨"

### A股特点
- 缩量因子在A股有效，尤其在底部区域
- 需要和价格因子配合使用（单独用效果一般）
- 缩量+超跌（反转因子配合）= 强组合因子
- 连板/涨停股缩量意义不同（锁仓效应）

### 适用场景
✅ 适合: 寻找底部企稳的股票、和反转因子组合
⚠️ 不适合: 单独使用（缩量的原因很多，不一定是好事）
""",
        "params": {"lookback_short": 5, "lookback_long": 60},
        "ascending": True,
    },

    "reversal_momentum_combo": {
        "name": "反转+动量组合",
        "category": "组合因子",
        "short_desc": "短期超跌 + 中期趋势向上 = 强势回调买入",
        "full_desc": """
### 核心逻辑
单纯买超跌的（反转）风险是"接飞刀"——它可能继续跌。
单纯买趋势强的（动量）风险是"追高"——它可能见顶。

把两者组合: **中期趋势向上的票，短期出现回调 → 这是"强势回调"的买点。**

类比: 一只股票过去3个月涨了30%（说明基本面好/资金认可），
但最近2周跌了10%（短期获利了结），这时候买入更安全。

### 数学定义
```
因子1: 短期反转 = 过去10日收益率 (选负的=近期下跌)
因子2: 中期动量 = 过去60日收益率 (选正的=中期上涨)
组合: 先筛选60日动量>0的股票池，再在其中选10日跌幅最大的
```

### 为什么这样组合有效?
- 中期动量>0 确保股票的"大方向"是向上的（不是垃圾股）
- 短期回调 提供更好的买入价格（不追高）
- 逻辑自洽: 好票的短期回调 = 恐慌给的便宜筹码

### A股特点
- 组合因子效果 > 单因子（学术共识）
- 这个组合的夏普比率通常比单反转高0.2-0.3
- 回撤也更可控（不会买到一路跌的垃圾股）

### 适用场景
✅ 各种市况下都相对稳健
⚠️ 纯熊市中效果也会打折（60日动量筛选后股票池变小）
""",
        "params": {"short_lookback": 10, "long_lookback": 60},
        "ascending": True,
    },
}


# ══════════════════════════════════════════════════════════════
# 因子计算函数
# ══════════════════════════════════════════════════════════════

def compute_factor(df: pd.DataFrame, factor_id: str) -> pd.DataFrame:
    """
    计算指定因子。

    Args:
        df: 全市场日K线 (需含 code, trade_date, close, volume)
        factor_id: 因子ID (对应 FACTOR_CATALOG 的 key)

    Returns:
        原 df 新增 "factor" 列
    """
    df = df.sort_values(["code", "trade_date"]).copy()

    if factor_id == "reversal_20d":
        df["factor"] = df.groupby("code")["close"].pct_change(20)

    elif factor_id == "momentum_60d":
        df["factor"] = df.groupby("code")["close"].pct_change(60)

    elif factor_id == "volatility_20d":
        df["daily_ret"] = df.groupby("code")["close"].pct_change()
        df["factor"] = df.groupby("code")["daily_ret"].transform(
            lambda x: x.rolling(20).std()
        )

    elif factor_id == "volume_shrink":
        df["vol_short"] = df.groupby("code")["volume"].transform(
            lambda x: x.rolling(5).mean()
        )
        df["vol_long"] = df.groupby("code")["volume"].transform(
            lambda x: x.rolling(60).mean()
        )
        df["factor"] = df["vol_short"] / df["vol_long"].replace(0, np.nan)
        # 附加条件: 近5日跌幅不超过5% (排除暴跌缩量)
        df["ret5"] = df.groupby("code")["close"].pct_change(5)
        df.loc[df["ret5"] < -0.05, "factor"] = np.nan

    elif factor_id == "reversal_momentum_combo":
        df["short_ret"] = df.groupby("code")["close"].pct_change(10)
        df["long_ret"] = df.groupby("code")["close"].pct_change(60)
        # 只保留中期动量>0的
        df["factor"] = df["short_ret"]
        df.loc[df["long_ret"] <= 0, "factor"] = np.nan

    else:
        raise ValueError(f"未知因子: {factor_id}")

    return df


def run_factor_backtest(
    df: pd.DataFrame,
    factor_id: str,
    hold_days: int = 20,
    n_groups: int = 5,
) -> dict:
    """
    通用因子分层回测。

    Returns:
        {
            group_navs: {Q1: [...], ...Q5: [...]},
            nav_dates: [...],
            ic_series: [(date, ic), ...],
            meta: 因子目录信息,
        }
    """
    meta = FACTOR_CATALOG.get(factor_id, {})
    ascending = meta.get("ascending", True)

    # 计算因子
    df = compute_factor(df, factor_id)

    # 排除涨跌停
    df["daily_ret"] = df.groupby("code")["close"].pct_change()
    df = df[df["daily_ret"].abs() < 0.095]

    # 未来收益 (用于IC)
    df["future_ret"] = df.groupby("code")["close"].shift(-hold_days) / df["close"] - 1

    all_dates = sorted(df["trade_date"].unique())
    rb_dates = all_dates[::hold_days]

    ret_pivot = df.pivot_table(index="trade_date", columns="code", values="daily_ret")

    # 分层回测
    group_navs = {f"Q{i+1}": [1.0] for i in range(n_groups)}
    nav_dates = [all_dates[0]]
    ic_series = []

    for i in range(len(rb_dates) - 1):
        cross = df[df["trade_date"] == rb_dates[i]].dropna(subset=["factor"])
        cross = cross[cross["volume"] > 0]

        if len(cross) < n_groups * 20:
            continue

        # IC
        if "future_ret" in cross.columns:
            valid_ic = cross.dropna(subset=["future_ret"])
            if len(valid_ic) > 50:
                rank_ic = valid_ic["factor"].corr(valid_ic["future_ret"], method="spearman")
                ic_series.append((rb_dates[i], rank_ic))

        # 分组
        try:
            cross["group"] = pd.qcut(
                cross["factor"], q=n_groups,
                labels=[f"Q{j+1}" for j in range(n_groups)],
                duplicates="drop"
            )
        except ValueError:
            continue

        hold = ret_pivot.loc[
            (ret_pivot.index > rb_dates[i]) & (ret_pivot.index <= rb_dates[i+1])
        ]
        if hold.empty:
            continue

        for g in range(n_groups):
            gn = f"Q{g+1}"
            codes = cross[cross["group"] == gn]["code"].tolist()
            valid = [c for c in codes if c in hold.columns]
            if valid:
                period_ret = (1 + hold[valid].mean(axis=1)).prod() - 1
            else:
                period_ret = 0
            group_navs[gn].append(group_navs[gn][-1] * (1 + period_ret))

        nav_dates.append(rb_dates[i+1])

    return {
        "group_navs": group_navs,
        "nav_dates": nav_dates,
        "ic_series": ic_series,
        "meta": meta,
        "ascending": ascending,
    }
