"""Market Brief — daily news + market context summaries via Claude API.

Modes:
  premarket  (08:30 IST)  - overnight US, Asian markets, crude, INR, top India biz news
  midday     (12:30/14:30) - intraday Nifty/sector moves, RBI/govt actions
  eod        (16:00 IST)  - today's close, FII/DII, portfolio P&L, tomorrow catalysts

Output: Single Telegram message per run, Claude-summarized."""
from __future__ import annotations
import os, sys, json, argparse
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
IST = ZoneInfo("Asia/Kolkata")

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")


def send_tg(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        print("[brief] no telegram creds")
        return
    r = requests.post(
        f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
        json={"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"},
        timeout=10,
    )
    if not r.ok:
        print(f"[brief] tg fail {r.status_code}: {r.text[:200]}")


def fetch_quote(ticker: str) -> dict:
    """Returns {last, prev_close, pct_change} for a yfinance ticker."""
    try:
        h = yf.Ticker(ticker).history(period="5d", interval="1d", auto_adjust=False)
        if len(h) < 2:
            return {}
        last = float(h["Close"].iloc[-1])
        prev = float(h["Close"].iloc[-2])
        return {"last": round(last, 2), "prev": round(prev, 2),
                "pct": round((last - prev) / prev * 100, 2)}
    except Exception:
        return {}


def collect_premarket_context() -> dict:
    """Overnight US + Asian + commodities + currencies."""
    ctx = {"mode": "premarket", "ts": datetime.now(IST).isoformat()}
    ctx["us_markets"] = {
        "S&P 500": fetch_quote("^GSPC"),
        "Nasdaq":  fetch_quote("^IXIC"),
        "Dow":     fetch_quote("^DJI"),
    }
    ctx["asia"] = {
        "Nikkei":     fetch_quote("^N225"),
        "Hang Seng":  fetch_quote("^HSI"),
        "Shanghai":   fetch_quote("000001.SS"),
    }
    ctx["commodities"] = {
        "Crude (WTI)": fetch_quote("CL=F"),
        "Gold":        fetch_quote("GC=F"),
        "Brent":       fetch_quote("BZ=F"),
    }
    ctx["currency"] = {
        "USD/INR":     fetch_quote("INR=X"),
        "DXY":         fetch_quote("DX-Y.NYB"),
    }
    ctx["india_indices"] = {
        "Nifty 50":    fetch_quote("^NSEI"),
        "Bank Nifty":  fetch_quote("^NSEBANK"),
        "VIX (India)": fetch_quote("^INDIAVIX"),
    }
    # Last news for held stocks (top 5)
    try:
        news = pd.read_csv(DATA / "news.csv").tail(20)
        ctx["recent_india_news"] = news[["symbol","headline","sentiment"]].to_dict("records") if not news.empty else []
    except Exception:
        ctx["recent_india_news"] = []
    return ctx


def collect_midday_context() -> dict:
    ctx = {"mode": "midday", "ts": datetime.now(IST).isoformat()}
    ctx["india_indices"] = {
        "Nifty 50":    fetch_quote("^NSEI"),
        "Bank Nifty":  fetch_quote("^NSEBANK"),
        "VIX":         fetch_quote("^INDIAVIX"),
    }
    try:
        regime = pd.read_csv(DATA / "market_regime.csv").iloc[-1].to_dict()
        ctx["regime"] = {k: str(v) for k, v in regime.items() if pd.notna(v)}
    except Exception: ctx["regime"] = {}
    try:
        sectors = pd.read_csv(DATA / "sector_strength.csv")
        ctx["top_sectors"] = sectors.head(5).to_dict("records")
        ctx["bot_sectors"] = sectors.tail(5).to_dict("records")
    except Exception: ctx["top_sectors"] = ctx["bot_sectors"] = []
    try:
        gainers = pd.read_csv(DATA / "groww_top_gainers.csv").head(10)
        ctx["top_gainers"] = gainers.to_dict("records") if not gainers.empty else []
    except Exception: ctx["top_gainers"] = []
    try:
        positions = pd.read_csv(DATA / "positions.csv")
        ctx["my_positions"] = positions[["symbol","quantity","entry_price","strategy"]].to_dict("records")
    except Exception: ctx["my_positions"] = []
    return ctx


def collect_eod_context() -> dict:
    ctx = collect_midday_context()
    ctx["mode"] = "eod"
    try:
        events = []
        with (DATA / "events.jsonl").open() as f:
            for line in f:
                if "2026-" not in line: continue
                e = json.loads(line)
                if e["timestamp"].startswith(datetime.now(IST).strftime("%Y-%m-%d")):
                    events.append(e)
        ctx["today_fills"] = [e for e in events if e.get("agent")=="execution" and "fill" in e.get("action","")]
        ctx["today_signals"] = sum(1 for e in events if e.get("action")=="signal_generated")
        ctx["today_rejected"] = sum(1 for e in events if e.get("action")=="trade_rejected")
    except Exception: pass
    return ctx


PROMPT_BY_MODE = {
    "premarket": """You are sending a pre-market brief to an Indian retail equity trader (Nifty 500 swing trader, ₹20L capital, currently 8 open positions).

Summarize the overnight global setup IN ONE TELEGRAM MESSAGE (max 800 chars, Markdown).

Structure:
1. *Headline*: 1-line directional read (e.g. "Cautious open expected — US -0.4%, Crude +1.2%")
2. *US/Asia*: 1 line each with key %s
3. *Currency/Commodities*: 1 line — USD/INR + crude moves
4. *India outlook*: 1 line — Nifty likely direction + what to watch
5. *Top news* (if news_csv has fresh items): 1-2 lines highlighting positive/negative for Indian large caps

Don't be vague. Cite actual %s from the data. Skip sections with no data.""",

    "midday": """You are sending a mid-day pulse to an Indian retail trader. Market is open.

Summarize IN ONE TELEGRAM MESSAGE (max 600 chars, Markdown).

Structure:
1. *Pulse* (1 line): Nifty current % + VIX + regime label
2. *Sector*: 1 line — strongest + weakest sector
3. *Movers*: 1 line — top 2-3 gainers
4. *Portfolio*: 1 line — positions count + any held-stock making big move (>3%)

Be concise. If nothing notable, say so.""",

    "eod": """You are sending an EOD wrap to an Indian retail trader. Market just closed.

Summarize IN ONE TELEGRAM MESSAGE (max 1000 chars, Markdown).

Structure:
1. *Day*: Nifty close % + VIX
2. *Bot performance*: today's fills, rejected signals, win rate from fills (if any closed)
3. *My positions*: total count, biggest winner today, biggest loser today (cite %)
4. *Tomorrow watch*: 1-2 lines — any catalysts (earnings, RBI events, US data), regime outlook"""
}


def call_claude(prompt: str, context: dict) -> str:
    if not CLAUDE_KEY:
        return "[brief] no Anthropic key"
    user = f"DATA:\n{json.dumps(context, default=str, indent=2)[:12000]}\n\nWrite the Telegram message now."
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": CLAUDE_MODEL, "max_tokens": 1200, "system": prompt,
                  "messages": [{"role": "user", "content": user}]},
            timeout=60,
        )
        if not r.ok:
            return f"[brief] Claude error {r.status_code}: {r.text[:200]}"
        body = r.json()
        return "".join(b.get("text","") for b in body.get("content",[]) if b.get("type")=="text").strip()
    except Exception as e:
        return f"[brief] exception: {e}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["premarket","midday","eod"], required=True)
    args = ap.parse_args()
    print(f"[{datetime.now(IST).isoformat()}] market_brief {args.mode}")
    if args.mode == "premarket": ctx = collect_premarket_context()
    elif args.mode == "midday":  ctx = collect_midday_context()
    else:                         ctx = collect_eod_context()
    prompt = PROMPT_BY_MODE[args.mode]
    summary = call_claude(prompt, ctx)
    header = {"premarket":"🌅 *PRE-MARKET BRIEF*", "midday":"⚡ *MID-DAY PULSE*", "eod":"🌇 *EOD WRAP*"}[args.mode]
    send_tg(f"{header} — {datetime.now(IST).strftime('%H:%M IST')}\n\n{summary}")
    print(f"[brief] sent. Length: {len(summary)}")


if __name__ == "__main__":
    main()
