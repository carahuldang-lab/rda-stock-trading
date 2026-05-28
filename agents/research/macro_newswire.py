"""Macro Newswire — portfolio-wide impact monitor.

Tracks macro moves (USD/INR, crude, US/Asia markets, VIX) + global headline events.
When moves exceed threshold or major event detected, Claude assesses impact on
the user's specific portfolio and sends advice via RDA Newswire bot.

Cron: 4x daily (07:30 pre-mkt, 12:00 mid-day, 15:45 post-close, 18:00 evening)."""
from __future__ import annotations
import os, sys, json, hashlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import pandas as pd
import yfinance as yf
from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
STATE = DATA / ".macro_seen.json"
IST = ZoneInfo("Asia/Kolkata")

TG_TOKEN = os.getenv("TELEGRAM_NEWS_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")

MACRO_TICKERS = {
    "USD/INR":      "INR=X",
    "Crude (WTI)":  "CL=F",
    "Brent":        "BZ=F",
    "Gold":         "GC=F",
    "S&P 500":      "^GSPC",
    "Nasdaq":       "^IXIC",
    "Dow":          "^DJI",
    "Nikkei":       "^N225",
    "Hang Seng":    "^HSI",
    "Nifty":        "^NSEI",
    "Bank Nifty":   "^NSEBANK",
    "India VIX":    "^INDIAVIX",
    "DXY":          "DX-Y.NYB",
}

SIGNIFICANT_MOVE_PCT = 1.5  # |pct| > this = noteworthy


def load_state() -> dict:
    if STATE.exists():
        try: return json.loads(STATE.read_text())
        except: pass
    return {"last_alert_hash": "", "last_alert_ts": ""}


def save_state(s: dict) -> None:
    STATE.write_text(json.dumps(s, indent=2))


def fetch_macro_quotes() -> dict:
    out = {}
    for name, ticker in MACRO_TICKERS.items():
        try:
            h = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=False)
            if len(h) < 2: continue
            last = float(h["Close"].iloc[-1])
            prev = float(h["Close"].iloc[-2])
            pct = (last - prev) / prev * 100 if prev else 0
            out[name] = {"last": round(last, 2), "prev": round(prev, 2), "pct": round(pct, 2)}
        except Exception: pass
    return out


def get_portfolio() -> dict:
    out = {"holdings": [], "bot_positions": [], "watchlist": []}
    try:
        gh = pd.read_csv(DATA / "groww_holdings.csv")
        out["holdings"] = gh.to_dict("records")
    except: pass
    try:
        pos = pd.read_csv(DATA / "positions.csv")
        out["bot_positions"] = pos[["symbol","quantity","entry_price","sector"]].to_dict("records")
    except: pass
    try:
        wl = pd.read_csv(DATA / "watchlist.csv")
        out["watchlist"] = wl["symbol"].dropna().astype(str).tolist()
    except: pass
    return out


def detect_significant_moves(quotes: dict) -> list:
    significant = []
    for name, q in quotes.items():
        if abs(q.get("pct", 0)) >= SIGNIFICANT_MOVE_PCT:
            direction = "↑" if q["pct"] > 0 else "↓"
            significant.append(f"{name} {direction}{abs(q['pct']):.2f}% (now {q['last']})")
    return significant


def call_claude_macro(quotes: dict, portfolio: dict, significant: list) -> str:
    if not CLAUDE_KEY: return ""
    system = """You are RDA Macro Newswire — Indian equity macro analyst.
You see global moves (US, Asia, crude, currency) and assess impact on a specific Indian portfolio.

Output a single Telegram message (Markdown, <2000 chars) structured as:
*🌍 MACRO PULSE - HH:MM IST*

*Key moves:* (1 line, biggest)
*Sentiment for Indian equities:* RISK-ON / RISK-OFF / NEUTRAL (1 line why)

*Impact on your portfolio:*
- Sector-by-sector: which holdings benefit/suffer
- Specific action items: hold/trim/add for any concentrated risk

Be SHARP. Cite actual numbers. Skip filler. If nothing material, say "No portfolio-impacting macro events."."""
    user_data = {
        "macro_quotes": quotes,
        "significant_moves": significant,
        "portfolio": portfolio,
        "timestamp": datetime.now(IST).isoformat()
    }
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": CLAUDE_MODEL, "max_tokens": 1200, "system": system,
                  "messages": [{"role":"user","content": json.dumps(user_data, default=str)[:10000]}]},
            timeout=60)
        if not r.ok: return f"API err {r.status_code}: {r.text[:200]}"
        return "".join(b.get("text","") for b in r.json().get("content",[]) if b.get("type")=="text").strip()
    except Exception as e:
        return f"err: {e}"


def send_telegram(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT: return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for attempt, payload in enumerate([
        {"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"},
        {"chat_id": TG_CHAT, "text": text},
    ]):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.ok: return True
            print(f"[macro] tg attempt {attempt+1} HTTP {r.status_code}")
        except Exception as e:
            print(f"[macro] tg err: {e}")
    return False


def main():
    print(f"[{datetime.now(IST).isoformat()}] macro_newswire run")
    quotes = fetch_macro_quotes()
    if not quotes: print("[macro] no quotes"); return
    portfolio = get_portfolio()
    significant = detect_significant_moves(quotes)
    print(f"[macro] {len(quotes)} quotes, {len(significant)} significant moves")
    # Dedup: only alert if state has changed or 4h passed
    sig_hash = hashlib.md5("|".join(sorted(significant)).encode()).hexdigest()
    state = load_state()
    last_ts = state.get("last_alert_ts", "")
    hours_since = 999
    if last_ts:
        try:
            hours_since = (datetime.now(IST) - datetime.fromisoformat(last_ts)).total_seconds() / 3600
        except: pass
    if state.get("last_alert_hash") == sig_hash and hours_since < 4:
        print(f"[macro] same moves, dedup'd (last alert {hours_since:.1f}h ago)"); return
    msg = call_claude_macro(quotes, portfolio, significant)
    if not msg: print("[macro] empty claude response"); return
    if send_telegram(msg):
        save_state({"last_alert_hash": sig_hash, "last_alert_ts": datetime.now(IST).isoformat()})
        print(f"[macro] alert sent, {len(msg)} chars")


if __name__ == "__main__":
    main()
