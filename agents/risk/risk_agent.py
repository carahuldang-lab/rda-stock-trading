"""Risk Agent — Position sizing, exposure limits, kill switches.

Responsibilities:
    1. Position sizing — how many shares to buy given risk budget.
    2. Enforce max open positions, max sector exposure.
    3. Daily loss kill switch — halt trading if daily P&L < -5%.
    4. Pre-trade validation — every order must pass risk checks.

This agent has VETO power over all trades.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple


@dataclass
class RiskCheck:
    """Result of a pre-trade risk validation."""
    approved: bool
    quantity: int                       # Position size (0 if rejected)
    reason: str = ""


class RiskAgent:
    """Gatekeeper between signals and execution."""

    def __init__(self, config: dict):
        self.config = config
        self.risk_cfg = config["risk"]
        self.account_cfg = config["account"]
        self.daily_loss_halt = False    # Set true if kill switch triggered

    # ------------------------------------------------------------------
    # Position sizing
    # ------------------------------------------------------------------
    def calculate_position_size(
        self,
        entry_price: float,
        stop_loss: float,
    ) -> int:
        """Calculate shares to buy based on risk-per-trade rule.

        Formula:
            risk_amount = capital * risk_per_trade_pct / 100
            risk_per_share = entry_price - stop_loss
            shares = floor(risk_amount / risk_per_share)
        """
        capital = self.account_cfg["capital"]
        risk_pct = self.risk_cfg["risk_per_trade_pct"]
        risk_amount = capital * risk_pct / 100         # ₹2,000 for ₹1L @ 2%

        risk_per_share = entry_price - stop_loss
        if risk_per_share <= 0:
            return 0

        shares = int(risk_amount / risk_per_share)

        # Cap by available capital (don't use more than 30% per trade)
        max_capital_per_trade = capital * 0.30
        max_shares_by_capital = int(max_capital_per_trade / entry_price)
        return min(shares, max_shares_by_capital)

    # ------------------------------------------------------------------
    # Pre-trade validation
    # ------------------------------------------------------------------
    def validate_trade(
        self,
        symbol: str,
        entry_price: float,
        stop_loss: float,
        sector: str,
        open_positions: list,           # list of currently held positions
        daily_pnl: float = 0.0,
    ) -> RiskCheck:
        """Run all pre-trade checks. Returns RiskCheck with approval + quantity."""

        # 1. Daily loss kill switch
        capital = self.account_cfg["capital"]
        daily_loss_pct = abs(daily_pnl) / capital * 100
        if daily_pnl < 0 and daily_loss_pct >= self.risk_cfg["max_daily_loss_pct"]:
            self.daily_loss_halt = True
            return RiskCheck(False, 0, f"Daily loss limit hit: {daily_loss_pct:.2f}%")

        if self.daily_loss_halt:
            return RiskCheck(False, 0, "Daily loss halt active — no new trades today")

        # 2. Max open positions
        if len(open_positions) >= self.risk_cfg["max_open_positions"]:
            return RiskCheck(False, 0,
                             f"Max positions reached ({self.risk_cfg['max_open_positions']})")

        # 3. Already holding this symbol
        if any(p["symbol"] == symbol for p in open_positions):
            return RiskCheck(False, 0, f"Already holding {symbol}")

        # 4. Sector concentration
        sector_exposure = self._calc_sector_exposure(sector, open_positions, capital)
        if sector_exposure >= self.risk_cfg["max_sector_exposure_pct"]:
            return RiskCheck(False, 0,
                             f"Sector cap reached for {sector}: {sector_exposure:.1f}%")

        # 5. Position sizing
        qty = self.calculate_position_size(entry_price, stop_loss)
        if qty <= 0:
            return RiskCheck(False, 0, "Invalid stop-loss → zero position size")

        return RiskCheck(True, qty, "Approved")

    def _calc_sector_exposure(
        self,
        sector: str,
        open_positions: list,
        capital: float,
    ) -> float:
        """Return current sector exposure as % of capital."""
        sector_capital = sum(
            p["quantity"] * p["entry_price"]
            for p in open_positions
            if p.get("sector") == sector
        )
        return (sector_capital / capital) * 100 if capital > 0 else 0.0

    def reset_daily_halt(self) -> None:
        """Called at start of each trading day to clear the kill switch."""
        self.daily_loss_halt = False
