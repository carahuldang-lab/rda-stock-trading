"""Loss-Cut Advisor — checks held positions every 10 min during market.
If any drops >7% from entry, urgent Claude advisory: TRIM/EXIT/HOLD with levels."""
from __future__ import annotations
import os, json, hashlib
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests, pandas as pd
from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
STATE = DATA / ".loss_cut_seen.json"
IST = ZoneInfo("Asia/Kolkata")
TG = os.getenv("TELEGRAM_NEWS_BOT_TOKEN","")
CHAT = os.getenv("TELEGRAM_CHAT_ID","")
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY","")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL","claude-sonnet-4-5-20250929")

THRESH_PCT = -7.0  # alert when pnl_pct < -7%


def load_seen():
    if STATE.exists():
        try: return json.loads(STATE.read_text())
        except: pass
    return {}


def save_seen(d): STATE.write_text(json.dumps(d))


def get_drawdown_holdings():
    out = []
    try:
        gh = pd.read_csv(DATA / "groww_holdings.csv")
        for _, r in gh.iterrows():
            pnl = float(r.get("pnl_pct", 0))
            if pnl < THRESH_PCT:
                out.append({"symbol": str(r["symbol"]).upper(), "qty": int(r["qty"]),
                             "avg": float(r["avg_price"]), "ltp": float(r["ltp"]),
                             "pnl_pct": pnl, "invested": float(r["invested"]),
                             "source": "groww"})
    except: pass
    return out


def claude_loss_advice(stock):
    if not CLAUDE_KEY: return None
    system = """You are RDA Risk Manager. A user's position is down >7%. Give urgent action: TRIM 50% / EXIT / HOLD-AVERAGE-DOWN.

Use web_search to check WHY it's falling (news, results, sector). Then decide.

Output (1500 chars Markdown):
*🚨 LOSS-CUT ADVISORY — SYM (-X%)*
*Why falling:* 1-2 lines based on news/web search
*Verdict:* TRIM / EXIT / HOLD (with specific levels)
*Stop loss:* exact price below which exit no matter what
*Risk:* what could make this worse
Be DECISIVE — no hedging. The user needs action."""
    user = json.dumps(stock, default=str)
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":CLAUDE_KEY,"anthropic-version":"2023-06-01",
                     "content-type":"application/json","anthropic-beta":"web-search-2025-03-05"},
            json={"model":CLAUDE_MODEL,"max_tokens":1500,"system":system,
                  "tools":[{"type":"web_search_20250305","name":"web_search","max_uses":3}],
                  "messages":[{"role":"user","content":user}]}, timeout=90)
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
    losers = get_drawdown_holdings()
    if not losers: print("[loss_cut] no holdings below threshold"); return
    seen = load_seen()
    today = datetime.now(IST).strftime("%Y-%m-%d")
    sent = 0
    for stock in losers:
        sym = stock["symbol"]
        # Dedup: only alert once per stock per day per 2% deeper drop
        bucket = int(stock["pnl_pct"] / 2) * 2  # group -7/-9/-11 etc
        key = f"{sym}|{today}|{bucket}"
        if seen.get(key): continue
        seen[key] = datetime.now(IST).isoformat()
        advice = claude_loss_advice(stock)
        if advice and send_tg(advice):
            sent += 1
    save_seen(seen)
    print(f"[loss_cut] {sent} advisories sent")


if __name__=="__main__": main()
