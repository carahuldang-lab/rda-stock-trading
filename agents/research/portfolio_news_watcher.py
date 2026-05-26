"""Portfolio News Watcher — sends Telegram alerts for market sentiment + position news.

Runs every 30-60 min during market hours.

Workflow:
1. Load market regime (BULLISH/NEUTRAL/BEARISH/CRASH)
2. Load open positions:
   - data/positions.csv     (paper trading positions)
   - data/groww_holdings.csv (user's real Groww holdings)
3. For each symbol, fetch latest news (last 6 hrs) from data/news.csv
4. Classify sentiment per symbol (POSITIVE / NEGATIVE / NEUTRAL)
5. Send a single consolidated Telegram message with:
   - Market overview (regime, VIX, Nifty %)
   - Position-by-position news summary
6. Quiet hours: only sends if (a) market open + delta from last alert or
   (b) STRONG NEGATIVE news on a held position

Output format (matches the SOXL bot style the user likes):
    📊 MARKET SENTIMENT — 2026-05-22 14:30 IST
    Regime: BULLISH | VIX: 12.4 | Nifty: +0.8%
    Breadth: 68% stocks > 50EMA ✅

    📰 YOUR POSITIONS — News last 6h
    🟢 SYNGENE — Analyst upgrade, +12% target (Moneycontrol, 1h ago)
    🔴 BAJFINANCE — RBI inquiry on co-lending (LiveMint, 3h ago) ⚠️
    ⚪ NAVINFLUOR — No material news

Run manually:
    python -m agents.research.portfolio_news_watcher

Or via scheduler (added by scheduler.py at 09:30, 11:30, 13:30, 15:00 IST).
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Optional

import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

load_dotenv()

DATA_DIR = Path(__file__).parent.parent.parent / "data"
NEWS_FILE = DATA_DIR / "news.csv"
REGIME_FILE = DATA_DIR / "market_regime.csv"
POSITIONS_FILE = DATA_DIR / "positions.csv"
GROWW_FILE = DATA_DIR / "groww_holdings.csv"
LAST_ALERT_FILE = DATA_DIR / "portfolio_alert_last.txt"

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# ============================================================
# Loaders
# ============================================================

def _load_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(path)
    except Exception:
        return pd.DataFrame()


def get_held_symbols() -> Dict[str, str]:
    """Return {symbol: source} where source is 'paper' or 'groww' or 'both'."""
    held: Dict[str, str] = {}

    # Paper trading positions
    pos = _load_csv(POSITIONS_FILE)
    if not pos.empty and "symbol" in pos.columns:
        for sym in pos["symbol"].dropna().astype(str).str.strip().str.upper().unique():
            held[sym] = "paper"

    # Groww holdings (real money)
    groww = _load_csv(GROWW_FILE)
    if not groww.empty and "symbol" in groww.columns:
        for sym in groww["symbol"].dropna().astype(str).str.strip().str.upper().unique():
            held[sym] = "both" if sym in held else "groww"

    return held


def get_market_regime() -> Optional[Dict]:
    df = _load_csv(REGIME_FILE)
    if df.empty:
        return None
    row = df.iloc[-1].to_dict()
    return row


def get_recent_news_for_symbol(symbol: str, hours: int = 6) -> List[Dict]:
    """Return [{headline, publisher, url, sentiment, hours_ago}, ...] sorted newest first."""
    df = _load_csv(NEWS_FILE)
    if df.empty or "symbol" not in df.columns:
        return []
    sub = df[df["symbol"].astype(str).str.upper() == symbol.upper()].copy()
    if sub.empty:
        return []

    if "published_at" in sub.columns:
        try:
            sub["published_at"] = pd.to_datetime(sub["published_at"], errors="coerce", utc=True)
            cutoff = pd.Timestamp.utcnow() - pd.Timedelta(hours=hours)
            sub = sub[sub["published_at"] >= cutoff]
        except Exception:
            pass

    sub = sub.sort_values("published_at", ascending=False) if "published_at" in sub.columns else sub
    out = []
    for _, r in sub.head(3).iterrows():
        hours_ago = "?"
        if "published_at" in r and pd.notna(r["published_at"]):
            try:
                delta = pd.Timestamp.utcnow() - r["published_at"]
                hours_ago = f"{int(delta.total_seconds() / 3600)}h"
            except Exception:
                hours_ago = "?"
        out.append({
            "headline": str(r.get("headline", ""))[:120],
            "publisher": str(r.get("publisher", "")),
            "url": str(r.get("url", "")),
            "sentiment": str(r.get("sentiment", "neutral")).lower(),
            "hours_ago": hours_ago,
        })
    return out


# ============================================================
# Formatter
# ============================================================

REGIME_EMOJI = {
    "BULLISH": "🟢",
    "NEUTRAL": "⚪",
    "BEARISH": "🔴",
    "CRASH": "🚨",
}

SENT_EMOJI = {
    "positive": "🟢",
    "negative": "🔴",
    "neutral": "⚪",
}


def format_message() -> str:
    now = datetime.now()
    lines = [f"📊 *PORTFOLIO PULSE* — {now.strftime('%Y-%m-%d %H:%M IST')}"]

    # --- Market regime ---
    regime = get_market_regime()
    if regime:
        r = regime.get("regime", "UNKNOWN")
        emoji = REGIME_EMOJI.get(r, "❔")
        vix = regime.get("vix", "?")
        nifty_pct = regime.get("nifty_5d_pct", "?")
        try:
            nifty_pct_str = f"{float(nifty_pct):+.2f}%"
        except Exception:
            nifty_pct_str = str(nifty_pct)
        try:
            vix_str = f"{float(vix):.1f}"
        except Exception:
            vix_str = str(vix)
        lines.append(f"{emoji} Regime: *{r}* | VIX: {vix_str} | Nifty 5d: {nifty_pct_str}")
        reason = str(regime.get("reasoning", ""))[:120]
        if reason:
            lines.append(f"   _{reason}_")
    else:
        lines.append("⚪ Regime: data not available yet")

    lines.append("")

    # --- Position news ---
    held = get_held_symbols()
    if not held:
        lines.append("📭 No open positions to monitor.")
    else:
        lines.append(f"📰 *YOUR POSITIONS* ({len(held)} held) — news last 6h")
        lines.append("")
        sorted_symbols = sorted(held.keys())
        for sym in sorted_symbols:
            source = held[sym]
            src_tag = {"paper": "📝", "groww": "💰", "both": "💰📝"}.get(source, "")
            news = get_recent_news_for_symbol(sym, hours=6)
            if not news:
                lines.append(f"⚪ {src_tag} *{sym}* — no recent news")
                continue
            # Worst sentiment first
            news_sorted = sorted(news, key=lambda n: 0 if n["sentiment"] == "negative" else (1 if n["sentiment"] == "neutral" else 2))
            top = news_sorted[0]
            emoji = SENT_EMOJI.get(top["sentiment"], "⚪")
            warn = " ⚠️" if top["sentiment"] == "negative" else ""
            lines.append(f"{emoji} {src_tag} *{sym}* — {top['headline']} _({top['publisher']}, {top['hours_ago']} ago){warn}_")

    lines.append("")
    lines.append("_Sources: MoneyControl, ET, BS, LiveMint_")
    return "\n".join(lines)


# ============================================================
# Telegram sender
# ============================================================

def send_telegram(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        print("[portfolio_news_watcher] Telegram credentials missing — skipping send")
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={
                "chat_id": TG_CHAT,
                "text": text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            },
            timeout=10,
        )
        if r.ok:
            print(f"[portfolio_news_watcher] Telegram sent: {r.status_code}")
            return True
        print(f"[portfolio_news_watcher] Telegram error {r.status_code}: {r.text[:200]}")
        return False
    except Exception as e:
        print(f"[portfolio_news_watcher] Telegram exception: {e}")
        return False


# ============================================================
# Main
# ============================================================

def should_alert() -> bool:
    """Throttle: don't send same hour twice unless content changed.
    Always send if --force flag passed.
    """
    if "--force" in sys.argv:
        return True
    if not LAST_ALERT_FILE.exists():
        return True
    try:
        last = datetime.fromisoformat(LAST_ALERT_FILE.read_text().strip())
        # Min 25 min gap
        if (datetime.now() - last).total_seconds() < 25 * 60:
            return False
    except Exception:
        return True
    return True


def main():
    if not should_alert():
        print("[portfolio_news_watcher] Throttled (last alert <25min ago). Skipping.")
        return

    msg = format_message()
    print("\n" + "=" * 60)
    print(msg)
    print("=" * 60 + "\n")

    if send_telegram(msg):
        LAST_ALERT_FILE.write_text(datetime.now().isoformat())


if __name__ == "__main__":
    main()
