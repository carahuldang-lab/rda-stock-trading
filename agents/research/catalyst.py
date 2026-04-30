"""Catalyst Detector — identifies stock-specific positive catalysts.

A catalyst gives a stock-specific tailwind that can override broader market regime.
Categories detected:
    1. Positive news (sentiment = positive in last 48h)
    2. Volume surge (today vol > 2.5x 20-day avg)
    3. Gap-up (open > prev close + 2%)
    4. Analyst upgrade (price target raised)

Used for Tier 1 signals — these can BUY at FULL size even when market regime is NEUTRAL/BEARISH.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).parent.parent.parent / "data"


@dataclass
class Catalyst:
    symbol: str
    has_catalyst: bool
    score: int                    # 0-4
    types: list[str]              # ["news_positive", "volume_surge", ...]
    reasoning: str


def _check_news(symbol: str) -> tuple[bool, str]:
    """Look for positive news in last 48h."""
    f = DATA_DIR / "news.csv"
    if not f.exists():
        return False, ""
    try:
        df = pd.read_csv(f)
        if df.empty or "symbol" not in df.columns:
            return False, ""
        sym_news = df[df["symbol"] == symbol]
        if sym_news.empty:
            return False, ""
        cutoff = datetime.now() - timedelta(days=2)
        sym_news = sym_news.copy()
        sym_news["pub_dt"] = pd.to_datetime(sym_news["fetched_at"], errors="coerce")
        recent = sym_news[sym_news["pub_dt"] >= cutoff]
        positive = recent[recent["sentiment"] == "positive"]
        if not positive.empty:
            return True, f"news positive ({len(positive)} headline{'s' if len(positive) > 1 else ''})"
    except Exception:
        pass
    return False, ""


def _check_volume_surge(df: pd.DataFrame) -> tuple[bool, str]:
    """Today's volume > 2.5x 20-day average."""
    if df is None or len(df) < 21:
        return False, ""
    last_vol = float(df["volume"].iloc[-1])
    avg_vol = float(df["volume"].iloc[-21:-1].mean())
    if avg_vol > 0 and last_vol > 2.5 * avg_vol:
        return True, f"volume {last_vol/avg_vol:.1f}x avg"
    return False, ""


def _check_gap_up(df: pd.DataFrame) -> tuple[bool, str]:
    """Today's open > prev close + 2%."""
    if df is None or len(df) < 2:
        return False, ""
    today_open = float(df["open"].iloc[-1])
    prev_close = float(df["close"].iloc[-2])
    if prev_close > 0 and (today_open - prev_close) / prev_close * 100 > 2.0:
        return True, f"gap up {(today_open - prev_close)/prev_close*100:.1f}%"
    return False, ""


def _check_analyst_upgrade(symbol: str) -> tuple[bool, str]:
    """Recent analyst rating upgrade or strong upside."""
    f = DATA_DIR / "analyst_reports.csv"
    if not f.exists():
        return False, ""
    try:
        df = pd.read_csv(f)
        row = df[df["symbol"] == symbol]
        if row.empty:
            return False, ""
        r = row.iloc[0]
        # Strong consensus = "buy" or "strong_buy" + upside > 15%
        consensus = str(r.get("consensus", "")).lower()
        upside = float(r.get("upside_pct", 0))
        if "buy" in consensus and upside > 15:
            return True, f"analyst buy ({upside:+.0f}% upside)"
    except Exception:
        pass
    return False, ""


def detect_catalyst(symbol: str, df: pd.DataFrame) -> Catalyst:
    """Return Catalyst object indicating if/why a stock has a positive catalyst."""
    types = []
    reasons = []
    score = 0

    has, why = _check_news(symbol)
    if has:
        types.append("news_positive")
        reasons.append(why)
        score += 1

    has, why = _check_volume_surge(df)
    if has:
        types.append("volume_surge")
        reasons.append(why)
        score += 1

    has, why = _check_gap_up(df)
    if has:
        types.append("gap_up")
        reasons.append(why)
        score += 1

    has, why = _check_analyst_upgrade(symbol)
    if has:
        types.append("analyst_upgrade")
        reasons.append(why)
        score += 1

    return Catalyst(
        symbol=symbol,
        has_catalyst=score >= 1,
        score=score,
        types=types,
        reasoning=" | ".join(reasons) if reasons else "no catalyst",
    )
