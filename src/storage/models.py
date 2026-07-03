"""SQLAlchemy ORM 模型定义 — Phase 1 核心表"""

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


# ============================================================
# Layer 0: 元数据层
# ============================================================


class StockBasic(Base):
    """股票基础信息"""

    __tablename__ = "stock_basic"

    code: Mapped[str] = mapped_column(String(6), primary_key=True)
    name: Mapped[str] = mapped_column(String(20), nullable=False)
    market: Mapped[str] = mapped_column(String(4), nullable=False)  # sh/sz/bj
    board: Mapped[str | None] = mapped_column(String(10))
    industry_l1: Mapped[str | None] = mapped_column(String(30))
    industry_l2: Mapped[str | None] = mapped_column(String(30))
    list_date: Mapped[date | None] = mapped_column(Date)
    delist_date: Mapped[date | None] = mapped_column(Date)
    total_shares: Mapped[int | None] = mapped_column(BigInteger)
    float_shares: Mapped[int | None] = mapped_column(BigInteger)
    is_st: Mapped[bool] = mapped_column(Boolean, default=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class TradeCalendar(Base):
    """交易日历"""

    __tablename__ = "trade_calendar"

    cal_date: Mapped[date] = mapped_column(Date, primary_key=True)
    is_open: Mapped[bool] = mapped_column(Boolean, nullable=False)
    prev_trade: Mapped[date | None] = mapped_column(Date)
    next_trade: Mapped[date | None] = mapped_column(Date)


# ============================================================
# Layer 1: 行情层
# ============================================================


class StockDaily(Base):
    """日线行情"""

    __tablename__ = "stock_daily"

    code: Mapped[str] = mapped_column(String(6), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    high: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    low: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    close: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    turnover: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    amplitude: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
    change_amt: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    adj_factor: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=1.0)

    __table_args__ = (
        Index("idx_daily_code", "code", trade_date.desc()),
        Index("idx_daily_date", "trade_date", "code"),
    )


class IndexDaily(Base):
    """指数日线"""

    __tablename__ = "index_daily"

    code: Mapped[str] = mapped_column(String(10), primary_key=True)
    trade_date: Mapped[date] = mapped_column(Date, primary_key=True)
    open: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    high: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    low: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    close: Mapped[Decimal | None] = mapped_column(Numeric(10, 3))
    volume: Mapped[int | None] = mapped_column(BigInteger)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(18, 2))
    change_pct: Mapped[Decimal | None] = mapped_column(Numeric(8, 4))
