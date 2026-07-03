"""股票列表采集器 — 获取全市场股票基础信息"""

from datetime import datetime

from sqlalchemy import text
from sqlalchemy.dialects.postgresql import insert

from src.datasource.eastmoney_source import fetch_all_stock_list, fetch_stock_info
from src.datasource.mootdx_source import fetch_stock_list
from src.logger import logger
from src.storage.database import SessionLocal
from src.storage.models import StockBasic


def _is_stock_code(code: str) -> bool:
    """判断是否为A股股票代码（排除基金、债券等）"""
    if not code or len(code) != 6:
        return False
    # A股: 00/30/60/68/83/87 开头
    prefix2 = code[:2]
    return prefix2 in ("00", "30", "60", "68", "83", "87", "00", "02")


def _determine_board(code: str) -> str:
    """根据代码判断板块"""
    if code.startswith("60"):
        return "主板"
    elif code.startswith("00") or code.startswith("02"):
        return "中小板"
    elif code.startswith("30"):
        return "创业板"
    elif code.startswith("68"):
        return "科创板"
    elif code.startswith("8"):
        return "北交所"
    return "其他"


def collect_stock_list() -> int:
    """
    采集全市场股票列表并写入 stock_basic 表。
    使用东财接口获取列表，mootdx补充。

    Returns:
        写入/更新的记录数
    """
    logger.info("开始采集全市场股票列表...")

    # 1. 从东财获取股票列表
    stocks = fetch_all_stock_list()
    logger.info(f"东财返回 {len(stocks)} 只股票")

    if not stocks:
        logger.warning("东财股票列表为空，尝试 mootdx")
        df = fetch_stock_list()
        if df.empty:
            logger.error("所有数据源均无法获取股票列表")
            return 0
        stocks = [
            {"code": row.get("code", ""), "name": row.get("name", ""), "market": row.get("market", "")}
            for _, row in df.iterrows()
        ]

    # 2. 过滤出A股
    a_stocks = [s for s in stocks if _is_stock_code(s["code"])]
    logger.info(f"过滤后 A 股 {len(a_stocks)} 只")

    # 3. 清洗数据（去除 NUL 字符，PG 不允许字符串中包含 \x00）
    for s in a_stocks:
        for key in ("name", "market", "code"):
            if key in s and isinstance(s[key], str):
                s[key] = s[key].replace("\x00", "").strip()

    # 3. 批量写入数据库 (UPSERT)
    db = SessionLocal()
    count = 0
    try:
        for stock in a_stocks:
            code = stock["code"]
            stmt = insert(StockBasic).values(
                code=code,
                name=stock.get("name", ""),
                market=stock.get("market", "sz"),
                board=_determine_board(code),
                is_active=True,
                updated_at=datetime.now(),
            ).on_conflict_do_update(
                index_elements=["code"],
                set_={
                    "name": stock.get("name", ""),
                    "market": stock.get("market", "sz"),
                    "board": _determine_board(code),
                    "is_active": True,
                    "updated_at": datetime.now(),
                },
            )
            db.execute(stmt)
            count += 1

        db.commit()
        logger.info(f"股票列表写入完成: {count} 条")
    except Exception as e:
        db.rollback()
        logger.error(f"股票列表写入失败: {e}")
        raise
    finally:
        db.close()

    return count
