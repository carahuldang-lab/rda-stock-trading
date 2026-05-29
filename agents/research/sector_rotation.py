"""Sectoral Rotation Tracker — tracks Nifty sector indices day-over-day.
Identifies rotation (which sectors gained/lost most) and maps to user's holdings."""
from __future__ import annotations
import os, json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests, pandas as pd, yfinance as yf
from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
IST = ZoneInfo("Asia/Kolkata")
TG = os.getenv("TELEGRAM_NEWS_BOT_TOKEN","")
CHAT = os.getenv("TELEGRAM_CHAT_ID","")
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY","")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL","claude-sonnet-4-5-20250929")

SECTOR_INDICES = {
    "Banking":   "^NSEBANK",
    "IT":        "NIFTYIT.NS",
    "Auto":      "NIFTYAUTO.NS",
    "Pharma":    "NIFTYPHARMA.NS",
    "FMCG":      "NIFTYFMCG.NS",
    "Metal":     "NIFTYMETAL.NS",
    "Realty":    "NIFTYREALTY.NS",
    "PSU Bank":  "NIFTYPSU.NS",
    "Energy":    "NIFTYENERGY.NS",
    "Media":     "NIFTYMEDIA.NS",
}


def fetch_quote(t):
    try:
        h = yf.Ticker(t).history(period="5d", interval="1d")
        if len(h) < 2: return None
        return {"last": float(h["Close"].iloc[-1]), "prev": float(h["Close"].iloc[-2])}
    except: return None


def get_holdings():
    out = []
    try:
        gh = pd.read_csv(DATA / "groww_holdings.csv")
        out += gh["symbol"].dropna().astype(str).str.upper().tolist()
    except: pass
    try:
        wl = pd.read_csv(DATA / "watchlist.csv")
        out += wl["symbol"].dropna().astype(str).str.upper().tolist()
    except: pass
    return list(set(out))


def send_tg(text):
    if not TG or not CHAT: return False
    url = f"https://api.telegram.org/bot{TG}/sendMessage"
    for p in [{"chat_id":CHAT,"text":text,"parse_mode":"Markdown"},{"chat_id":CHAT,"text":text}]:
        try:
            if requests.post(url,json=p,timeout=10).ok: return True
        except: pass
    return False


def main():
    rotations = []
    for name, ticker in SECTOR_INDICES.items():
        q = fetch_quote(ticker)
        if q:
            pct = (q["last"]/q["prev"]-1)*100
            rotations.append({"sector": name, "pct": round(pct,2), "last": q["last"]})
    if not rotations: print("[sector] no data"); return
    rotations.sort(key=lambda x: x["pct"], reverse=True)
    if not CLAUDE_KEY:
        msg = "*🔄 SECTOR ROTATION (today)*\n" + "\n".join(f"{r['sector']}: {r['pct']:+.2f}%" for r in rotations[:10])
        send_tg(msg); return
    system = """You are RDA Sector Strategist. Given today's Nifty sector index moves + the user's portfolio holdings, identify rotation themes and actionable trades.

Output (1500 chars Markdown):
*🔄 SECTOR ROTATION — DD MMM*
*Money flowing INTO:* top 2 sectors with %s
*Money flowing OUT OF:* bottom 2 sectors with %s
*Your portfolio exposure:* which of your holdings are in winning sectors / losing sectors
*Rotation play:* 1-2 specific BUY ideas (winning sector + your watchlist match) and 1-2 TRIM ideas (your losing sector holdings)
Be specific — cite stock names from the holdings list."""
    user = json.dumps({"sector_moves": rotations, "holdings": get_holdings()}, default=str)
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":CLAUDE_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":CLAUDE_MODEL,"max_tokens":1500,"system":system,
                  "messages":[{"role":"user","content":user}]}, timeout=60)
        if r.ok:
            text = "".join(b.get("text","") for b in r.json().get("content",[]) if b.get("type")=="text").strip()
            if text: send_tg(text); print(f"[sector] sent {len(text)} chars")
    except Exception as e:
        print(f"[sector] err: {e}")


if __name__=="__main__": main()
