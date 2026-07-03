"""同花顺数据源 — 热点归因/北向资金/一致预期（HTTP，不封IP）"""

from datetime import date as _date

import pandas as pd
import requests

from src.logger import logger

HSGT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36",
    "Host": "data.hexin.cn",
    "Referer": "https://data.hexin.cn/",
}

THS_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/117.0.0.0 Safari/537.36"


# ── 北向资金 ────────────────────────────────────────────────


def fetch_northbound_realtime() -> pd.DataFrame:
    """
    沪深股通当日实时分钟流向。
    返回: DataFrame[time, hgt_yi, sgt_yi] (单位：亿元)
    """
    url = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
    try:
        r = requests.get(url, headers=HSGT_HEADERS, timeout=10)
        d = r.json()
    except Exception as e:
        logger.error(f"北向资金请求失败: {e}")
        return pd.DataFrame()

    times = d.get("time", [])
    hgt = d.get("hgt", [])
    sgt = d.get("sgt", [])
    n = len(times)

    return pd.DataFrame({
        "time": times,
        "hgt_yi": (hgt[:n] + [None] * max(0, n - len(hgt)))[:n],
        "sgt_yi": (sgt[:n] + [None] * max(0, n - len(sgt)))[:n],
    })


def fetch_northbound_close() -> dict:
    """
    获取北向资金当日收盘累计净买入。
    Returns:
        {hgt: float, sgt: float} 单位亿元，失败返回空dict
    """
    df = fetch_northbound_realtime()
    if df.empty:
        return {}

    # 取最后一个非空值
    hgt_vals = df["hgt_yi"].dropna()
    sgt_vals = df["sgt_yi"].dropna()

    return {
        "hgt": float(hgt_vals.iloc[-1]) if not hgt_vals.empty else 0,
        "sgt": float(sgt_vals.iloc[-1]) if not sgt_vals.empty else 0,
    }


# ── 强势股热点归因 ──────────────────────────────────────────


def fetch_hot_stocks(trade_date: str = None) -> pd.DataFrame:
    """
    同花顺当日强势股归因。

    Args:
        trade_date: 'YYYY-MM-DD' 格式，None=今天

    Returns:
        DataFrame，含题材归因标签
    """
    if trade_date is None:
        trade_date = _date.today().strftime("%Y-%m-%d")

    url = (
        f"http://zx.10jqka.com.cn/event/api/getharden/"
        f"date/{trade_date}/orderby/date/orderway/desc/charset/GBK/"
    )
    headers = {"User-Agent": THS_UA}

    try:
        r = requests.get(url, headers=headers, timeout=10)
        data = r.json()
    except Exception as e:
        logger.error(f"同花顺热点请求失败: {e}")
        return pd.DataFrame()

    if data.get("errocode", 0) != 0:
        logger.warning(f"同花顺热点错误: {data.get('errormsg', '')}")
        return pd.DataFrame()

    rows = data.get("data") or []
    if not rows:
        return pd.DataFrame()

    df = pd.DataFrame(rows)
    return df
