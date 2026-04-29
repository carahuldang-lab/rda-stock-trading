"""Portfolio Agent — P&L, MTM, tax bookkeeping.

Responsibilities:
    1. Track all open + closed positions.
    2. Compute realized + unrealized P&L (daily + lifetime).
    3. Mark-to-market every minute during market hours.
    4. Capital gains classification (STCG <1yr, LTCG >1yr) — useful for RDA tax filing.
    5. Generate daily/weekly/monthly reports.
    6. Calculate net brokerage, STT, GST costs.

This is your audit trail. Every trade flows through here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional


@dataclass
class Position:
    """An open position."""
    symbol: str
    quantity: int
    entry_price: float
    entry_time: datetime
    stop_loss: float
    target: float
    sector: str = ""
    strategy: str = ""
    current_price: float = 0.0          # Updated by MTM


@dataclass
class ClosedTrade:
    """A completed round-trip trade."""
    symbol: str
    quantity: int
    entry_price: float
    exit_price: float
    entry_time: datetime
    exit_time: datetime
    pnl_gross: float                    # Before charges
    pnl_net: float                      # After brokerage + STT + GST
    holding_days: int
    gain_type: str                      # STCG | LTCG | INTRADAY
    sector: str = ""
    strategy: str = ""
    exit_reason: str = ""               # target | stoploss | manual | square_off


@dataclass
class DailySnapshot:
    """End-of-day portfolio snapshot — written to daily report."""
    snapshot_date: date
    capital: float
    cash: float
    invested: float
    unrealized_pnl: float
    realized_pnl_today: float
    open_positions: int
    trades_today: int
    win_rate: float


class PortfolioAgent:
    """Maintains state of all positions and produces reports."""

    def __init__(self, config: dict):
        self.config = config
        self.account_cfg = config["account"]
        self.cost_cfg = config["portfolio"]
        self.open_positions: Dict[str, Position] = {}
        self.closed_trades: List[ClosedTrade] = []

    # ------------------------------------------------------------------
    # Position lifecycle
    # ------------------------------------------------------------------
    def open_position(self, position: Position) -> None:
        """Record a new open position."""
        self.open_positions[position.symbol] = position

    def close_position(
        self,
        symbol: str,
        exit_price: float,
        exit_reason: str = "manual",
    ) -> Optional[ClosedTrade]:
        """Close an existing position and book P&L."""
        pos = self.open_positions.pop(symbol, None)
        if pos is None:
            return None

        gross = (exit_price - pos.entry_price) * pos.quantity
        charges = self._calc_charges(pos.entry_price, exit_price, pos.quantity)
        net = gross - charges
        holding_days = (datetime.now() - pos.entry_time).days

        gain_type = "INTRADAY" if holding_days == 0 else (
            "STCG" if holding_days < 365 else "LTCG"
        )

        trade = ClosedTrade(
            symbol=pos.symbol,
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            entry_time=pos.entry_time,
            exit_time=datetime.now(),
            pnl_gross=gross,
            pnl_net=net,
            holding_days=holding_days,
            gain_type=gain_type,
            sector=pos.sector,
            strategy=pos.strategy,
            exit_reason=exit_reason,
        )
        self.closed_trades.append(trade)
        return trade

    # ------------------------------------------------------------------
    # P&L computation
    # ------------------------------------------------------------------
    def update_mtm(self, prices: Dict[str, float]) -> None:
        """Update current_price on all open positions."""
        for symbol, pos in self.open_positions.items():
            if symbol in prices:
                pos.current_price = prices[symbol]

    def unrealized_pnl(self) -> float:
        return sum(
            (p.current_price - p.entry_price) * p.quantity
            for p in self.open_positions.values()
            if p.current_price > 0
        )

    def realized_pnl(self, day: Optional[date] = None) -> float:
        if day is None:
            return sum(t.pnl_net for t in self.closed_trades)
        return sum(t.pnl_net for t in self.closed_trades if t.exit_time.date() == day)

    def win_rate(self) -> float:
        if not self.closed_trades:
            return 0.0
        wins = sum(1 for t in self.closed_trades if t.pnl_net > 0)
        return wins / len(self.closed_trades) * 100

    # ------------------------------------------------------------------
    # Charges (Dhan flat brokerage + statutory)
    # ------------------------------------------------------------------
    def _calc_charges(self, buy: float, sell: float, qty: int) -> float:
        """Compute total charges (brokerage + STT + GST)."""
        cfg = self.cost_cfg
        brokerage = cfg["brokerage_per_trade"] * 2          # entry + exit
        stt = (sell * qty) * cfg["stt_pct"] / 100           # only on sell side
        gst = brokerage * cfg["gst_pct"] / 100
        return brokerage + stt + gst

    # ------------------------------------------------------------------
    # Reports
    # ------------------------------------------------------------------
    def daily_snapshot(self) -> DailySnapshot:
        capital = self.account_cfg["capital"]
        invested = sum(p.entry_price * p.quantity for p in self.open_positions.values())
        return DailySnapshot(
            snapshot_date=date.today(),
            capital=capital,
            cash=capital - invested,
            invested=invested,
            unrealized_pnl=self.unrealized_pnl(),
            realized_pnl_today=self.realized_pnl(date.today()),
            open_positions=len(self.open_positions),
            trades_today=sum(
                1 for t in self.closed_trades if t.exit_time.date() == date.today()
            ),
            win_rate=self.win_rate(),
        )
