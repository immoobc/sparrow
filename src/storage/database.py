"""数据库连接管理"""

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.config import settings

_pool_size = settings.effective_db_pool_size
_max_overflow = _pool_size * 2  # 低配: 2+4=6连接, 高配: 5+10=15连接

engine = create_engine(
    settings.database_url,
    pool_size=_pool_size,
    max_overflow=_max_overflow,
    pool_pre_ping=True,
    pool_recycle=1800,  # 30分钟回收空闲连接，减少内存驻留
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
