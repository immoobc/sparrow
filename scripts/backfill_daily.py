"""历史日K线回填脚本

用法:
    # 回填全市场（跳过已有数据的票）
    python -m scripts.backfill_daily

    # 回填指定股票
    python -m scripts.backfill_daily --codes 600519,000858,688017

    # 强制重新回填（不跳过）
    python -m scripts.backfill_daily --force
"""

import argparse
import sys
from pathlib import Path

# 添加项目根目录到 path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.collector.daily_kline_collector import backfill_all, collect_full_history
from src.logger import logger
from src.storage.database import SessionLocal


def get_all_stock_codes() -> list[str]:
    """从 stock_basic 表获取全部活跃股票代码"""
    db = SessionLocal()
    try:
        from sqlalchemy import text

        result = db.execute(
            text("SELECT code FROM stock_basic WHERE is_active = TRUE ORDER BY code")
        )
        codes = [row[0].strip() for row in result]
        return codes
    finally:
        db.close()


def main():
    parser = argparse.ArgumentParser(description="历史日K线回填")
    parser.add_argument(
        "--codes", type=str, help="指定股票代码,逗号分隔", default=""
    )
    parser.add_argument(
        "--force", action="store_true", help="强制重新回填(不跳过已有数据)"
    )
    args = parser.parse_args()

    if args.codes:
        codes = [c.strip() for c in args.codes.split(",") if c.strip()]
        logger.info(f"回填指定股票: {codes}")
        for code in codes:
            try:
                n = collect_full_history(code)
                logger.info(f"[{code}] 完成，共 {n} 条")
            except Exception as e:
                logger.error(f"[{code}] 失败: {e}")
    else:
        codes = get_all_stock_codes()
        if not codes:
            logger.error("stock_basic 表为空，请先运行股票列表采集")
            sys.exit(1)
        result = backfill_all(codes, skip_existing=not args.force)
        logger.info(f"回填结果: {result}")


if __name__ == "__main__":
    main()
