"""Pump-and-Dump Detector — watchlist + Groww holdings.
If any stock moves >15% in 3 days with abnormal volume profile (early spike, fading),
sends urgent skepticism alert to Newswire bot."""
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
STATE = DATA / ".pump_dump_seen.json"
IST = ZoneInfo("Asia/Kolkata")
TG = os.getenv("TELEGRAM_NEWS_BOT_TOKEN","")
CHAT = os.getenv("TELEGRAM_CHAT_ID","")
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY","")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL","claude-haiku-4-5-20251001")  # cheap classification

MOVE_THRESH_PCT = 15.0  # 3-day move
VOL_FADE_RATIO = 0.7    # last 2 days vol < 70% of 3-day avg = fading interest


def load_seen():
    if STATE.exists():
        try: return json.loads(STATE.read_text())
        except: pass
    return {}


def save_seen(d): STATE.write_text(json.dumps(d))


def universe():
    u = set()
    for f in ["watchlist.csv","groww_holdings.csv"]:
        try:
            df = pd.read_csv(DATA / f)
            u.update(df["symbol"].dropna().astype(str).str.upper())
        except: pass
    return list(u)


def detect_pump(symbol):
    """Returns dict if pump pattern detected, else None."""
    try:
        t = yf.Ticker(f"{symbol}.NS")
        h = t.history(period="10d", interval="1d")
        if len(h) < 5: return None
        last3 = h.tail(3)
        prev3 = h.iloc[-6:-3] if len(h) >= 6 else h.head(3)
        move_pct = (last3["Close"].iloc[-1] / prev3["Close"].iloc[-1] - 1) * 100
        if abs(move_pct) < MOVE_THRESH_PCT: return None
        # Check volume pattern: high early, fading
        early_vol = h.iloc[-3]["Volume"]  # 3 days ago
        recent_vol = h.tail(2)["Volume"].mean()
        avg_vol = h.tail(5)["Volume"].mean()
        is_fading = recent_vol < VOL_FADE_RATIO * avg_vol if avg_vol > 0 else False
        return {
            "symbol": symbol,
            "move_3d_pct": round(float(move_pct), 2),
            "last_price": round(float(h["Close"].iloc[-1]), 2),
            "early_vol": int(early_vol),
            "recent_vol_avg": int(recent_vol),
            "avg_vol_5d": int(avg_vol),
            "vol_fading": is_fading
        }
    except Exception as e:
        return None


def claude_assess(detection):
    if not CLAUDE_KEY: return None
    system = """You are RDA Manipulation Detective. A user's watchlist stock just moved >15% in 3 days.
Assess: Is this a real catalyst-driven move (results / order win / sector rotation) OR a pump-and-dump pattern?

Use web search aggressively to find the cause.

Output (Markdown, <1200 chars):
*🚨 SUSPECT MOVE — SYM ±X%*
*What happened:* 1 line — pump/breakout/result/etc
*Verdict:* GENUINE / SUSPECT PUMP / WATCH
*Why suspect (if so):* low retail awareness, no major news, vol fading, etc
*Action:* HOLD / TRIM 50% / EXIT — with reasoning"""
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":CLAUDE_KEY,"anthropic-version":"2023-06-01",
                     "content-type":"application/json","anthropic-beta":"web-search-2025-03-05"},
            json={"model":CLAUDE_MODEL,"max_tokens":1200,"system":system,
                  "tools":[{"type":"web_search_20250305","name":"web_search","max_uses":3}],
                  "messages":[{"role":"user","content":json.dumps(detection)}]}, timeout=90)
        if r.ok:
            return "".join(b.get("text","") for b in r.json().get("content",[]) if b.get("type")=="text").strip()
    except: pass
    return None


def send_tg(text):
    if not TG or not CHAT: return False
    url = f"https://api.telegram.org/bot{TG}/sendMessage"
    for p in [{"chat_id":CHAT,"text":text,"parse_mode":"Markdown"},{"chat_id":CHAT,"text":text}]:
        try:
            if requests.post(url,json=p,timeout=10).ok: return True
        except: pass
    return False


def main():
    uni = universe()
    seen = load_seen()
    today = datetime.now(IST).strftime("%Y-%m-%d")
    sent = 0
    for sym in uni:
        if sent >= 3: break  # cap to avoid token burn
        det = detect_pump(sym)
        if not det: continue
        key = f"{sym}|{today}"
        if seen.get(key): continue
        seen[key] = datetime.now(IST).isoformat()
        # Only burn Claude tokens if vol fading (more suspect)
        if not det.get("vol_fading"):
            print(f"[pump] {sym} +{det['move_3d_pct']}% but vol healthy; skip Claude")
            continue
        analysis = claude_assess(det)
        if analysis and send_tg(analysis): sent += 1
    save_seen(seen)
    print(f"[pump] {sent} suspect alerts sent")


if __name__=="__main__": main()
