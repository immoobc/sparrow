"""数据库初始化脚本 — 创建数据库和表结构"""

import subprocess
import sys
from pathlib import Path

from src.config import settings
from src.logger import logger


def create_database():
    """创建 PostgreSQL 数据库（如果不存在）"""
    # 从 DATABASE_URL 解析数据库名
    db_name = settings.database_url.rsplit("/", 1)[-1]
    db_url_without_name = settings.database_url.rsplit("/", 1)[0]

    logger.info(f"准备创建数据库: {db_name}")

    from sqlalchemy import create_engine, text

    # 连接 postgres 默认库来创建新库
    engine = create_engine(f"{db_url_without_name}/postgres", isolation_level="AUTOCOMMIT")
    with engine.connect() as conn:
        result = conn.execute(
            text("SELECT 1 FROM pg_database WHERE datname = :name"),
            {"name": db_name},
        )
        if result.fetchone():
            logger.info(f"数据库 {db_name} 已存在")
        else:
            conn.execute(text(f'CREATE DATABASE "{db_name}"'))
            logger.info(f"数据库 {db_name} 创建成功")
    engine.dispose()


def execute_init_sql():
    """执行建表 SQL 脚本"""
    sql_path = Path(__file__).parent / "init_db.sql"
    if not sql_path.exists():
        logger.error(f"建表脚本不存在: {sql_path}")
        return False

    logger.info("执行建表脚本...")

    from sqlalchemy import create_engine, text

    engine = create_engine(settings.database_url)
    with engine.connect() as conn:
        sql_content = sql_path.read_text(encoding="utf-8")
        conn.execute(text(sql_content))
        conn.commit()

    engine.dispose()
    logger.info("建表完成")
    return True


def main():
    """完整的数据库初始化流程"""
    logger.info("=" * 50)
    logger.info("Sparrow 数据库初始化")
    logger.info("=" * 50)

    try:
        # 1. 创建数据库
        create_database()

        # 2. 执行建表
        execute_init_sql()

        logger.info("✓ 数据库初始化完成!")
        logger.info(f"  连接地址: {settings.database_url}")

    except Exception as e:
        logger.error(f"初始化失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
