"""Opening Range Breakout (ORB) Scalp — short-term intraday strategy.

Logic — runs on 5m bars during 09:30-14:00 IST:
    - Compute opening range = high/low of FIRST 15 minutes (3 x 5m bars)
    - LONG entry: 5m close breaks above OR_high with volume > 1.5x avg
    - SHORT entry: 5m close breaks below OR_low (skip if config blocks shorts)
    - Stop-loss: opposite side of OR
    - Target: 1.5x range size
    - Time exit: 14:30 IST (avoid late-day reversal)

Only fires once per stock per day.
"""
from __future__ import annotations

from datetime import datetime, time as dt_time
from typing import Optional

import pandas as pd

from agents.technical import Signal, SignalType


def generate_signal(df: pd.DataFrame, config: dict, symbol: str = "") -> Optional[Signal]:
    """Detect ORB breakout on 5-minute bars.

    Expects df indexed by datetime, with columns open/high/low/close/volume.
    """
    if df is None or len(df) < 6:
        return None

    # Filter to today's bars only
    if not isinstance(df.index, pd.DatetimeIndex):
        return None
    today = df.index[-1].date()
    today_df = df[df.index.date == today]
    if len(today_df) < 4:
        return None

    # Opening range = first 3 bars (15 min)
    or_bars = today_df.iloc[:3]
    or_high = float(or_bars["high"].max())
    or_low = float(or_bars["low"].min())
    or_range = or_high - or_low
    if or_range <= 0:
        return None

    # Current bar
    last = today_df.iloc[-1]
    last_close = float(last["close"])
    last_vol = float(last["volume"])
    avg_vol = float(today_df["volume"].iloc[:3].mean())

    # Time-of-day filter (no entries after 14:00 IST)
    last_time = df.index[-1].time()
    if last_time > dt_time(14, 0):
        return None
    if last_time < dt_time(9, 30):       # before opening range completes
        return None

    # Long breakout
    if last_close > or_high and last_vol > 1.5 * avg_vol:
        entry = last_close
        stop_loss = or_low
        target = entry + 1.5 * or_range
        return Signal(
            symbol=symbol, signal_type=SignalType.BUY,
            entry_price=entry, stop_loss=stop_loss, target=target,
            timestamp=datetime.now(), strategy_name="scalp_orb",
            confidence=0.65,
            reasoning=f"ORB long: break above {or_high:.2f}, range {or_range:.2f}, "
                       f"vol {last_vol/avg_vol:.1f}x",
        )

    # Short breakout (only if config allows)
    if config.get("execution", {}).get("allow_short", False):
        if last_close < or_low and last_vol > 1.5 * avg_vol:
            entry = last_close
            stop_loss = or_high
            target = entry - 1.5 * or_range
            return Signal(
                symbol=symbol, signal_type=SignalType.SELL,
                entry_price=entry, stop_loss=stop_loss, target=target,
                timestamp=datetime.now(), strategy_name="scalp_orb",
                confidence=0.65,
                reasoning=f"ORB short: break below {or_low:.2f}",
            )
    return None
