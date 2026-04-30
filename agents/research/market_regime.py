"""Market Regime Detector — answer "is the broad market healthy?"

Uses Nifty 50 + Nifty 500 + India VIX to classify:
    BULLISH  — Nifty > 200-EMA, RSI 50-70, VIX low → take BUY signals freely
    NEUTRAL  — choppy, sideways → reduce position size by 50%
    BEARISH  — Nifty < 200-EMA, declining → BLOCK new BUYs entirely
    CRASH    — VIX > 25, Nifty -5%+ in 5 days → exit all longs

The trading bot checks this BEFORE every BUY decision.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).parent.parent.parent / "data"
REGIME_FILE = DATA_DIR / "market_regime.csv"


@dataclass
class MarketRegime:
    regime: str                  # BULLISH | NEUTRAL | BEARISH | CRASH
    nifty_close: float
    nifty_vs_200ema_pct: float   # +ve = above, -ve = below
    nifty_rsi: float
    nifty_5d_pct: float
    vix: float
    confidence: float            # 0-1
    reasoning: str
    position_size_multiplier: float  # 1.0 = full, 0.5 = half, 0.0 = none


def detect_regime() -> MarketRegime:
    """Pull NIFTY 50 + India VIX from yfinance, classify regime."""
    try:
        nifty = yf.download("^NSEI", period="250d", interval="1d",
                            progress=False, auto_adjust=True)
        if hasattr(nifty.columns, "get_level_values"):
            nifty.columns = nifty.columns.get_level_values(0)
        nifty.columns = [c.lower() for c in nifty.columns]
    except Exception:
        return MarketRegime(
            regime="UNKNOWN", nifty_close=0, nifty_vs_200ema_pct=0,
            nifty_rsi=50, nifty_5d_pct=0, vix=0, confidence=0,
            reasoning="data fetch failed", position_size_multiplier=0.5,
        )

    if nifty.empty or len(nifty) < 200:
        return MarketRegime(
            regime="UNKNOWN", nifty_close=0, nifty_vs_200ema_pct=0,
            nifty_rsi=50, nifty_5d_pct=0, vix=0, confidence=0,
            reasoning="insufficient history", position_size_multiplier=0.5,
        )

    # Indicators
    close = float(nifty["close"].iloc[-1])
    ema200 = float(nifty["close"].ewm(span=200, adjust=False).mean().iloc[-1])
    ema50 = float(nifty["close"].ewm(span=50, adjust=False).mean().iloc[-1])
    pct_vs_200 = (close - ema200) / ema200 * 100

    # 14-period RSI
    delta = nifty["close"].diff()
    gain = delta.where(delta > 0, 0).rolling(14).mean()
    loss = -delta.where(delta < 0, 0).rolling(14).mean()
    rs = gain / loss.replace(0, 1e-9)
    rsi = float((100 - 100 / (1 + rs)).iloc[-1])

    # 5-day % change
    if len(nifty) >= 6:
        pct_5d = (close - float(nifty["close"].iloc[-6])) / float(nifty["close"].iloc[-6]) * 100
    else:
        pct_5d = 0.0

    # India VIX
    vix_val = 15.0
    try:
        vix_df = yf.download("^INDIAVIX", period="10d", interval="1d",
                              progress=False, auto_adjust=True)
        if hasattr(vix_df.columns, "get_level_values"):
            vix_df.columns = vix_df.columns.get_level_values(0)
        if not vix_df.empty:
            vix_val = float(vix_df["Close"].iloc[-1])
    except Exception:
        pass

    # Classification (priority: CRASH > BEARISH > NEUTRAL > BULLISH)
    reasons = []
    if vix_val > 25 or pct_5d < -5:
        regime = "CRASH"
        size_mult = 0.0
        reasons.append(f"VIX={vix_val:.1f}, 5d={pct_5d:+.1f}% — exit longs")
    elif close < ema200 and rsi < 45:
        regime = "BEARISH"
        size_mult = 0.0
        reasons.append(f"Nifty {pct_vs_200:+.1f}% vs 200-EMA, RSI {rsi:.0f} — block BUYs")
    elif close < ema50 or rsi < 50:
        regime = "NEUTRAL"
        size_mult = 0.5
        reasons.append(f"Choppy: close vs 50-EMA={(close-ema50)/ema50*100:+.1f}%, "
                       f"RSI={rsi:.0f} — half-size only")
    else:
        regime = "BULLISH"
        size_mult = 1.0
        reasons.append(f"Nifty +{pct_vs_200:.1f}% vs 200-EMA, RSI {rsi:.0f}, "
                       f"VIX {vix_val:.0f} — full size")

    return MarketRegime(
        regime=regime,
        nifty_close=close,
        nifty_vs_200ema_pct=round(pct_vs_200, 2),
        nifty_rsi=round(rsi, 1),
        nifty_5d_pct=round(pct_5d, 2),
        vix=round(vix_val, 1),
        confidence=0.8,
        reasoning="; ".join(reasons),
        position_size_multiplier=size_mult,
    )


def save_regime(regime: MarketRegime) -> None:
    """Append snapshot to data/market_regime.csv for dashboard history."""
    DATA_DIR.mkdir(exist_ok=True, parents=True)
    new_exists = REGIME_FILE.exists()
    with open(REGIME_FILE, "a", encoding="utf-8") as f:
        if not new_exists:
            f.write("timestamp,regime,nifty_close,nifty_vs_200ema_pct,nifty_rsi,nifty_5d_pct,vix,size_mult,reasoning\n")
        f.write(
            f"{datetime.now().isoformat(timespec='seconds')},"
            f"{regime.regime},{regime.nifty_close:.2f},"
            f"{regime.nifty_vs_200ema_pct},{regime.nifty_rsi},"
            f"{regime.nifty_5d_pct},{regime.vix},"
            f"{regime.position_size_multiplier},"
            f'"{regime.reasoning}"\n'
        )


def detect_sector_strength(candidates_df: pd.DataFrame) -> dict[str, dict]:
    """Compute per-sector strength using RELATIVE ranking.

    Top 30% of sectors (by avg score) = STRONG (full size).
    Middle 40% = NEUTRAL (70% size).
    Bottom 30% = WEAK (30% size — but Tier 0 signals can still trade).

    This way, sector classification adapts to market conditions:
    in any market there are always relative leaders + laggards.
    """
    out = {}
    if candidates_df is None or candidates_df.empty or "sector" not in candidates_df.columns:
        return out

    # Aggregate per sector
    rows = []
    for sector, group in candidates_df.groupby("sector"):
        if len(group) < 3:
            continue
        rows.append({
            "sector": sector,
            "avg_score": float(group["score"].mean()),
            "n_stocks": len(group),
            "n_above_70": int((group["score"] > 70).sum()),
        })

    if not rows:
        return out

    df = pd.DataFrame(rows).sort_values("avg_score", ascending=False).reset_index(drop=True)
    n = len(df)
    strong_n = max(1, int(n * 0.30))
    weak_n = max(1, int(n * 0.30))

    for i, row in df.iterrows():
        if i < strong_n:
            strength, mult = "STRONG", 1.0
        elif i >= n - weak_n:
            strength, mult = "WEAK", 0.3
        else:
            strength, mult = "NEUTRAL", 0.7
        out[row["sector"]] = {
            "strength": strength,
            "avg_score": round(row["avg_score"], 1),
            "n_stocks": int(row["n_stocks"]),
            "n_above_70": int(row["n_above_70"]),
            "size_mult": mult,
        }
    return out


def save_sector_strength(sector_map: dict) -> None:
    """Save sector strength snapshot to data/sector_strength.csv."""
    if not sector_map:
        return
    rows = []
    ts = datetime.now().isoformat(timespec="seconds")
    for sector, info in sector_map.items():
        rows.append({
            "timestamp": ts, "sector": sector,
            "strength": info["strength"], "avg_score": info["avg_score"],
            "n_stocks": info["n_stocks"], "n_above_70": info["n_above_70"],
            "size_mult": info["size_mult"],
        })
    DATA_DIR.mkdir(exist_ok=True, parents=True)
    f = DATA_DIR / "sector_strength.csv"
    df_new = pd.DataFrame(rows)
    if f.exists():
        df_old = pd.read_csv(f)
        df_combined = pd.concat([df_old, df_new], ignore_index=True)
    else:
        df_combined = df_new
    # Keep only last 100 snapshots per sector to control file size
    df_combined.to_csv(f, index=False)


def get_latest_regime() -> Optional[MarketRegime]:
    """Read most recent regime from CSV (avoid re-fetching)."""
    if not REGIME_FILE.exists():
        return None
    try:
        df = pd.read_csv(REGIME_FILE)
        if df.empty:
            return None
        last = df.iloc[-1]
        return MarketRegime(
            regime=str(last["regime"]),
            nifty_close=float(last["nifty_close"]),
            nifty_vs_200ema_pct=float(last["nifty_vs_200ema_pct"]),
            nifty_rsi=float(last["nifty_rsi"]),
            nifty_5d_pct=float(last["nifty_5d_pct"]),
            vix=float(last["vix"]),
            confidence=0.8,
            reasoning=str(last["reasoning"]),
            position_size_multiplier=float(last["size_mult"]),
        )
    except Exception:
        return None
