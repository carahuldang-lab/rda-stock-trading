"""Fundamental Agent — Screener, financial health, quality filters.

Responsibilities:
    1. Apply fundamental screening filters (P/E, ROE, debt, growth).
    2. Parse latest quarterly results (when fundamental.enabled = true).
    3. Maintain a "tradeable universe" — stocks that pass quality bar.
    4. Flag financial red flags (auditor changes, debt spikes, promoter pledge).

Phase 1: Disabled. Universe = full Nifty 500.
Phase 2: Enable with min_market_cap, max_pe, min_roe filters.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List


@dataclass
class FundamentalSnapshot:
    """Snapshot of a stock's fundamental metrics."""
    symbol: str
    market_cap_cr: float = 0.0
    pe_ratio: float = 0.0
    roe: float = 0.0                    # Return on Equity %
    debt_to_equity: float = 0.0
    sales_growth_yoy: float = 0.0
    profit_growth_yoy: float = 0.0
    promoter_holding_pct: float = 0.0
    promoter_pledge_pct: float = 0.0
    sector: str = ""
    flags: List[str] = field(default_factory=list)


class FundamentalAgent:
    """Filters universe to fundamentally sound stocks."""

    def __init__(self, config: dict):
        self.config = config
        self.fundamental_cfg = config.get("fundamental", {})
        self.enabled = self.fundamental_cfg.get("enabled", False)

    def get_tradeable_universe(self, full_universe: List[str]) -> List[str]:
        """Filter full universe to fundamentally sound stocks.

        If fundamental.enabled = false (Phase 1), passes through unchanged.
        """
        if not self.enabled:
            return full_universe

        # TODO: Phase 2 — apply filters
        passed = []
        for symbol in full_universe:
            snap = self.get_snapshot(symbol)
            if self._passes_filters(snap):
                passed.append(symbol)
        return passed

    def get_snapshot(self, symbol: str) -> FundamentalSnapshot:
        """Fetch fundamental snapshot for a symbol.

        Sources to consider:
            - Screener.in API (free, has rate limits)
            - Tijori Finance
            - Trendlyne
            - Manual scraping of NSE corporate filings
        """
        # TODO: Phase 2
        return FundamentalSnapshot(symbol=symbol)

    def _passes_filters(self, snap: FundamentalSnapshot) -> bool:
        """Apply all configured filters."""
        cfg = self.fundamental_cfg
        if snap.market_cap_cr < cfg.get("min_market_cap_cr", 0):
            return False
        if snap.pe_ratio > cfg.get("max_pe", 999):
            return False
        if snap.roe < cfg.get("min_roe", 0):
            return False
        if snap.debt_to_equity > cfg.get("max_debt_to_equity", 999):
            return False
        if cfg.get("exclude_loss_making", False) and snap.profit_growth_yoy < 0:
            return False
        return True
