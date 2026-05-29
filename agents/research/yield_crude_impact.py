"""Yield + Crude Impact Mapper — fetches India 10Y G-Sec yield + WTI/Brent crude.
On significant move (>10bps yield, >2% crude), Claude maps impact to user's portfolio."""
from __future__ import annotations
import os, json, hashlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests, pandas as pd, yfinance as yf
from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
STATE = DATA / ".yield_crude_seen.json"
IST = ZoneInfo("Asia/Kolkata")
TG = os.getenv("TELEGRAM_NEWS_BOT_TOKEN","")
CHAT = os.getenv("TELEGRAM_CHAT_ID","")
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY","")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL","claude-sonnet-4-5-20250929")


def fetch_quote(ticker):
    try:
        h = yf.Ticker(ticker).history(period="5d", interval="1d")
        if len(h) < 2: return None
        return {"last": float(h["Close"].iloc[-1]), "prev": float(h["Close"].iloc[-2])}
    except: return None


def universe():
    syms = set()
    for f in ["watchlist.csv","groww_holdings.csv","positions.csv"]:
        try:
            df = pd.read_csv(DATA / f)
            syms.update(df["symbol"].dropna().astype(str).str.upper())
        except: pass
    return list(syms)


def send_tg(text):
    if not TG or not CHAT: return False
    url = f"https://api.telegram.org/bot{TG}/sendMessage"
    for p in [{"chat_id":CHAT,"text":text,"parse_mode":"Markdown"},{"chat_id":CHAT,"text":text}]:
        try:
            if requests.post(url,json=p,timeout=10).ok: return True
        except: pass
    return False


def main():
    yield_10y = fetch_quote("^TNX")  # US 10Y proxy (India G-Sec not on yfinance directly)
    crude_wti = fetch_quote("CL=F")
    crude_brent = fetch_quote("BZ=F")
    moves = []
    if yield_10y:
        bps = (yield_10y["last"] - yield_10y["prev"]) * 100
        moves.append(f"US 10Y yield: {yield_10y['last']:.2f}% ({bps:+.0f}bps)")
    if crude_wti:
        pct = (crude_wti["last"]/crude_wti["prev"]-1)*100
        moves.append(f"WTI crude: ${crude_wti['last']:.2f} ({pct:+.2f}%)")
    if crude_brent:
        pct = (crude_brent["last"]/crude_brent["prev"]-1)*100
        moves.append(f"Brent crude: ${crude_brent['last']:.2f} ({pct:+.2f}%)")

    portfolio = universe()
    if not CLAUDE_KEY:
        print("[yc] no claude key"); return
    system = """You are RDA Macro-Equity Bridge. Given today's bond yield + crude moves and the user's specific Indian equity portfolio, map sector-by-sector impact.

Output (1500 chars Markdown):
*🌊 YIELD + CRUDE IMPACT — DD MMM*
*Today's moves:* (1 line)
*Rate-sensitive impact (your holdings):* list which holdings are affected and how (NBFCs/banks/realty negative on yield spike; debt-laden negative; long-duration positive on yield fall)
*Crude impact (your holdings):* OMC/paint/aviation/cement/chemicals — call out specific stocks
*Action items:* 1-2 specific BUY/TRIM ideas based on the macro

If no significant moves (<10bps yield, <2% crude), say "No actionable macro moves today" — keep it brief."""
    user = json.dumps({"moves": moves, "portfolio": portfolio}, default=str)
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":CLAUDE_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":CLAUDE_MODEL,"max_tokens":1500,"system":system,
                  "messages":[{"role":"user","content":user}]}, timeout=60)
        if r.ok:
            text = "".join(b.get("text","") for b in r.json().get("content",[]) if b.get("type")=="text").strip()
            if text: send_tg(text); print(f"[yc] sent {len(text)} chars")
    except Exception as e:
        print(f"[yc] err: {e}")


if __name__=="__main__": main()
