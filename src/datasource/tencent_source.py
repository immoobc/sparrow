"""腾讯财经数据源 — PE/PB/市值/实时行情（HTTP，不封IP）"""

import urllib.request
from typing import Optional

from src.logger import logger


def get_prefix(code: str) -> str:
    """6位代码 → 市场前缀"""
    if code.startswith(("6", "9")):
        return "sh"
    elif code.startswith("8"):
        return "bj"
    else:
        return "sz"


def fetch_realtime_quotes(codes: list[str]) -> dict[str, dict]:
    """
    批量拉取腾讯财经实时行情。

    Args:
        codes: 6位股票代码列表，如 ["688017", "300476"]
               也支持指数和ETF

    Returns:
        {code: {name, price, pe_ttm, pb, mcap_yi, ...}}
    """
    if not codes:
        return {}

    prefixed = [f"{get_prefix(c)}{c}" for c in codes]
    url = "https://qt.gtimg.cn/q=" + ",".join(prefixed)

    req = urllib.request.Request(url)
    req.add_header("User-Agent", "Mozilla/5.0")

    try:
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode("gbk")
    except Exception as e:
        logger.error(f"腾讯行情请求失败: {e}")
        return {}

    result = {}
    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        key = line.split("=")[0].split("_")[-1]
        vals = line.split('"')[1].split("~")
        if len(vals) < 53:
            continue

        code = key[2:]  # 去掉 sh/sz 前缀
        try:
            result[code] = {
                "name": vals[1],
                "price": float(vals[3]) if vals[3] else 0,
                "last_close": float(vals[4]) if vals[4] else 0,
                "open": float(vals[5]) if vals[5] else 0,
                "change_amt": float(vals[31]) if vals[31] else 0,
                "change_pct": float(vals[32]) if vals[32] else 0,
                "high": float(vals[33]) if vals[33] else 0,
                "low": float(vals[34]) if vals[34] else 0,
                "amount_wan": float(vals[37]) if vals[37] else 0,
                "turnover_pct": float(vals[38]) if vals[38] else 0,
                "pe_ttm": float(vals[39]) if vals[39] else 0,
                "amplitude_pct": float(vals[43]) if vals[43] else 0,
                "mcap_yi": float(vals[44]) if vals[44] else 0,
                "float_mcap_yi": float(vals[45]) if vals[45] else 0,
                "pb": float(vals[46]) if vals[46] else 0,
                "limit_up": float(vals[47]) if vals[47] else 0,
                "limit_down": float(vals[48]) if vals[48] else 0,
                "vol_ratio": float(vals[49]) if vals[49] else 0,
                "pe_static": float(vals[52]) if vals[52] else 0,
            }
        except (ValueError, IndexError) as e:
            logger.warning(f"解析 {code} 行情失败: {e}")
            continue

    return result


def fetch_batch_quotes(
    codes: list[str], batch_size: int = 50
) -> dict[str, dict]:
    """
    分批拉取行情（腾讯接口单次建议不超过50只）。

    Args:
        codes: 全量代码列表
        batch_size: 每批数量

    Returns:
        合并后的行情字典
    """
    all_quotes = {}
    for i in range(0, len(codes), batch_size):
        batch = codes[i : i + batch_size]
        quotes = fetch_realtime_quotes(batch)
        all_quotes.update(quotes)
    return all_quotes
