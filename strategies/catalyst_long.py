"""Catalyst Long Strategy — event-driven entries independent of market structure.

Fires when a stock has a stock-specific catalyst that often runs regardless of
broader market regime:
    1. Recent positive news (last 48h)
    2. Volume surge (>2.5x avg)
    3. Gap-up open (>2% above prev close)
    4. Analyst upgrade with >15% upside

Multiple catalysts → higher confidence + position size.

This is what hedge funds call "event-driven" — different from technical breakouts.
News-driven moves can run 10-30% in days regardless of Nifty.

Stop-loss: tighter (1.5x ATR) since events can reverse fast.
Target: 2.5x ATR (1:1.7 RR — accept lower RR for high base rate).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from agents.technical import Signal, SignalType
from agents.research.catalyst import detect_catalyst


def generate_signal(df: pd.DataFrame, config: dict, symbol: str = "") -> Optional[Signal]:
    """Fire when at least 1 catalyst + basic technical sanity check."""
    if df is None or len(df) < 25:
        return None

    cat = detect_catalyst(symbol, df)
    if not cat.has_catalyst:
        return None

    last = df.iloc[-1]
    close = float(last["close"])
    rsi = float(last.get("rsi", 50))

    # Basic sanity: don't BUY if RSI > 80 (already exhausted)
    # or RSI < 30 (let mean reversion handle that)
    if rsi > 80 or rsi < 30:
        return None

    # Don't BUY if price is in a clear downtrend (close < 50-EMA AND falling)
    if "ema_trend" in df.columns:
        ema50 = float(last["ema_trend"])
        if close < ema50:
            ema50_5d_ago = float(df["ema_trend"].iloc[-6]) if len(df) >= 6 else ema50
            if ema50 < ema50_5d_ago:    # 50-EMA falling
                return None

    atr = float(last.get("atr", close * 0.02))
    entry = close
    stop_loss = entry - 1.5 * atr
    target = entry + 2.5 * atr

    # Confidence based on catalyst count
    if cat.score >= 3:
        conf = 0.80
        grade = "A"
    elif cat.score == 2:
        conf = 0.70
        grade = "B"
    else:
        conf = 0.60
        grade = "C"

    return Signal(
        symbol=symbol,
        signal_type=SignalType.BUY,
        entry_price=entry,
        stop_loss=stop_loss,
        target=target,
        timestamp=datetime.now(),
        strategy_name=f"catalyst_{grade.lower()}",
        confidence=conf,
        reasoning=f"Catalyst {grade} ({cat.score} signals): {cat.reasoning}",
    )
