"""Multi-Timeframe support — fetch + analyze data at any granularity.

Timeframes supported:
    1m, 5m, 15m, 1h, 1d, 1wk, 1mo
yfinance limits:
    1m  → max 7 days
    5m, 15m → max 60 days
    1h → max 730 days
    1d, 1wk, 1mo → unlimited

Each timeframe maps to a typical hold-period recommendation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd
import yfinance as yf


@dataclass
class TimeframeConfig:
    label: str
    yf_interval: str
    yf_max_period: str
    typical_hold: str            # human-readable
    expected_hold_days: float
    use_case: str


TIMEFRAMES = {
    "1m":  TimeframeConfig("1-min",  "1m",  "5d",  "minutes — scalp",            0.04, "Scalping"),
    "5m":  TimeframeConfig("5-min",  "5m",  "60d", "30 min – 2 hours",            0.1,  "Scalping / intraday"),
    "15m": TimeframeConfig("15-min", "15m", "60d", "2-6 hours",                   0.3,  "Intraday momentum"),
    "1h":  TimeframeConfig("Hourly", "1h",  "730d","1-3 days",                    2.0,  "Short-term swing"),
    "1d":  TimeframeConfig("Daily",  "1d",  "10y", "5-15 days",                   10.0, "Swing / positional"),
    "1wk": TimeframeConfig("Weekly", "1wk", "10y", "1-3 months",                  60.0, "Positional"),
    "1mo": TimeframeConfig("Monthly","1mo", "10y", "6-24 months",                 300.0,"Long-term investing"),
}


def fetch_bars(symbol: str, timeframe: str, period: Optional[str] = None) -> pd.DataFrame:
    """Fetch OHLCV bars for a symbol at any timeframe."""
    if timeframe not in TIMEFRAMES:
        raise ValueError(f"Unknown timeframe: {timeframe}")
    cfg = TIMEFRAMES[timeframe]
    use_period = period or cfg.yf_max_period
    df = yf.download(
        f"{symbol}.NS", period=use_period, interval=cfg.yf_interval,
        progress=False, auto_adjust=True,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    if hasattr(df.columns, "get_level_values"):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]]


def recommend_hold_period(timeframe: str, signal_confidence: float = 0.7) -> str:
    """Return human-readable hold recommendation."""
    cfg = TIMEFRAMES.get(timeframe)
    if cfg is None:
        return "Unknown"
    base = cfg.typical_hold
    if signal_confidence >= 0.85:
        return f"{base} (high conviction — let it run)"
    if signal_confidence < 0.5:
        return f"{base} (low conviction — tighten stop)"
    return base
