"""数据库连接管理"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config import settings

engine = create_engine(
    settings.database_url,
    pool_size=5,
    max_overflow=10,
    pool_pre_ping=True,
    echo=False,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db():
    """获取数据库会话（上下文管理器用法）"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
