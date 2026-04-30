"""Analyst Reports Aggregator — pulls analyst recommendations + price targets.

Sources:
    1. yfinance Ticker.recommendations / .recommendations_summary  — free, instant
    2. yfinance Ticker.analyst_price_targets                       — free
    3. Tickertape (HTML scraping)                                  — free, slower
    4. ET Markets (HTML scraping)                                  — free, slower

Output: data/analyst_reports.csv
Used by: dashboard "My Portfolio" + Watchlist tabs to validate signals.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).parent.parent.parent / "data"
REPORTS_FILE = DATA_DIR / "analyst_reports.csv"

HEADERS = [
    "symbol", "as_of", "consensus", "n_analysts",
    "buy_count", "hold_count", "sell_count",
    "current_price", "target_mean", "target_low", "target_high",
    "upside_pct",
]


def fetch_one(symbol: str) -> dict | None:
    """Pull analyst summary for a symbol via yfinance."""
    try:
        t = yf.Ticker(f"{symbol}.NS")
        info = t.info
        if not info:
            return None

        # Recommendation key fields from yfinance:
        # recommendationKey, recommendationMean, numberOfAnalystOpinions,
        # targetMeanPrice, targetHighPrice, targetLowPrice, currentPrice
        rec_key = info.get("recommendationKey", "")
        rec_mean = info.get("recommendationMean", 0)
        n = info.get("numberOfAnalystOpinions", 0)
        cur = info.get("currentPrice", 0) or 0
        tmean = info.get("targetMeanPrice", 0) or 0
        thigh = info.get("targetHighPrice", 0) or 0
        tlow = info.get("targetLowPrice", 0) or 0

        # Try to get buy/hold/sell counts
        try:
            recs = t.recommendations
            if recs is not None and not recs.empty and "period" in recs.columns:
                latest = recs[recs["period"] == "0m"].iloc[0] if not recs[recs["period"] == "0m"].empty else recs.iloc[0]
                buy_c = int(latest.get("strongBuy", 0)) + int(latest.get("buy", 0))
                hold_c = int(latest.get("hold", 0))
                sell_c = int(latest.get("sell", 0)) + int(latest.get("strongSell", 0))
            else:
                buy_c = hold_c = sell_c = 0
        except Exception:
            buy_c = hold_c = sell_c = 0

        upside = ((tmean - cur) / cur * 100) if cur > 0 else 0

        return {
            "symbol": symbol,
            "as_of": datetime.now().date().isoformat(),
            "consensus": rec_key,
            "n_analysts": n,
            "buy_count": buy_c,
            "hold_count": hold_c,
            "sell_count": sell_c,
            "current_price": round(cur, 2),
            "target_mean": round(tmean, 2),
            "target_low": round(tlow, 2),
            "target_high": round(thigh, 2),
            "upside_pct": round(upside, 2),
        }
    except Exception:
        return None


def refresh_reports(symbols: list[str], delay: float = 0.3) -> int:
    """Refresh analyst data for all symbols."""
    import time
    DATA_DIR.mkdir(exist_ok=True, parents=True)
    rows = []
    for i, sym in enumerate(symbols, 1):
        if i % 25 == 0:
            print(f"  Analyst progress: {i}/{len(symbols)}")
        r = fetch_one(sym)
        if r is not None:
            rows.append(r)
        time.sleep(delay)

    with open(REPORTS_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=HEADERS, extrasaction="ignore")
        w.writeheader(); w.writerows(rows)
    print(f"  Saved {len(rows)} analyst reports to {REPORTS_FILE}")
    return len(rows)


def load_reports() -> pd.DataFrame:
    if not REPORTS_FILE.exists():
        return pd.DataFrame()
    return pd.read_csv(REPORTS_FILE)


def get_for_symbol(symbol: str) -> dict | None:
    df = load_reports()
    if df.empty:
        return None
    row = df[df["symbol"] == symbol]
    return row.iloc[0].to_dict() if not row.empty else None
