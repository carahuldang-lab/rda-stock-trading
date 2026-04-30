"""Gap-Up Momentum Strategy — catch stocks gapping up on news/catalyst.

Logic:
    1. Today's open > prev close + 2% (gap up)
    2. Today's open < prev close + 8% (NOT extended — avoid pump-and-dumps)
    3. Volume on day-1 > 1.3x average (real interest)
    4. RSI 50-75 (bullish but room to run)
    5. Above 50-EMA (uptrend)
    6. Min price >= Rs.50 (avoid penny stocks)
    7. Min avg daily volume >= 1 lakh shares (liquidity)

Stop-loss: 1% below today's open (gap fill = invalidation)
Target: today's open + 2x gap size (typically 4-6% upside)

Why not chase >8% gappers?
    Stats show stocks gapping >8% reverse 60%+ of the time intraday.
    The sweet spot is 2-7% gaps with volume confirmation.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from agents.technical import Signal, SignalType


def generate_signal(df: pd.DataFrame, config: dict, symbol: str = "") -> Optional[Signal]:
    if df is None or len(df) < 50:
        return None

    last = df.iloc[-1]
    prev = df.iloc[-2]

    today_open = float(last["open"])
    today_close = float(last["close"])
    today_high = float(last["high"])
    today_volume = float(last["volume"])
    prev_close = float(prev["close"])

    # --- Filters ---
    # 1. Gap size — between 2% and 8%
    if prev_close <= 0:
        return None
    gap_pct = (today_open - prev_close) / prev_close * 100
    if gap_pct < 2.0 or gap_pct > 8.0:
        return None

    # 2. Min price filter (no penny stocks)
    if today_close < 50:
        return None

    # 3. Volume — today must be > 1.3x 20-day avg
    avg_vol = float(df["volume"].iloc[-21:-1].mean())
    if avg_vol <= 0:
        return None
    if avg_vol < 100_000:    # min 1 lakh shares avg/day for liquidity
        return None
    if today_volume < 1.3 * avg_vol:
        return None

    # 4. RSI healthy
    rsi = float(last.get("rsi", 50))
    if not (50 <= rsi <= 75):
        return None

    # 5. Above 50-EMA
    if "ema_trend" in df.columns:
        ema50 = float(last["ema_trend"])
        if today_close < ema50:
            return None

    # 6. Today's close > today's open (gap held)
    if today_close < today_open:
        return None

    # --- Signal ---
    entry = today_close
    stop_loss = today_open * 0.99       # 1% below today's open
    gap_size = today_open - prev_close
    target = today_open + 2 * gap_size  # 2x gap size

    # Confidence based on gap size + volume
    vol_ratio = today_volume / avg_vol
    if 3 <= gap_pct <= 6 and vol_ratio >= 2.0:
        conf = 0.78
        grade = "A"
    elif vol_ratio >= 1.7:
        conf = 0.65
        grade = "B"
    else:
        conf = 0.55
        grade = "C"

    return Signal(
        symbol=symbol,
        signal_type=SignalType.BUY,
        entry_price=entry,
        stop_loss=stop_loss,
        target=target,
        timestamp=datetime.now(),
        strategy_name=f"gap_up_{grade.lower()}",
        confidence=conf,
        reasoning=f"Gap-up {grade} {gap_pct:+.1f}%, vol {vol_ratio:.1f}x avg, "
                   f"RSI {rsi:.0f}, gap held",
    )
