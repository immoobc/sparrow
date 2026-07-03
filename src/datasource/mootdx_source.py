"""通达信数据源 — K线/盘口/财务（TCP 7709，不封IP）"""

import socket
from datetime import date, datetime

import pandas as pd
from mootdx.quotes import Quotes

from src.logger import logger

# 实测可用备选服务器（2026-06 验证）
_TDX_SERVERS = [
    ("119.97.185.59", 7709),
    ("124.70.133.119", 7709),
    ("116.205.183.150", 7709),
    ("123.60.73.44", 7709),
    ("116.205.163.254", 7709),
    ("121.36.225.169", 7709),
    ("123.60.70.228", 7709),
    ("124.71.9.153", 7709),
    ("110.41.147.114", 7709),
    ("124.71.187.122", 7709),
]

_client = None


def _probe(ip: str, port: int, timeout: float = 2.0) -> bool:
    """TCP 握手探测服务器是否可达"""
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except Exception:
        return False


def get_client() -> Quotes:
    """获取 mootdx 客户端（单例复用，规避 0.11.x BESTIP bug）"""
    global _client
    if _client is not None:
        return _client

    # 顺序探测服务器
    for ip, port in _TDX_SERVERS:
        if _probe(ip, port):
            logger.info(f"mootdx 连接服务器: {ip}:{port}")
            _client = Quotes.factory(market="std", server=(ip, port))
            return _client

    # 回退: mootdx 自带测速
    try:
        _client = Quotes.factory(market="std", bestip=True)
        logger.info("mootdx 使用 bestip 模式")
        return _client
    except Exception:
        pass

    try:
        _client = Quotes.factory(market="std")
        return _client
    except Exception as e:
        raise RuntimeError(
            f"所有 mootdx 服务器不可达。请检查网络环境。错误: {e}"
        )


def _get_market(code: str) -> int:
    """股票代码 → 通达信市场 (0=深圳, 1=上海)"""
    if code.startswith(("6", "9")):
        return 1
    # 51/52/58 开头的是上海ETF
    if code.startswith(("51", "52", "58")):
        return 1
    return 0


def fetch_daily_bars(
    code: str, count: int = 800, start: int = 0
) -> pd.DataFrame:
    """
    拉取日K线数据（直接用底层API，规避pandas 3.0兼容问题）。

    Args:
        code: 6位股票代码
        count: 一次拉取的K线条数（最大800）
        start: 起始偏移（0=最新，800=往前第801根开始）

    Returns:
        DataFrame: columns=[datetime, open, close, high, low, vol, amount]
    """
    client = get_client()
    market = _get_market(code)

    # 直接调底层 tdxpy，避免 mootdx to_data 的 pd.to_datetime 报错
    raw_data = client.client.get_security_bars(4, market, code, start, count)
    if not raw_data:
        return pd.DataFrame()

    # 过滤脏数据（无效日期）
    clean = []
    for item in raw_data:
        dt = item.get("datetime", "")
        if not dt or len(dt) < 10:
            continue
        year = item.get("year", 0)
        if year < 1990 or year > 2030:
            continue
        clean.append({
            "datetime": dt[:10],  # 只取日期部分
            "open": item.get("open", 0),
            "close": item.get("close", 0),
            "high": item.get("high", 0),
            "low": item.get("low", 0),
            "vol": item.get("vol", 0),
            "amount": item.get("amount", 0),
        })

    if not clean:
        return pd.DataFrame()

    return pd.DataFrame(clean)


def fetch_all_daily_bars(code: str) -> pd.DataFrame:
    """
    拉取指定股票的全部历史日K线。
    mootdx 每次最多返回800条，需循环拉取。

    Returns:
        全部历史日K线 DataFrame，按日期升序
    """
    all_frames = []
    start = 0
    batch_size = 800

    while True:
        df = fetch_daily_bars(code, count=batch_size, start=start)
        if df is None or df.empty:
            break
        all_frames.append(df)
        if len(df) < batch_size:
            # 拿到的数据不足一批，说明已到头
            break
        start += batch_size

    if not all_frames:
        return pd.DataFrame()

    result = pd.concat(all_frames, ignore_index=True)
    # 去重 + 按日期升序
    if "datetime" in result.columns:
        result = result.drop_duplicates(subset=["datetime"]).sort_values(
            "datetime"
        )
    return result.reset_index(drop=True)


def fetch_stock_list() -> pd.DataFrame:
    """
    获取全市场股票列表。
    通过 mootdx 的 stocks 接口获取。

    Returns:
        DataFrame: columns=[code, name, ...]
    """
    client = get_client()
    # market: 0=深圳, 1=上海
    df_sz = client.stocks(market=0)
    df_sh = client.stocks(market=1)

    frames = []
    if df_sz is not None and not df_sz.empty:
        df_sz["market"] = "sz"
        frames.append(df_sz)
    if df_sh is not None and not df_sh.empty:
        df_sh["market"] = "sh"
        frames.append(df_sh)

    if not frames:
        return pd.DataFrame()

    return pd.concat(frames, ignore_index=True)


def fetch_finance(code: str) -> pd.DataFrame:
    """获取财务快照（37字段季报数据）"""
    client = get_client()
    df = client.finance(symbol=code)
    return df if df is not None else pd.DataFrame()
