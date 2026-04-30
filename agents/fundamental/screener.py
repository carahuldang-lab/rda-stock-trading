"""Fundamental Screener — pull P/E, ROE, debt, market-cap from yfinance.

yfinance provides Yahoo Finance fundamentals for free (with rate limits).
We cache all results to data/fundamentals.csv to avoid re-pulling daily.

Coverage:
    - Indian NSE stocks (use .NS suffix)
    - Updates ~daily on Yahoo's side

Run weekly to refresh:
    python -m agents.fundamental.screener --refresh
"""
from __future__ import annotations

import csv
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).parent.parent.parent / "data"
FUNDAMENTALS_FILE = DATA_DIR / "fundamentals.csv"

HEADERS = [
    "symbol", "company", "sector", "industry",
    "market_cap_cr", "pe_ratio", "pb_ratio", "roe",
    "debt_to_equity", "earnings_growth", "revenue_growth",
    "dividend_yield", "beta", "fifty_day_avg", "two_hundred_day_avg",
    "as_of",
]


def _to_cr(value: Optional[float]) -> float:
    """Convert raw INR amount to crores. Returns 0 on bad input."""
    if value is None or pd.isna(value):
        return 0.0
    try:
        return round(float(value) / 1e7, 2)
    except Exception:
        return 0.0


def _safe(info: dict, key: str, default=0.0) -> float:
    v = info.get(key, default)
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return default
    try:
        return float(v)
    except Exception:
        return default


def fetch_one(symbol: str) -> Optional[dict]:
    """Pull fundamentals for a single symbol via yfinance."""
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        info = ticker.info
        if not info or "longName" not in info:
            return None
        return {
            "symbol": symbol,
            "company": info.get("longName", "")[:60],
            "sector": info.get("sector", "Unknown")[:40],
            "industry": info.get("industry", "")[:40],
            "market_cap_cr": _to_cr(info.get("marketCap")),
            "pe_ratio": round(_safe(info, "trailingPE"), 2),
            "pb_ratio": round(_safe(info, "priceToBook"), 2),
            "roe": round(_safe(info, "returnOnEquity") * 100, 2),
            "debt_to_equity": round(_safe(info, "debtToEquity") / 100, 2),
            "earnings_growth": round(_safe(info, "earningsGrowth") * 100, 2),
            "revenue_growth": round(_safe(info, "revenueGrowth") * 100, 2),
            "dividend_yield": round(_safe(info, "dividendYield") * 100, 2),
            "beta": round(_safe(info, "beta"), 2),
            "fifty_day_avg": round(_safe(info, "fiftyDayAverage"), 2),
            "two_hundred_day_avg": round(_safe(info, "twoHundredDayAverage"), 2),
            "as_of": datetime.now().date().isoformat(),
        }
    except Exception:
        return None


def refresh_all(symbols: list[str], delay: float = 0.4) -> int:
    """Re-pull fundamentals for every symbol. ~1 stock per 0.4s = 200s for 500."""
    DATA_DIR.mkdir(exist_ok=True)
    rows = []
    fail = 0
    for i, sym in enumerate(symbols, 1):
        if i % 25 == 0:
            print(f"  Progress: {i}/{len(symbols)} (failures: {fail})")
        row = fetch_one(sym)
        if row is None:
            fail += 1
            continue
        rows.append(row)
        time.sleep(delay)

    with open(FUNDAMENTALS_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"\n  Saved {len(rows)} rows to {FUNDAMENTALS_FILE} ({fail} failures)")
    return len(rows)


def load_fundamentals() -> pd.DataFrame:
    """Read cached fundamentals — used by FundamentalAgent."""
    if not FUNDAMENTALS_FILE.exists():
        return pd.DataFrame(columns=HEADERS)
    return pd.read_csv(FUNDAMENTALS_FILE)


def is_stale(max_age_days: int = 7) -> bool:
    if not FUNDAMENTALS_FILE.exists():
        return True
    df = load_fundamentals()
    if df.empty or "as_of" not in df.columns:
        return True
    latest = pd.to_datetime(df["as_of"]).max()
    return (datetime.now() - latest) > timedelta(days=max_age_days)


if __name__ == "__main__":
    # CLI usage: refresh top N from Nifty 500
    sys.path.insert(0, str(Path(__file__).parent.parent.parent))
    master = pd.read_csv(DATA_DIR / "nifty500.csv")
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    syms = master["symbol"].dropna().tolist()
    if limit > 0:
        syms = syms[:limit]
    print(f"Refreshing fundamentals for {len(syms)} stocks...")
    refresh_all(syms)
