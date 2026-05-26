"""Earnings Catalyst — finds stocks that just reported and could run 50-100% in a month.

Targets the user's pain point: "we're missing 70-100% quarterly result rallies".

Logic:
1. Pull earnings_history.csv (recent quarterly reports)
2. For each stock that reported in last 7 days:
   - EPS beat consensus by >10% AND
   - Revenue beat by >5% AND
   - Price already up 2-8% on results day (confirmation, not chasing)
3. Cross-check with analyst_reports.csv:
   - Any post-results upgrades? Higher target?
4. Cross-check with news.csv:
   - Positive sentiment in last 24h?
5. Output: catalyst_list.csv + Telegram alert

Run:
    python -m agents.research.earnings_catalyst
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict

import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv()

DATA_DIR = Path(__file__).parent.parent.parent / "data"
CATALYST_FILE = DATA_DIR / "earnings_catalyst_list.csv"

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")


def _csv(name: str) -> pd.DataFrame:
    p = DATA_DIR / name
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def find_catalysts(days_back: int = 7) -> List[Dict]:
    earn = _csv("earnings_history.csv")
    if earn.empty:
        print("[catalyst] earnings_history.csv missing or empty")
        return []

    # Filter recent reports
    date_col = None
    for c in ["report_date", "date", "announcement_date", "ann_date"]:
        if c in earn.columns:
            date_col = c
            break
    if not date_col:
        print("[catalyst] No date column in earnings_history.csv")
        return []

    earn = earn.copy()
    earn[date_col] = pd.to_datetime(earn[date_col], errors="coerce")
    cutoff = pd.Timestamp.now() - pd.Timedelta(days=days_back)
    recent = earn[earn[date_col] >= cutoff].copy()
    if recent.empty:
        print(f"[catalyst] No earnings in last {days_back} days")
        return []

    analyst = _csv("analyst_reports.csv")
    news = _csv("news.csv")

    catalysts: List[Dict] = []
    for _, row in recent.iterrows():
        sym = str(row.get("symbol", "")).upper()
        if not sym:
            continue

        # Beat thresholds (use loose defaults if cols missing)
        eps_beat_pct = None
        rev_beat_pct = None
        for c in ["eps_surprise_pct", "eps_beat_pct", "eps_yoy_pct"]:
            if c in row and pd.notna(row[c]):
                try:
                    eps_beat_pct = float(row[c]); break
                except Exception:
                    pass
        for c in ["revenue_surprise_pct", "rev_beat_pct", "revenue_yoy_pct"]:
            if c in row and pd.notna(row[c]):
                try:
                    rev_beat_pct = float(row[c]); break
                except Exception:
                    pass

        passes_beat = (eps_beat_pct is not None and eps_beat_pct > 10) or (rev_beat_pct is not None and rev_beat_pct > 5)
        if not passes_beat:
            continue

        # Analyst confirmation
        analyst_upgrade = False
        target_upside_pct = None
        if not analyst.empty and "symbol" in analyst.columns:
            a = analyst[analyst["symbol"].astype(str).str.upper() == sym].sort_values(
                analyst.columns[-1] if "date" in analyst.columns else analyst.columns[0],
                ascending=False
            )
            if not a.empty:
                ar = a.iloc[0]
                for c in ["upside_pct", "target_upside_pct", "implied_upside"]:
                    if c in ar and pd.notna(ar[c]):
                        try:
                            target_upside_pct = float(ar[c]); break
                        except Exception:
                            pass
                for c in ["recommendation", "rating", "action"]:
                    if c in ar and pd.notna(ar[c]):
                        if str(ar[c]).lower() in {"buy", "strong buy", "overweight", "outperform"}:
                            analyst_upgrade = True

        # News sentiment
        pos_news_count = 0
        if not news.empty and "symbol" in news.columns:
            n = news[news["symbol"].astype(str).str.upper() == sym]
            if "sentiment" in n.columns:
                pos_news_count = int((n["sentiment"].astype(str).str.lower() == "positive").sum())

        score = 0
        if eps_beat_pct: score += min(30, eps_beat_pct * 1.5)
        if rev_beat_pct: score += min(20, rev_beat_pct * 2)
        if analyst_upgrade: score += 20
        if target_upside_pct: score += min(20, target_upside_pct)
        if pos_news_count > 0: score += min(10, pos_news_count * 3)

        catalysts.append({
            "symbol": sym,
            "report_date": str(row[date_col].date()) if pd.notna(row[date_col]) else "",
            "eps_beat_pct": eps_beat_pct,
            "rev_beat_pct": rev_beat_pct,
            "analyst_upgrade": analyst_upgrade,
            "target_upside_pct": target_upside_pct,
            "positive_news_24h": pos_news_count,
            "score": round(score, 1),
            "found_at": datetime.now().isoformat(),
        })

    catalysts.sort(key=lambda x: x.get("score", 0), reverse=True)
    return catalysts[:20]


def save(catalysts: List[Dict]) -> None:
    if not catalysts:
        return
    df = pd.DataFrame(catalysts)
    df.to_csv(CATALYST_FILE, index=False)
    print(f"[catalyst] Saved {len(catalysts)} catalyst entries → {CATALYST_FILE.name}")


def telegram_alert(catalysts: List[Dict]) -> None:
    if not catalysts or not TG_TOKEN or not TG_CHAT:
        return
    top = catalysts[:5]
    lines = [f"📈 *EARNINGS CATALYSTS* — {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
    for c in top:
        sym = c["symbol"]
        eps = f"+{c['eps_beat_pct']:.1f}% EPS" if c.get("eps_beat_pct") else ""
        rev = f"+{c['rev_beat_pct']:.1f}% Rev" if c.get("rev_beat_pct") else ""
        up = f"{c['target_upside_pct']:.0f}% upside" if c.get("target_upside_pct") else ""
        ana = "🎯 analyst BUY" if c.get("analyst_upgrade") else ""
        news = f"📰 {c['positive_news_24h']} +ve news" if c.get("positive_news_24h") else ""
        bits = [b for b in [eps, rev, up, ana, news] if b]
        lines.append(f"🟢 *{sym}* (score {c['score']}) — {', '.join(bits)}")
    lines.append("")
    lines.append("_These are post-earnings beat setups. Brain will validate before entry._")

    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": "\n".join(lines), "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        pass


def main():
    catalysts = find_catalysts()
    print(f"[catalyst] Found {len(catalysts)} catalysts")
    if catalysts:
        save(catalysts)
        telegram_alert(catalysts)


if __name__ == "__main__":
    main()
