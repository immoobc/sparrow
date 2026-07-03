"""模拟盘交易系统 — 用真实行情验证策略，不花真金白银

核心功能:
1. 按策略信号模拟买卖（记录每笔交易）
2. 用真实第二天开盘价成交（不假设收盘价买入）
3. 逐日跟踪持仓盈亏
4. 计算实时总资产/收益率
5. 和回测对比，看实盘执行偏差

使用方式:
    from src.strategy.paper_trading import PaperTrader
    trader = PaperTrader(capital=100000)
    trader.execute_signal(signal)  # 按信号调仓
    trader.update_prices()         # 用最新价格更新持仓
    trader.get_portfolio_summary() # 查看当前持仓状态
"""

import json
import time
from datetime import date, datetime
from pathlib import Path
from dataclasses import dataclass, field

import pandas as pd

from src.config import settings
from src.logger import logger

PAPER_DIR = Path(settings.data_dir) / "paper_trading"


@dataclass
class Trade:
    """一笔交易记录"""
    trade_date: str          # 成交日期
    code: str                # 股票代码
    direction: str           # "buy" / "sell"
    price: float             # 成交价
    shares: int              # 成交数量(股)
    amount: float            # 成交金额(元)
    commission: float        # 手续费
    reason: str = ""         # 交易原因


@dataclass
class Position:
    """一个持仓"""
    code: str
    shares: int              # 持有数量
    avg_cost: float          # 平均成本价
    current_price: float = 0 # 最新价
    pnl_pct: float = 0       # 盈亏比例
    market_value: float = 0  # 市值


class PaperTrader:
    """模拟盘交易员"""

    def __init__(self, capital: float = 100000):
        self.initial_capital = capital
        self.cash = capital
        self.positions: dict[str, Position] = {}  # {code: Position}
        self.trades: list[Trade] = []
        self.nav_history: list[dict] = []  # [{date, nav, cash, market_value}]
        self.commission_rate = 0.0015  # 单边千1.5(含印花税)
        self.start_date = date.today().isoformat()

        # 尝试加载已有状态
        self._load_state()

    @property
    def total_market_value(self) -> float:
        return sum(p.shares * p.current_price for p in self.positions.values())

    @property
    def total_asset(self) -> float:
        return self.cash + self.total_market_value

    @property
    def total_return_pct(self) -> float:
        return (self.total_asset / self.initial_capital - 1) * 100

    def buy(self, code: str, price: float, shares: int, reason: str = "") -> bool:
        """
        买入股票。

        Args:
            code: 股票代码
            price: 买入价格
            shares: 买入数量（必须是100的倍数）
            reason: 买入原因

        Returns:
            是否成功
        """
        if shares <= 0 or shares % 100 != 0:
            return False

        amount = price * shares
        commission = max(amount * self.commission_rate, 5)  # 最低5元
        total_cost = amount + commission

        if total_cost > self.cash:
            # 资金不足，减少买入量
            affordable = int(self.cash / (price * (1 + self.commission_rate)) / 100) * 100
            if affordable < 100:
                return False
            shares = affordable
            amount = price * shares
            commission = max(amount * self.commission_rate, 5)
            total_cost = amount + commission

        # 扣钱
        self.cash -= total_cost

        # 更新持仓
        if code in self.positions:
            pos = self.positions[code]
            total_shares = pos.shares + shares
            pos.avg_cost = (pos.avg_cost * pos.shares + price * shares) / total_shares
            pos.shares = total_shares
        else:
            self.positions[code] = Position(
                code=code, shares=shares, avg_cost=price, current_price=price
            )

        # 记录交易
        self.trades.append(Trade(
            trade_date=date.today().isoformat(),
            code=code, direction="buy",
            price=price, shares=shares,
            amount=amount, commission=commission,
            reason=reason,
        ))

        self._save_state()
        return True

    def sell(self, code: str, price: float, shares: int = 0, reason: str = "") -> bool:
        """
        卖出股票。shares=0表示全部卖出。

        Returns:
            是否成功
        """
        if code not in self.positions:
            return False

        pos = self.positions[code]
        if shares == 0:
            shares = pos.shares
        if shares > pos.shares:
            shares = pos.shares

        amount = price * shares
        commission = max(amount * self.commission_rate, 5)
        net_income = amount - commission

        # 收钱
        self.cash += net_income

        # 更新/删除持仓
        pos.shares -= shares
        if pos.shares <= 0:
            del self.positions[code]

        # 记录交易
        self.trades.append(Trade(
            trade_date=date.today().isoformat(),
            code=code, direction="sell",
            price=price, shares=shares,
            amount=amount, commission=commission,
            reason=reason,
        ))

        self._save_state()
        return True

    def update_prices(self, prices: dict[str, float] = None):
        """
        用最新价格更新所有持仓。

        Args:
            prices: {code: latest_price}，None则自动从数据源获取
        """
        if prices is None:
            prices = self._fetch_latest_prices()

        for code, pos in self.positions.items():
            if code in prices and prices[code] > 0:
                pos.current_price = prices[code]
                pos.pnl_pct = (pos.current_price / pos.avg_cost - 1) * 100
                pos.market_value = pos.shares * pos.current_price

        # 记录净值
        self.nav_history.append({
            "date": date.today().isoformat(),
            "nav": round(self.total_asset / self.initial_capital, 4),
            "total_asset": round(self.total_asset, 2),
            "cash": round(self.cash, 2),
            "market_value": round(self.total_market_value, 2),
            "positions_count": len(self.positions),
        })

        self._save_state()

    def execute_signal(self, signal: dict) -> dict:
        """
        按策略信号执行调仓。

        Args:
            signal: generate_live_signal()的输出

        Returns:
            执行结果 {bought, sold, skipped}
        """
        if "error" in signal or "target_portfolio" not in signal:
            return {"error": "信号无效"}

        target_codes = {p["code"]: p for p in signal["target_portfolio"]}
        current_codes = set(self.positions.keys())
        target_code_set = set(target_codes.keys())

        # 先卖出不在目标中的
        sold = []
        for code in list(current_codes - target_code_set):
            pos = self.positions[code]
            if pos.current_price > 0:
                self.sell(code, pos.current_price, reason="调仓卖出")
                sold.append(code)

        # 检查止损
        for code in list(self.positions.keys()):
            pos = self.positions[code]
            if pos.pnl_pct <= -20:  # 止损线
                if pos.current_price > 0:
                    self.sell(code, pos.current_price, reason=f"止损({pos.pnl_pct:.1f}%)")
                    sold.append(code)

        # 买入新的
        bought = []
        skipped = []
        per_stock_capital = self.cash / max(len(target_code_set - current_codes), 1) * 0.95

        for code, info in target_codes.items():
            if code in self.positions:
                continue  # 已持有
            price = info.get("close", 0)
            if price <= 0:
                skipped.append(code)
                continue
            shares = int(per_stock_capital / price / 100) * 100
            if shares < 100:
                skipped.append(code)
                continue
            if self.buy(code, price, shares, reason="策略买入"):
                bought.append(code)
            else:
                skipped.append(code)

        return {
            "bought": len(bought),
            "sold": len(sold),
            "skipped": len(skipped),
            "total_positions": len(self.positions),
        }

    def get_portfolio_summary(self) -> dict:
        """获取当前持仓汇总"""
        positions_list = []
        for code, pos in sorted(self.positions.items()):
            positions_list.append({
                "代码": pos.code,
                "持仓(股)": pos.shares,
                "成本价": round(pos.avg_cost, 2),
                "现价": round(pos.current_price, 2),
                "盈亏%": round(pos.pnl_pct, 1),
                "市值": round(pos.market_value, 0),
            })

        return {
            "initial_capital": self.initial_capital,
            "total_asset": round(self.total_asset, 2),
            "cash": round(self.cash, 2),
            "market_value": round(self.total_market_value, 2),
            "total_return_pct": round(self.total_return_pct, 2),
            "positions_count": len(self.positions),
            "positions": positions_list,
            "total_trades": len(self.trades),
            "start_date": self.start_date,
            "nav_history": self.nav_history[-30:],  # 最近30天
        }

    def reset(self):
        """清空所有数据，重新开始"""
        self.cash = self.initial_capital
        self.positions = {}
        self.trades = []
        self.nav_history = []
        self.start_date = date.today().isoformat()
        self._save_state()

    def _fetch_latest_prices(self) -> dict:
        """从通达信获取最新价格"""
        if not self.positions:
            return {}

        try:
            from src.storage.cache import load_daily
            codes = list(self.positions.keys())
            df = load_daily(codes=codes)
            if df.empty:
                return {}
            latest = df.sort_values("trade_date").groupby("code").last()
            return latest["close"].to_dict()
        except Exception:
            return {}

    def _save_state(self):
        """保存状态到文件"""
        PAPER_DIR.mkdir(parents=True, exist_ok=True)
        state = {
            "initial_capital": self.initial_capital,
            "cash": self.cash,
            "start_date": self.start_date,
            "positions": {
                code: {
                    "shares": pos.shares,
                    "avg_cost": pos.avg_cost,
                    "current_price": pos.current_price,
                }
                for code, pos in self.positions.items()
            },
            "trades": [
                {
                    "trade_date": t.trade_date,
                    "code": t.code,
                    "direction": t.direction,
                    "price": t.price,
                    "shares": t.shares,
                    "amount": t.amount,
                    "commission": t.commission,
                    "reason": t.reason,
                }
                for t in self.trades
            ],
            "nav_history": self.nav_history,
            "updated": datetime.now().isoformat(),
        }
        path = PAPER_DIR / "state.json"
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2))

    def _load_state(self):
        """从文件加载状态"""
        path = PAPER_DIR / "state.json"
        if not path.exists():
            return

        try:
            state = json.loads(path.read_text())
            self.initial_capital = state.get("initial_capital", self.initial_capital)
            self.cash = state.get("cash", self.initial_capital)
            self.start_date = state.get("start_date", date.today().isoformat())

            self.positions = {}
            for code, info in state.get("positions", {}).items():
                self.positions[code] = Position(
                    code=code,
                    shares=info["shares"],
                    avg_cost=info["avg_cost"],
                    current_price=info.get("current_price", info["avg_cost"]),
                )

            self.trades = []
            for t in state.get("trades", []):
                self.trades.append(Trade(**t))

            self.nav_history = state.get("nav_history", [])
        except Exception as e:
            logger.warning(f"加载模拟盘状态失败: {e}")
