"""RDA Newswire — event-driven news bot for Indian retail trader.

Monitors watchlist + Groww holdings + bot positions. Pulls fresh news from
yfinance per symbol + existing news.csv. Deduplicates against state file.
For each NEW material news item, asks Claude for impact analysis tailored to
the user's portfolio. Sends to dedicated TELEGRAM_NEWS_BOT.

Runs every 10 min via cron. Sends ONLY when new news is detected (event-driven)."""
from __future__ import annotations
import os, sys, json, hashlib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
SEEN_FILE = DATA / ".newswire_seen.json"
IMPACT_CSV = DATA / "news_impact.csv"
IST = ZoneInfo("Asia/Kolkata")

TG_TOKEN = os.getenv("TELEGRAM_NEWS_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")

MAX_NEWS_PER_RUN = 5  # don't flood Telegram


def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text()).get("hashes", []))
        except Exception: pass
    return set()


def save_seen(hashes: set) -> None:
    # Keep last 5000 hashes to bound state file
    keep = list(hashes)[-5000:]
    SEEN_FILE.write_text(json.dumps({"hashes": keep, "updated": datetime.now(IST).isoformat()}))


def hash_news(symbol: str, headline: str) -> str:
    return hashlib.md5(f"{symbol}|{headline[:200].lower()}".encode()).hexdigest()


def get_universe() -> dict:
    """Returns {symbol: status} where status is 'holding', 'watchlist', or 'bot_position'."""
    uni = {}
    try:
        wl = pd.read_csv(DATA / "watchlist.csv")
        for s in wl["symbol"].dropna().astype(str): uni[s.upper()] = "watchlist"
    except Exception: pass
    try:
        gh = pd.read_csv(DATA / "groww_holdings.csv")
        for s in gh["symbol"].dropna().astype(str): uni[s.upper()] = "holding"
    except Exception: pass
    # NOTE: Bot paper positions intentionally EXCLUDED from newswire
    # per user request — only real Groww holdings + watchlist get news alerts.
    return uni


def fetch_yf_news(symbol: str, limit: int = 3) -> list:
    """Pull recent news for one symbol via yfinance."""
    try:
        t = yf.Ticker(f"{symbol}.NS")
        items = t.news[:limit] if hasattr(t, "news") else []
        out = []
        for it in items:
            content = it.get("content") if isinstance(it, dict) else None
            if content:
                title = content.get("title", "")
                summary = content.get("summary", "")
                pub_date = content.get("pubDate", "")
                publisher = content.get("provider", {}).get("displayName", "")
            else:
                title = it.get("title", "")
                summary = it.get("summary", "")
                pub_date = it.get("providerPublishTime", "")
                publisher = it.get("publisher", "")
            if title:
                out.append({
                    "symbol": symbol, "headline": title[:300],
                    "summary": str(summary)[:500], "publisher": publisher,
                    "published": str(pub_date)
                })
        return out
    except Exception as e:
        return []



RESULT_KEYWORDS = ["q1 result", "q2 result", "q3 result", "q4 result", "quarterly result",
                   "earnings", "eps", "net profit", "revenue grew", "results announced",
                   "result declared", "ebitda", "topline", "bottom line"]

def is_result_news(headline: str, summary: str = "") -> bool:
    text = (headline + " " + (summary or "")).lower()
    return any(k in text for k in RESULT_KEYWORDS)


def claude_analyze(symbol: str, status: str, headline: str, summary: str,
                   position_info: dict | None) -> dict:
    """Ask Claude to score impact + give actionable advice."""
    if not CLAUDE_KEY:
        return {"impact": "?", "advice": "Claude not configured", "severity": 0}
    pos_text = ""
    if position_info:
        pos_text = f"\nUser holds: qty={position_info.get('qty')}, avg=Rs.{position_info.get('avg_price')}, current=Rs.{position_info.get('ltp')}, pnl={position_info.get('pnl_pct')}%"
    system = """You are RDA Newswire — a sharp, decisive Indian equity analyst writing for a retail trader.
For each news item, decide:
1. IMPACT (single word): POSITIVE / NEGATIVE / NEUTRAL
2. SEVERITY (0-10): how material is this for the stock price in next 1-5 trading days
3. ADVICE: 2-3 actionable sentences specific to user's position (hold/add/trim/exit + price levels if relevant)

Output STRICT JSON only:
{"impact":"POSITIVE|NEGATIVE|NEUTRAL","severity":7,"advice":"...","one_line_summary":"..."}"""
    user = f"Stock: {symbol} ({status})\nHeadline: {headline}\nSummary: {summary[:1500]}{pos_text}"
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": CLAUDE_MODEL, "max_tokens": 800, "system": system,
                  "messages": [{"role": "user", "content": user}]}, timeout=45)
        if not r.ok: return {"impact":"?", "advice": f"API err {r.status_code}", "severity": 0}
        text = "".join(b.get("text","") for b in r.json().get("content",[]) if b.get("type")=="text")
        text = text.strip()
        if text.startswith("```"): text = "\n".join(text.split("\n")[1:-1])
        return json.loads(text)
    except Exception as e:
        return {"impact": "?", "advice": str(e)[:200], "severity": 0}


def send_telegram(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        print("[newswire] no telegram creds"); return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for attempt, payload in enumerate([
        {"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"},
        {"chat_id": TG_CHAT, "text": text},
    ]):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.ok: return True
            print(f"[newswire] tg attempt {attempt+1} HTTP {r.status_code}: {r.text[:200]}")
        except Exception as e:
            print(f"[newswire] tg err: {e}")
    return False


def format_message(symbol: str, status: str, headline: str, analysis: dict,
                   position_info: dict | None) -> str:
    impact_emoji = {"POSITIVE": "🟢", "NEGATIVE": "🔴", "NEUTRAL": "⚪"}.get(analysis.get("impact","?"), "❔")
    status_label = {"holding": "💰 HELD", "watchlist": "👀 WATCH", "bot_position": "🤖 BOT"}.get(status, status)
    result_prefix = "📊 *QUARTERLY RESULT* — " if analysis.get("is_result") else ""
    sev = analysis.get("severity", 0)
    sev_bar = "█" * int(sev) + "░" * (10 - int(sev))
    lines = [
        f"{result_prefix}{impact_emoji} *{symbol}* — {status_label}",
        f"_{headline[:240]}_",
        "",
        f"*Impact:* {analysis.get('impact','?')} (severity {sev}/10)  `{sev_bar}`",
    ]
    if analysis.get("one_line_summary"):
        lines.append(f"_{analysis['one_line_summary'][:200]}_")
    lines.append("")
    if position_info:
        lines.append(f"*Your position:* {position_info.get('qty')} @ Rs.{position_info.get('avg_price')} | PnL {position_info.get('pnl_pct')}%")
    lines.append(f"*Advice:* {analysis.get('advice','-')[:600]}")
    lines.append(f"\n_Source: yfinance · {datetime.now(IST).strftime('%H:%M IST')}_")
    return "\n".join(lines)


def append_impact_csv(rows: list) -> None:
    if not rows: return
    df_new = pd.DataFrame(rows)
    if IMPACT_CSV.exists():
        df_old = pd.read_csv(IMPACT_CSV)
        df_new = pd.concat([df_old, df_new], ignore_index=True).tail(2000)
    df_new.to_csv(IMPACT_CSV, index=False)


def main():
    print(f"[{datetime.now(IST).isoformat()}] newswire run")
    universe = get_universe()
    if not universe:
        print("[newswire] empty universe"); return
    print(f"[newswire] monitoring {len(universe)} symbols")

    # Build position info for held stocks
    pos_info = {}
    try:
        gh = pd.read_csv(DATA / "groww_holdings.csv")
        for _, r in gh.iterrows():
            pos_info[str(r["symbol"]).upper()] = {
                "qty": int(r.get("qty", 0)), "avg_price": r.get("avg_price"),
                "ltp": r.get("ltp"), "pnl_pct": r.get("pnl_pct")
            }
    except Exception: pass

    seen = load_seen()
    new_count = 0
    impact_rows = []

    for symbol, status in universe.items():
        if new_count >= MAX_NEWS_PER_RUN: break
        for item in fetch_yf_news(symbol, limit=2):
            h = hash_news(symbol, item["headline"])
            if h in seen: continue
            seen.add(h)
            print(f"[newswire] NEW: {symbol} - {item['headline'][:80]}")
            analysis = claude_analyze(symbol, status, item["headline"],
                                       item.get("summary",""), pos_info.get(symbol))
            sev = analysis.get("severity", 0)
            is_result = is_result_news(item['headline'], item.get('summary',''))
            if is_result:
                sev = max(sev, 6)  # quarterly results = always material
                analysis['severity'] = sev
                analysis['is_result'] = True
            if sev < 3:
                print(f"  skip low severity {sev}")
                continue
            msg = format_message(symbol, status, item["headline"], analysis, pos_info.get(symbol))
            if send_telegram(msg):
                new_count += 1
                impact_rows.append({
                    "timestamp": datetime.now(IST).isoformat(),
                    "symbol": symbol, "status": status,
                    "headline": item["headline"], "impact": analysis.get("impact"),
                    "severity": sev, "advice": analysis.get("advice","")[:500],
                })

    save_seen(seen)
    append_impact_csv(impact_rows)
    print(f"[newswire] sent {new_count} alerts, total seen {len(seen)}")


if __name__ == "__main__":
    main()
