"""News Scraper — pulls latest headlines per stock + sentiment signal.

Sources (in order of preference):
    1. yfinance Ticker.news — Yahoo Finance news (free, reliable)
    2. NSE corporate announcements — fallback for India-specific news

Sentiment (lightweight rule-based):
    NEGATIVE keywords: fraud, raid, downgrade, loss, probe, scam, default,
                     resignation, miss, decline, layoff, cut, plunge, slump
    POSITIVE keywords: beat, upgrade, buyback, dividend, profit, surge, rally,
                     order, contract, deal, partnership, launch
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).parent.parent.parent / "data"
NEWS_FILE = DATA_DIR / "news.csv"

NEGATIVE_KEYWORDS = [
    "fraud", "raid", "downgrade", "loss", "probe", "scam", "default",
    "resignation", "miss", "decline", "layoff", "cut", "plunge", "slump",
    "fall", "crash", "warning", "lawsuit", "bankruptcy", "investigation",
    "penalty", "fine", "weak", "disappointing",
]

POSITIVE_KEYWORDS = [
    "beat", "upgrade", "buyback", "dividend", "profit", "surge", "rally",
    "order", "contract", "deal", "partnership", "launch", "growth", "record",
    "strong", "outperform", "raise", "expand", "approval", "wins", "win",
    "outpaced", "exceed", "guidance",
]


def _classify(headline: str) -> str:
    """Return 'positive' / 'negative' / 'neutral' from keyword counts."""
    text = headline.lower()
    pos = sum(1 for k in POSITIVE_KEYWORDS if k in text)
    neg = sum(1 for k in NEGATIVE_KEYWORDS if k in text)
    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


def fetch_news(symbol: str, limit: int = 5) -> list[dict]:
    """Pull latest news headlines for a symbol via yfinance."""
    try:
        ticker = yf.Ticker(f"{symbol}.NS")
        items = ticker.news or []
    except Exception:
        return []

    out = []
    for item in items[:limit]:
        # yfinance news structure varies — handle both shapes
        content = item.get("content", item)
        title = (content.get("title") or
                 item.get("title") or "")[:200]
        publisher = (content.get("provider", {}).get("displayName") or
                     item.get("publisher") or "Yahoo Finance")
        link = (content.get("canonicalUrl", {}).get("url") or
                item.get("link") or "")
        pub_date = (content.get("pubDate") or
                    item.get("providerPublishTime") or "")
        if isinstance(pub_date, (int, float)):
            pub_date = datetime.fromtimestamp(pub_date).isoformat(timespec="seconds")
        if not title:
            continue
        out.append({
            "symbol": symbol,
            "headline": title,
            "publisher": publisher,
            "url": link,
            "published_at": str(pub_date)[:19],
            "sentiment": _classify(title),
            "fetched_at": datetime.now().isoformat(timespec="seconds"),
        })
    return out


def refresh_news(symbols: list[str]) -> int:
    """Fetch news for all symbols and save to data/news.csv."""
    DATA_DIR.mkdir(exist_ok=True)
    rows = []
    for i, sym in enumerate(symbols, 1):
        if i % 25 == 0:
            print(f"  News progress: {i}/{len(symbols)}")
        rows.extend(fetch_news(sym))

    headers = ["symbol", "headline", "publisher", "url", "published_at",
               "sentiment", "fetched_at"]
    with open(NEWS_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=headers, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    print(f"  Saved {len(rows)} news items for {len(symbols)} symbols")
    return len(rows)


def load_news() -> pd.DataFrame:
    if not NEWS_FILE.exists():
        return pd.DataFrame()
    return pd.read_csv(NEWS_FILE)


def get_negative_news_symbols() -> set[str]:
    """Return set of symbols with at least one recent NEGATIVE headline."""
    df = load_news()
    if df.empty or "sentiment" not in df.columns:
        return set()
    return set(df.loc[df["sentiment"] == "negative", "symbol"].unique())
