"""MF Inflow Tracker — monthly digest using AMFI/Trendlyne-style public data.
Currently fetches MF flows from Trendlyne-public/MorningstarIndia where free, and
asks Claude to assess which of user's holdings have meaningful MF interest."""
from __future__ import annotations
import os, json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests, pandas as pd
from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
IST = ZoneInfo("Asia/Kolkata")
TG = os.getenv("TELEGRAM_NEWS_BOT_TOKEN","")
CHAT = os.getenv("TELEGRAM_CHAT_ID","")
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY","")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL","claude-sonnet-4-5-20250929")


def universe():
    u = set()
    for f in ["watchlist.csv","groww_holdings.csv"]:
        try:
            df = pd.read_csv(DATA / f)
            u.update(df["symbol"].dropna().astype(str).str.upper())
        except: pass
    return list(u)


def send_tg(text):
    if not TG or not CHAT: return False
    url = f"https://api.telegram.org/bot{TG}/sendMessage"
    for p in [{"chat_id":CHAT,"text":text,"parse_mode":"Markdown"},{"chat_id":CHAT,"text":text}]:
        try:
            if requests.post(url,json=p,timeout=10).ok: return True
        except: pass
    return False


def main():
    if not CLAUDE_KEY: return
    uni = universe()
    # Use Claude web search to look up latest monthly MF activity for each held stock
    system = """You are RDA Institutional Tracker. Use web_search to find for each of these Indian stocks: Which mutual funds (AMCs) bought / sold meaningful quantities in their LATEST monthly disclosure?

Focus on:
- Top buys by big AMCs (HDFC, ICICI, SBI, Nippon, Axis, Mirae, Kotak, Quant, Motilal Oswal)
- Top sells (especially Quant — they're known leading indicator)
- New AMC entries

Output (Markdown, <2500 chars):
*🏦 MF INFLOW DIGEST — MMMM YYYY*

For each stock with notable activity:
*SYMBOL:* AMCs buying (qty/value), AMCs selling, net signal
*BOTTOM LINE:* 1 sentence — institutional sentiment for your portfolio

If no data found for a stock, skip it silently. If NO activity found at all, say so."""
    payload = {"watchlist_and_holdings": uni, "month": datetime.now(IST).strftime("%B %Y")}
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":CLAUDE_KEY,"anthropic-version":"2023-06-01",
                     "content-type":"application/json","anthropic-beta":"web-search-2025-03-05"},
            json={"model":CLAUDE_MODEL,"max_tokens":3000,"system":system,
                  "tools":[{"type":"web_search_20250305","name":"web_search","max_uses":6}],
                  "messages":[{"role":"user","content":json.dumps(payload)}]}, timeout=180)
        if r.ok:
            text = "".join(b.get("text","") for b in r.json().get("content",[]) if b.get("type")=="text").strip()
            if text: send_tg(text); print(f"[mf] sent {len(text)} chars")
    except Exception as e:
        print(f"[mf] err: {e}")


if __name__=="__main__": main()
