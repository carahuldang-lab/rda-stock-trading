"""Momentum Breakout Strategy — Phase 1 starter strategy.

Logic:
    - Long entry when:
        * price closes above 20-period high
        * RSI > 55 (momentum confirmation)
        * Volume > 1.5x 20-period average volume
        * EMA(9) > EMA(21) > EMA(50) (uptrend confirmation)
    - Stop-loss: 2x ATR below entry
    - Target: 1:2 risk-reward

This is a starter — backtest before paper trading, paper trade before live.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from agents.technical import Signal, SignalType


def generate_signal(df: pd.DataFrame, config: dict, symbol: str = "") -> Optional[Signal]:
    """Generate a momentum breakout signal.

    Expects df with indicators already added (rsi, ema_fast, ema_slow, ema_trend, atr).
    """
    if len(df) < 50:
        return None

    last = df.iloc[-1]
    prev_high = df["high"].iloc[-21:-1].max()       # 20-period high (excl. current)

    # Volume confirmation
    avg_vol = df["volume"].iloc[-21:-1].mean()
    vol_ok = last["volume"] > 1.5 * avg_vol

    # Trend confirmation
    trend_ok = last["ema_fast"] > last["ema_slow"] > last["ema_trend"]

    # Momentum confirmation
    momentum_ok = last["rsi"] > 55

    # Breakout
    breakout = last["close"] > prev_high

    if not (breakout and vol_ok and trend_ok and momentum_ok):
        return None

    entry = float(last["close"])
    atr = float(last["atr"]) if pd.notna(last["atr"]) else entry * 0.02
    stop_loss = entry - 2 * atr
    target = entry + 4 * atr                        # 1:2 RR

    return Signal(
        symbol=symbol,
        signal_type=SignalType.BUY,
        entry_price=entry,
        stop_loss=stop_loss,
        target=target,
        timestamp=datetime.now(),
        strategy_name="momentum_breakout",
        confidence=0.7,
        reasoning=(
            f"Breakout above 20-period high ({prev_high:.2f}); "
            f"vol {last['volume']/avg_vol:.1f}x avg; RSI {last['rsi']:.1f}; "
            f"EMAs aligned bullish"
        ),
    )
