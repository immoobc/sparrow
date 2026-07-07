"""东方财富数据源 — 资金面/龙虎榜/研报等独有数据（内置限流防封）"""

import random
import time

import requests

from src.config import settings
from src.logger import logger

# ── 全局限流 + 会话复用 ──────────────────────────────────────
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"

_em_session = requests.Session()
_em_session.headers.update({"User-Agent": UA})
_em_last_call: float = 0.0
_em_daily_count: int = 0
_em_daily_reset: float = 0.0


def _reset_em_session():
    """重建 HTTP session（解决服务端主动断开连接的问题）"""
    global _em_session
    try:
        _em_session.close()
    except Exception:
        pass
    _em_session = requests.Session()
    _em_session.headers.update({"User-Agent": UA})


def em_get(
    url: str,
    params: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
    **kwargs,
) -> requests.Response:
    """
    东财统一请求入口：自动节流 + 复用session + 默认UA。
    所有 eastmoney.com 接口都应通过此函数请求。
    """
    global _em_last_call, _em_daily_count, _em_daily_reset

    # 每日计数重置
    now = time.time()
    if now - _em_daily_reset > 86400:
        _em_daily_count = 0
        _em_daily_reset = now

    # 每日上限检查
    if _em_daily_count >= settings.em_daily_limit:
        raise RuntimeError(
            f"东财每日请求已达上限 {settings.em_daily_limit}，明日重置"
        )

    # 限流等待
    wait = settings.em_min_interval - (now - _em_last_call)
    if wait > 0:
        time.sleep(wait + random.uniform(0.1, 0.5))

    try:
        resp = _em_session.get(
            url, params=params, headers=headers, timeout=timeout, **kwargs
        )
        _em_daily_count += 1
        return resp
    finally:
        _em_last_call = time.time()


def eastmoney_datacenter(
    report_name: str,
    columns: str = "ALL",
    filter_str: str = "",
    page_size: int = 50,
    sort_columns: str = "",
    sort_types: str = "-1",
    page_number: int = 1,
) -> list[dict]:
    """东财数据中心统一查询 — 龙虎榜/解禁/融资融券/大宗/股东户数/分红"""
    params = {
        "reportName": report_name,
        "columns": columns,
        "filter": filter_str,
        "pageNumber": str(page_number),
        "pageSize": str(page_size),
        "sortColumns": sort_columns,
        "sortTypes": sort_types,
        "source": "WEB",
        "client": "WEB",
    }
    try:
        r = em_get(DATACENTER_URL, params=params, timeout=15)
        d = r.json()
        if d.get("result") and d["result"].get("data"):
            return d["result"]["data"]
    except Exception as e:
        logger.error(f"东财 datacenter 请求失败 [{report_name}]: {e}")
    return []


def fetch_stock_info(code: str) -> dict:
    """
    东财个股基本面信息（行业/总股本/流通股/市值/上市日期）。
    """
    market_code = 1 if code.startswith("6") else 0
    url = "https://push2.eastmoney.com/api/qt/stock/get"
    params = {
        "fltt": "2",
        "invt": "2",
        "fields": "f57,f58,f84,f85,f127,f116,f117,f189,f43",
        "secid": f"{market_code}.{code}",
    }
    headers = {"User-Agent": UA}
    try:
        r = em_get(url, params=params, headers=headers, timeout=10)
        d = r.json().get("data", {})
        return {
            "code": d.get("f57", ""),
            "name": d.get("f58", ""),
            "industry": d.get("f127", ""),
            "total_shares": d.get("f84", 0),
            "float_shares": d.get("f85", 0),
            "mcap": d.get("f116", 0),
            "float_mcap": d.get("f117", 0),
            "list_date": str(d.get("f189", "")),
            "price": d.get("f43", 0),
        }
    except Exception as e:
        logger.error(f"东财个股信息请求失败 [{code}]: {e}")
        return {}


def fetch_all_stock_list() -> list[dict]:
    """
    通过东财接口获取全市场A股列表。
    用于补充 mootdx 拿不到的字段（行业、上市日期等）。
    """
    url = "https://push2.eastmoney.com/api/qt/clist/get"
    all_stocks = []

    for fs in ["m:0+t:6,m:0+t:80", "m:1+t:2,m:1+t:23"]:
        # 深圳(主板+创业板) + 上海(主板+科创板)
        params = {
            "pn": "1",
            "pz": "10000",
            "po": "1",
            "np": "1",
            "fltt": "2",
            "invt": "2",
            "fs": fs,
            "fields": "f12,f14,f13,f116,f117,f84,f85",
        }
        try:
            r = em_get(url, params=params, timeout=20)
            d = r.json()
            items = d.get("data", {}).get("diff", [])
            for item in items:
                code = item.get("f12", "")
                if not code or len(code) != 6:
                    continue
                all_stocks.append(
                    {
                        "code": code,
                        "name": item.get("f14", ""),
                        "market": "sh"
                        if item.get("f13") == 1
                        else "sz",
                    }
                )
        except Exception as e:
            logger.error(f"东财股票列表请求失败: {e}")

    return all_stocks
