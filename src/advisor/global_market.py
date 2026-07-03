"""全球市场数据获取 — 美/日/韩/中 + 黄金/美债

数据源: 腾讯财经（免费、不封IP、支持全球指数）

腾讯财经支持的全球指数代码:
- 美股: usQQQQ.OQ(纳斯达克), usDJI.OTC(道琼斯), usINX.OTC(标普500)
- 日韩: jkN225(日经225), jkKS11(韩国综合)
- 欧洲: jkFTSE(富时100), jkGDAXI(德国DAX)
- 商品: hkGC(黄金期货)
- 中国: sh000001(上证), sz399006(创业板), sh000300(沪深300)

注: 腾讯全球指数用 "us"/"jk"/"hk" 前缀
"""

import urllib.request
import json
from datetime import date

from src.logger import logger


# 全球主要资产代码映射
GLOBAL_ASSETS = {
    # A股指数
    "上证指数": "sh000001",
    "沪深300": "sh000300",
    "创业板指": "sz399006",
    "中证500": "sh000905",
    # 美股
    "标普500": "usINX",
    "纳斯达克": "usIXIC",
    "道琼斯": "usDJI",
    # 港股
    "恒生指数": "hkHSI",
    "恒生科技": "hkHSTECH",
    # 中国资产
    "中概互联ETF": "sh513050",
}


def fetch_global_quotes() -> dict:
    """
    一次性拉取全球主要资产的最新行情。

    Returns:
        {
            "上证指数": {"price": 3350, "change_pct": +0.8, "name": "上证指数"},
            "标普500": {"price": 5500, "change_pct": +1.2, ...},
            ...
        }
    """
    codes = list(GLOBAL_ASSETS.values())
    url = "https://qt.gtimg.cn/q=" + ",".join(codes)

    try:
        req = urllib.request.Request(url)
        req.add_header("User-Agent", "Mozilla/5.0")
        resp = urllib.request.urlopen(req, timeout=10)
        data = resp.read().decode("gbk", errors="ignore")
    except Exception as e:
        logger.warning(f"全球行情获取失败: {e}")
        return {}

    # 解析腾讯格式
    quotes = {}
    code_to_name = {v: k for k, v in GLOBAL_ASSETS.items()}

    for line in data.strip().split(";"):
        if not line.strip() or "=" not in line or '"' not in line:
            continue
        raw_key = line.split("=")[0].strip()
        # 提取代码部分: v_usINX="..." → usINX
        key = raw_key.split("_")[-1] if "_" in raw_key else raw_key
        vals = line.split('"')[1].split("~")
        if len(vals) < 35:
            continue

        try:
            price = float(vals[3]) if vals[3] else 0
            change_pct = float(vals[32]) if vals[32] else 0
            name = vals[1]
        except (ValueError, IndexError):
            continue

        # 匹配中文名
        matched_name = code_to_name.get(key, None)
        if matched_name:
            quotes[matched_name] = {
                "price": price,
                "change_pct": change_pct,
                "name": name,
                "code": key,
            }

    return quotes


def detect_significant_moves(quotes: dict, threshold: float = 2.0) -> list[dict]:
    """
    检测显著异动的资产（日涨跌幅绝对值超过阈值）。

    Args:
        quotes: fetch_global_quotes() 的返回值
        threshold: 异动阈值（默认 ±2%）

    Returns:
        [{"name": "纳斯达克", "change_pct": -3.2, "direction": "down", "code": "usINX"}, ...]
    """
    significant = []
    for asset_name, data in quotes.items():
        change_pct = data.get("change_pct", 0.0)
        if abs(change_pct) > threshold:
            direction = "up" if change_pct > 0 else "down"
            significant.append({
                "name": asset_name,
                "change_pct": change_pct,
                "direction": direction,
                "code": data.get("code", ""),
            })
    return significant


def get_global_treemap_data() -> list[dict]:
    """
    生成全球资产 Treemap 数据。

    Returns:
        [{name, category, value(size), change_pct(color)}, ...]
    """
    quotes = fetch_global_quotes()
    if not quotes:
        return []

    # 按分类组织
    categories = {
        "A股": ["上证指数", "沪深300", "创业板指", "中证500"],
        "美股": ["标普500", "纳斯达克", "道琼斯"],
        "港股": ["恒生指数", "恒生科技"],
        "中国资产": ["中概互联ETF"],
    }

    treemap_data = []
    for category, assets in categories.items():
        for asset_name in assets:
            if asset_name in quotes:
                q = quotes[asset_name]
                treemap_data.append({
                    "name": asset_name,
                    "category": category,
                    "value": 1,  # 等大小方块
                    "change_pct": q["change_pct"],
                    "price": q["price"],
                })

    return treemap_data


def get_sector_treemap_data() -> list[dict]:
    """
    生成A股行业板块 Treemap 数据。

    Returns:
        [{name, category, value, change_pct}, ...]
    """
    try:
        from src.advisor.sector_analyzer import load_sector_data_from_db
        sector_df = load_sector_data_from_db()
        if sector_df.empty:
            return []

        latest_date = sector_df["trade_date"].max()
        today = sector_df[sector_df["trade_date"] == latest_date]

        treemap_data = []
        for _, row in today.iterrows():
            treemap_data.append({
                "name": row["sector_name"],
                "category": "A股行业",
                "value": max(abs(float(row["change_pct"])), 0.1),  # 面积=|涨跌幅|
                "change_pct": float(row["change_pct"]),
                "leader": row.get("leader_name", ""),
            })
        return treemap_data
    except Exception:
        return []
