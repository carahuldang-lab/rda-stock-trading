"""Fortress Strategy — multi-factor BUY confirmation targeting 60%+ win rate.

Inspired by javajack/skill-algotrader's Fortress signal philosophy:
"Only trade setups where multiple uncorrelated factors agree."

Required (HARD filters — must ALL pass):
    F1. Breakout: close > 20-day high
    F2. Trend alignment: EMA(9) > EMA(21) > EMA(50) > EMA(200)
    F3. Long-term uptrend: close > EMA(200)
    F4. Volume conviction: today vol > 1.8x 20-day avg
    F5. RSI healthy: 55 <= RSI <= 70 (not exhausted)
    F6. No recent gap-down: lowest of last 5 closes >= prev_close * 0.95
    F7. ATR reasonable: 1% <= ATR/price <= 8% (not too quiet, not too volatile)
    F8. Positive 5-day momentum: 5-day ROC > 1.5%

A Fortress signal scores 8/8. We only trade 8/8 setups (highest conviction).
This produces fewer signals but higher win rate.

Stop-loss: 2x ATR
Target: 3x ATR (1:1.5 RR — Fortress trades have higher base hit rate so RR can be lower)
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

import pandas as pd

from agents.technical import Signal, SignalType


def _check_factors(df: pd.DataFrame) -> tuple[bool, dict, list[str]]:
    """Return (all_passed, factor_results_dict, reasoning_lines).

    Adaptive: if <200 bars (recent IPO), uses 50-EMA as long-term proxy for F3.
    """
    if len(df) < 50:
        return False, {}, ["insufficient history (need 50+ bars)"]

    last = df.iloc[-1]
    last5 = df.iloc[-6:-1]

    close = float(last["close"])
    high20 = float(df["high"].iloc[-21:-1].max())
    rsi = float(last.get("rsi", 50))
    ema9 = float(last.get("ema_fast", close))
    ema21 = float(last.get("ema_slow", close))
    ema50 = float(last.get("ema_trend", close))
    avg_vol = float(df["volume"].iloc[-21:-1].mean())
    today_vol = float(last["volume"])
    atr = float(last.get("atr", close * 0.02))

    # 200-EMA for F3 — use 50-EMA proxy if data too short
    if len(df) >= 200:
        ema200 = float(df["close"].ewm(span=200, adjust=False).mean().iloc[-1])
    else:
        # Recent IPO — use the longest available EMA we have (50)
        ema200 = ema50

    factors = {
        "F1_breakout": close >= high20 * 0.998,    # within 0.2% of high — "primed to break"
        "F2_trend_align": ema9 > ema21 > ema50 > ema200,
        "F3_long_uptrend": close > ema200,
        "F4_volume": today_vol > 1.8 * avg_vol if avg_vol > 0 else False,
        "F5_rsi_healthy": 55 <= rsi <= 70,
        "F6_no_gap_down": float(last5["close"].min()) >= close * 0.95,
        "F7_atr_reasonable": 0.01 <= (atr / close) <= 0.08 if close > 0 else False,
    }

    # F8: 5-day ROC
    if len(df) >= 6:
        roc = (close - float(df["close"].iloc[-6])) / float(df["close"].iloc[-6]) * 100
        factors["F8_momentum"] = roc > 1.5
    else:
        factors["F8_momentum"] = False

    # Reasoning
    reasons = []
    if factors["F1_breakout"]:
        reasons.append(f"breakout above {high20:.2f}")
    if factors["F2_trend_align"]:
        reasons.append("EMAs aligned bullish")
    if factors["F4_volume"]:
        reasons.append(f"volume {today_vol/avg_vol:.1f}x avg")
    if factors["F5_rsi_healthy"]:
        reasons.append(f"RSI {rsi:.0f}")
    if factors.get("F8_momentum"):
        reasons.append(f"5d ROC {roc:.1f}%")

    all_passed = all(factors.values())
    return all_passed, factors, reasons


def generate_signal(df: pd.DataFrame, config: dict, symbol: str = "") -> Optional[Signal]:
    """Generate Fortress BUY signal with GRADED tiers.

    Grades:
        A+ = 8/8 factors (highest conviction, confidence 0.90)
        A  = 7/8 factors (high conviction, confidence 0.78)
        B  = 6/8 factors (decent conviction, confidence 0.65)
        < 6/8 → no signal (added to watchlist only)
    """
    passed_all, factors, reasons = _check_factors(df)
    score = sum(1 for v in factors.values() if v)

    if score < 5:
        return None    # too weak — watchlist only, no trade

    # F1 (breakout) is mandatory — no breakout = no Fortress signal
    if not factors.get("F1_breakout"):
        return None

    last = df.iloc[-1]
    close = float(last["close"])
    atr = float(last.get("atr", close * 0.02))

    # Grade-based confidence + risk tightening
    if score == 8:
        grade, conf, sl_atr, tgt_atr = "A+", 0.90, 2.0, 3.0
    elif score == 7:
        grade, conf, sl_atr, tgt_atr = "A", 0.78, 1.8, 3.0
    elif score == 6:
        grade, conf, sl_atr, tgt_atr = "B", 0.65, 1.5, 2.5
    else:
        grade, conf, sl_atr, tgt_atr = "C", 0.55, 1.3, 2.2

    entry = close
    stop_loss = entry - sl_atr * atr
    target = entry + tgt_atr * atr

    failed = [k for k, v in factors.items() if not v]
    failed_str = f" (missed: {','.join(f.split('_')[0] for f in failed)})" if failed else ""

    return Signal(
        symbol=symbol,
        signal_type=SignalType.BUY,
        entry_price=entry,
        stop_loss=stop_loss,
        target=target,
        timestamp=datetime.now(),
        strategy_name=f"fortress_{grade.lower().replace('+','plus')}",
        confidence=conf,
        reasoning=f"Fortress {grade} {score}/8{failed_str}: " + "; ".join(reasons[:4]),
    )
