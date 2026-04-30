"""Mean Reversion Strategy — buy oversold bounces, sell overbought reversals.

Logic — LONG entry:
    - RSI < 30 (oversold)
    - Close below lower Bollinger Band (2 std dev)
    - Bullish reversal candle (close > prev close)
    - Above 200-EMA (so we don't bottom-fish in a downtrend)
    - Volume > 1.2x average (genuine buying interest)

Stop-loss: 1.5 x ATR below entry
Target: middle Bollinger Band (1:1.5 RR typically)

Best for: range-bound markets, sector rotation, post-panic bounces.
Worst for: strong trends (gets crushed in down-trends → 200-EMA filter saves us).
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from agents.technical import Signal, SignalType


def _bollinger(close: pd.Series, length: int = 20, std: float = 2.0) -> tuple[pd.Series, pd.Series, pd.Series]:
    sma = close.rolling(length).mean()
    sd = close.rolling(length).std()
    upper = sma + std * sd
    lower = sma - std * sd
    return upper, sma, lower


def generate_signal(df: pd.DataFrame, config: dict, symbol: str = "") -> Optional[Signal]:
    if df is None or len(df) < 200:
        return None

    df = df.copy()
    upper, mid, lower = _bollinger(df["close"], 20, 2.0)
    df["bb_upper"] = upper
    df["bb_mid"] = mid
    df["bb_lower"] = lower
    if "ema_trend" not in df.columns:
        df["ema_trend"] = df["close"].ewm(span=200, adjust=False).mean()

    last = df.iloc[-1]
    prev = df.iloc[-2]

    rsi = float(last.get("rsi", 50.0))
    close = float(last["close"])
    prev_close = float(prev["close"])
    bb_lower = float(last["bb_lower"])
    bb_mid = float(last["bb_mid"])
    ema200 = float(last["ema_trend"])
    atr = float(last.get("atr", close * 0.02))
    avg_vol = float(df["volume"].iloc[-21:-1].mean())
    vol_ok = float(last["volume"]) > 1.2 * avg_vol if avg_vol > 0 else False

    # Conditions
    oversold = rsi < 30
    below_bb = close < bb_lower
    bullish_reversal = close > prev_close
    above_long_trend = close > ema200

    if not (oversold and below_bb and bullish_reversal and above_long_trend and vol_ok):
        return None

    entry = close
    stop_loss = entry - 1.5 * atr
    target = bb_mid                       # mean-reversion target

    return Signal(
        symbol=symbol,
        signal_type=SignalType.BUY,
        entry_price=entry,
        stop_loss=stop_loss,
        target=target,
        timestamp=datetime.now(),
        strategy_name="mean_reversion",
        confidence=0.6,
        reasoning=(
            f"Oversold bounce: RSI {rsi:.1f}, close below BB lower ({bb_lower:.2f}), "
            f"bullish reversal candle, above 200-EMA, volume {float(last['volume'])/avg_vol:.1f}x avg"
        ),
    )
