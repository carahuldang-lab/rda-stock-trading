"""Research Agent — News, earnings, corporate actions, sector signals.

Responsibilities:
    1. Fetch market news (NSE/BSE announcements, sector news).
    2. Track earnings calendar — flag stocks reporting this week.
    3. Detect corporate actions (splits, bonuses, dividends, mergers).
    4. Identify sector rotation signals (which sectors leading/lagging).
    5. Surface "do not trade today" flags (e.g., F&O ban list, results day).

Phase 1: Skeleton + earnings calendar from NSE.
Phase 2: News scraping (Moneycontrol, ET Markets) + sentiment.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import List, Optional

from .news_scraper import (
    load_news, get_negative_news_symbols, fetch_news as fetch_news_for,
)


@dataclass
class NewsItem:
    """A single piece of market-relevant news."""
    symbol: str
    headline: str
    source: str
    published_at: str
    sentiment: str = "neutral"          # positive | negative | neutral


@dataclass
class CorporateAction:
    """A corporate action affecting a stock."""
    symbol: str
    action_type: str                    # split | bonus | dividend | merger
    ex_date: date
    details: str


class ResearchAgent:
    """Surfaces external signals that affect trading decisions."""

    def __init__(self, config: dict):
        self.config = config
        self._negative_symbols: Optional[set] = None

    def get_earnings_this_week(self) -> List[str]:
        """Return symbols reporting earnings in the next 5 trading days.

        Stocks with imminent earnings should typically be avoided
        for short-term technical trades due to gap risk.
        """
        # TODO: Phase 2 — scrape NSE earnings calendar
        return []

    def get_fno_ban_list(self) -> List[str]:
        """Return symbols currently in F&O ban (skip these).

        NSE publishes daily ban list at:
        https://www.nseindia.com/api/reports?archives=...&type=fno-ban
        """
        # TODO: Phase 2 — fetch live ban list
        return []

    def get_corporate_actions(self, days_ahead: int = 7) -> List[CorporateAction]:
        """Return upcoming corporate actions in next N days."""
        # TODO: Phase 2
        return []

    def get_sector_strength(self) -> dict:
        """Return sector ranking (1 = strongest, 11 = weakest).

        Used by technical agent to bias trades toward strong sectors.
        """
        # TODO: Phase 2 — sector indices relative strength
        return {}

    def should_avoid_today(self, symbol: str) -> tuple[bool, str]:
        """Top-level check: should we avoid trading this symbol today?"""
        if symbol in self.get_fno_ban_list():
            return True, "Stock in F&O ban list"
        if symbol in self.get_earnings_this_week():
            return True, "Earnings in next 5 days - gap risk"
        if self._negative_symbols is None:
            self._negative_symbols = get_negative_news_symbols()
        if symbol in self._negative_symbols:
            return True, "Negative news headline detected"
        return False, ""

    def get_news_for(self, symbol: str, limit: int = 3) -> list[dict]:
        """Return recent headlines (cached if possible) for dashboard display."""
        df = load_news()
        if df.empty:
            return []
        return df[df["symbol"] == symbol].head(limit).to_dict("records")
