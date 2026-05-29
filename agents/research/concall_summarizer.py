"""Concall Transcript Summarizer — detects new earnings call transcripts
in NSE corporate announcements for watchlist + holdings.
Claude summarizes management tone, guidance, red flags. Sent to Newswire bot."""
from __future__ import annotations
import os, json, hashlib, time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests, pandas as pd
from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
SEEN = DATA / ".concall_seen.json"
IST = ZoneInfo("Asia/Kolkata")
TG = os.getenv("TELEGRAM_NEWS_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")

KEYWORDS = ["earnings call", "conference call", "concall", "investor presentation",
            "earnings webcast", "transcript", "investor meet", "q1 result", "q2 result",
            "q3 result", "q4 result", "quarterly result"]


def load_seen():
    if SEEN.exists():
        try: return set(json.loads(SEEN.read_text()).get("h", []))
        except: pass
    return set()


def save_seen(s):
    SEEN.write_text(json.dumps({"h": list(s)[-2000:]}))


def nse_session():
    s = requests.Session()
    s.headers.update({"User-Agent":"Mozilla/5.0 Chrome/120", "Accept":"application/json",
                      "Referer":"https://www.nseindia.com/"})
    try:
        s.get("https://www.nseindia.com", timeout=10); time.sleep(1)
        s.get("https://www.nseindia.com/market-data/live-equity-market", timeout=10); time.sleep(1)
    except: pass
    return s


def load_universe():
    uni = set()
    for f in ["watchlist.csv", "groww_holdings.csv"]:
        try:
            df = pd.read_csv(DATA / f)
            uni.update(df["symbol"].dropna().astype(str).str.upper())
        except: pass
    return uni


def fetch_announcements(s, sym):
    try:
        r = s.get(f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={sym}", timeout=15)
        if not r.ok: return []
        d = r.json()
        return d if isinstance(d, list) else d.get("data", [])
    except: return []


def is_concall(item):
    text = (str(item.get("desc",""))+" "+str(item.get("subject",""))+" "+str(item.get("attchmntText",""))).lower()
    return any(k in text for k in KEYWORDS)


def claude_summarize(symbol, announcement):
    if not CLAUDE_KEY: return None
    system = """You are RDA Concall Analyst. Read the earnings/concall announcement and give the user a SHARP 1-Telegram-message summary (Markdown, <1500 chars).

Structure:
1. *Headline:* one-line takeaway (beat/miss/inline)
2. *Numbers:* revenue %, EBITDA margin, EPS (if mentioned)
3. *Management tone:* CAUTIOUS / OPTIMISTIC / GUIDANCE-CUT — cite key phrases
4. *Key guidance:* what they said about next quarter
5. *Red flags:* anything to worry about (debt, margins, demand, attrition, regulatory)
6. *Action for retail:* BUY / HOLD / TRIM / EXIT + why

Be decisive. If announcement is just a notification (not detailed), say so and flag to revisit when transcript is out."""
    user = f"Stock: {symbol}\nAnnouncement:\n{json.dumps(announcement, default=str)[:3000]}"
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version":"2023-06-01", "content-type":"application/json"},
            json={"model": CLAUDE_MODEL, "max_tokens": 1500, "system":system,
                  "messages":[{"role":"user","content":user}]}, timeout=60)
        if r.ok:
            return "".join(b.get("text","") for b in r.json().get("content",[]) if b.get("type")=="text").strip()
    except: pass
    return None


def send_tg(text):
    if not TG or not CHAT: return False
    url = f"https://api.telegram.org/bot{TG}/sendMessage"
    for p in [{"chat_id":CHAT,"text":text,"parse_mode":"Markdown"}, {"chat_id":CHAT,"text":text}]:
        try:
            r = requests.post(url, json=p, timeout=10)
            if r.ok: return True
        except: pass
    return False


MAX_ALERTS_PER_RUN = 5  # cap to avoid floods
MAX_AGE_DAYS = 3        # only alert on announcements from last 3 days


def parse_ann_date(s):
    if not s: return None
    for fmt in ("%d-%b-%Y %H:%M:%S","%d-%b-%Y","%Y-%m-%d","%Y-%m-%dT%H:%M:%S"):
        try: return datetime.strptime(str(s).strip()[:len(fmt)+3], fmt)
        except: pass
    return None


def main():
    print(f"[{datetime.now(IST).isoformat()}] concall scanner start")
    uni = load_universe()
    s = nse_session()
    seen = load_seen()
    sent = 0
    first_run = len(seen) == 0
    cutoff = datetime.now() - timedelta(days=MAX_AGE_DAYS)
    for sym in uni:
        if sent >= MAX_ALERTS_PER_RUN: break
        for it in fetch_announcements(s, sym)[:10]:
            if not is_concall(it): continue
            h = hashlib.md5(f"{sym}|{it.get('desc','')}|{it.get('an_dt','')}".encode()).hexdigest()
            if h in seen: continue
            seen.add(h)
            # Skip if older than MAX_AGE_DAYS — prevents first-run flood
            ann_dt = parse_ann_date(it.get('an_dt','') or it.get('date',''))
            if ann_dt and ann_dt < cutoff:
                continue
            # On first run, only alert on top-3 freshest, mark rest as seen silently
            if first_run and sent >= 3:
                continue
            summary = claude_summarize(sym, it)
            if summary:
                msg = f"📋 *CONCALL / RESULT* — *{sym}*\n\n{summary}\n\n_NSE corp ann · {datetime.now(IST).strftime('%H:%M IST')}_"
                if send_tg(msg): sent += 1
                if sent >= MAX_ALERTS_PER_RUN: break
        time.sleep(0.5)
    save_seen(seen)
    print(f"[concall] {sent} alerts sent, seen {len(seen)} (first_run={first_run})")


if __name__ == "__main__": main()
