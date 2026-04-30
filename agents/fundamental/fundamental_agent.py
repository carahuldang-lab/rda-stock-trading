"""Fundamental Agent — quality filter using cached yfinance fundamentals.

Reads from data/fundamentals.csv (built by agents.fundamental.screener).
Applies filters from config.yaml → fundamental section.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import pandas as pd

from .screener import load_fundamentals, is_stale


@dataclass
class FundamentalSnapshot:
    symbol: str
    market_cap_cr: float = 0.0
    pe_ratio: float = 0.0
    pb_ratio: float = 0.0
    roe: float = 0.0
    debt_to_equity: float = 0.0
    earnings_growth: float = 0.0
    revenue_growth: float = 0.0
    dividend_yield: float = 0.0
    sector: str = ""
    industry: str = ""
    flags: List[str] = field(default_factory=list)


class FundamentalAgent:
    """Filters universe to fundamentally sound stocks."""

    def __init__(self, config: dict):
        self.config = config
        self.cfg = config.get("fundamental", {})
        self.enabled = self.cfg.get("enabled", False)
        self._cache: Optional[pd.DataFrame] = None

    def _data(self) -> pd.DataFrame:
        if self._cache is None:
            self._cache = load_fundamentals()
        return self._cache

    def get_tradeable_universe(self, full_universe: List[str]) -> List[str]:
        if not self.enabled:
            return full_universe

        df = self._data()
        if df.empty:
            return full_universe        # no data → don't block

        passed = []
        for symbol in full_universe:
            snap = self.get_snapshot(symbol)
            if snap is None or self._passes_filters(snap):
                passed.append(symbol)
        return passed

    def get_snapshot(self, symbol: str) -> Optional[FundamentalSnapshot]:
        df = self._data()
        if df.empty:
            return None
        row = df[df["symbol"] == symbol]
        if row.empty:
            return None
        r = row.iloc[0]
        flags = []
        if r.get("debt_to_equity", 0) > 2.0:
            flags.append("HIGH_DEBT")
        if r.get("pe_ratio", 0) > 80:
            flags.append("EXPENSIVE")
        if r.get("roe", 0) < 8:
            flags.append("LOW_ROE")
        if r.get("earnings_growth", 0) < -10:
            flags.append("DECLINING_EARNINGS")
        return FundamentalSnapshot(
            symbol=symbol,
            market_cap_cr=float(r.get("market_cap_cr", 0) or 0),
            pe_ratio=float(r.get("pe_ratio", 0) or 0),
            pb_ratio=float(r.get("pb_ratio", 0) or 0),
            roe=float(r.get("roe", 0) or 0),
            debt_to_equity=float(r.get("debt_to_equity", 0) or 0),
            earnings_growth=float(r.get("earnings_growth", 0) or 0),
            revenue_growth=float(r.get("revenue_growth", 0) or 0),
            dividend_yield=float(r.get("dividend_yield", 0) or 0),
            sector=str(r.get("sector", "") or ""),
            industry=str(r.get("industry", "") or ""),
            flags=flags,
        )

    def _passes_filters(self, snap: FundamentalSnapshot) -> bool:
        c = self.cfg
        if snap.market_cap_cr and snap.market_cap_cr < c.get("min_market_cap_cr", 0):
            return False
        if snap.pe_ratio and snap.pe_ratio > c.get("max_pe", 999):
            return False
        if snap.pe_ratio and snap.pe_ratio <= 0:    # negative or zero P/E = loss-making
            if c.get("exclude_loss_making", False):
                return False
        if snap.roe and snap.roe < c.get("min_roe", 0):
            return False
        if snap.debt_to_equity and snap.debt_to_equity > c.get("max_debt_to_equity", 999):
            return False
        return True

    def is_data_stale(self) -> bool:
        return is_stale()
