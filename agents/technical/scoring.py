"""Candidate Scoring — assigns 0-100 score to every stock in universe.

Used for the dashboard's "Top Watchlist" — even stocks without active BUY signals
get a score based on how close they are to a breakout setup.

Score breakdown (out of 100):
    35 pts — Distance from 20-day high (0% away = 35, 5%+ away = 0)
    25 pts — RSI in optimal zone (50-65 = peak score)
    20 pts — Volume vs average (1.5x+ = full points)
    15 pts — EMA alignment (fast > slow > trend = full points)
     5 pts — Trend strength (recent ROC)

Higher score = closer to triggering a buy. Use to:
    - Build a watchlist (show top 20 daily)
    - Detect "almost-signals" (score 70+ but didn't trigger)
    - Sector rotation analysis (heatmap)
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pandas as pd


@dataclass
class CandidateScore:
    symbol: str
    score: float                        # 0-100
    last_close: float
    rsi: float
    distance_from_high_pct: float       # how far below 20-day high
    volume_ratio: float                 # vs 20-day avg
    ema_aligned: bool
    trend_pct: float                    # 5-day ROC
    sector: str = ""
    grade: str = "F"                    # A+ / A / B / C / D / F


def _grade(score: float) -> str:
    if score >= 85: return "A+"
    if score >= 75: return "A"
    if score >= 65: return "B"
    if score >= 55: return "C"
    if score >= 40: return "D"
    return "F"


def score_stock(df: pd.DataFrame, symbol: str = "", sector: str = "") -> Optional[CandidateScore]:
    """Score a single stock based on indicator-rich dataframe.

    Expects df with columns: close, high, low, volume, rsi, ema_fast, ema_slow, ema_trend.
    Returns None if data is insufficient.
    """
    if df is None or df.empty or len(df) < 25:
        return None

    last = df.iloc[-1]
    last_close = float(last["close"])

    # 1. Distance from 20-period high (35 pts max)
    # Sweet spot for breakout entry:
    #   - At/just below high (best — primed to break out)
    #   - Just broke out 0-1% above (still fresh entry)
    #   - Already extended >3% above = late entry
    #   - Far below high (>5%) = not in breakout setup
    period_high = float(df["high"].iloc[-21:-1].max())
    if period_high <= 0:
        return None
    distance_pct = ((period_high - last_close) / period_high) * 100
    if distance_pct >= 0:
        # Below or at high — measure how close to breakout
        if distance_pct <= 0.5:    pts_distance = 35.0
        elif distance_pct <= 1.5:  pts_distance = 30.0
        elif distance_pct <= 3.0:  pts_distance = 22.0
        elif distance_pct <= 5.0:  pts_distance = 12.0
        else:                       pts_distance = 0.0
    else:
        # Already broke out — penalize stretched moves
        extended = abs(distance_pct)
        if extended <= 1.0:         pts_distance = 33.0   # fresh breakout
        elif extended <= 2.5:       pts_distance = 25.0
        elif extended <= 5.0:       pts_distance = 15.0
        elif extended <= 8.0:       pts_distance = 6.0
        else:                       pts_distance = 0.0    # too late
    pts_distance = max(0.0, min(35.0, pts_distance))    # hard cap

    # 2. RSI optimal zone (25 pts max)
    # Sweet spot 55-65 (bullish but not exhausted)
    # Above 70 = overbought, penalize heavily
    rsi = float(last.get("rsi", 50.0))
    if pd.isna(rsi):
        rsi = 50.0
    if 55 <= rsi <= 65:
        pts_rsi = 25.0                          # peak
    elif 50 <= rsi < 55:
        pts_rsi = 15.0 + (rsi - 50) * 2.0       # 15 → 25
    elif 65 < rsi <= 70:
        pts_rsi = 25.0 - (rsi - 65) * 3.0       # 25 → 10
    elif 70 < rsi <= 75:
        pts_rsi = 10.0 - (rsi - 70) * 2.0       # 10 → 0
    elif 40 <= rsi < 50:
        pts_rsi = (rsi - 40) * 1.5              # 0 → 15
    else:
        pts_rsi = 0.0
    pts_rsi = max(0.0, min(25.0, pts_rsi))

    # 3. Volume ratio (20 pts)
    avg_vol = float(df["volume"].iloc[-21:-1].mean())
    last_vol = float(last["volume"])
    vol_ratio = (last_vol / avg_vol) if avg_vol > 0 else 0.0
    # 1.5x+ = full 20 pts
    pts_volume = min(20.0, max(0.0, (vol_ratio - 0.5) * 20.0))

    # 4. EMA alignment (15 pts)
    ema_f = float(last.get("ema_fast", last_close))
    ema_s = float(last.get("ema_slow", last_close))
    ema_t = float(last.get("ema_trend", last_close))
    if any(pd.isna(x) for x in (ema_f, ema_s, ema_t)):
        ema_aligned = False
        pts_ema = 0.0
    else:
        ema_aligned = ema_f > ema_s > ema_t
        if ema_aligned:
            pts_ema = 15.0
        elif ema_f > ema_s:
            pts_ema = 8.0
        else:
            pts_ema = 0.0

    # 5. Trend strength — 5-day ROC (5 pts)
    if len(df) >= 6:
        roc = ((last_close - float(df["close"].iloc[-6])) / float(df["close"].iloc[-6])) * 100
        pts_trend = min(5.0, max(0.0, roc))     # cap at 5
    else:
        roc = 0.0
        pts_trend = 0.0

    total = pts_distance + pts_rsi + pts_volume + pts_ema + pts_trend
    total = max(0.0, min(100.0, total))    # hard cap 0-100
    return CandidateScore(
        symbol=symbol,
        score=round(total, 1),
        last_close=last_close,
        rsi=round(rsi, 1),
        distance_from_high_pct=round(distance_pct, 2),
        volume_ratio=round(vol_ratio, 2),
        ema_aligned=ema_aligned,
        trend_pct=round(roc, 2),
        sector=sector,
        grade=_grade(total),
    )
