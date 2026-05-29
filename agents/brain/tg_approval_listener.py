"""Telegram Approval Listener — polls @rdan8nbot for callback_query taps.
When user taps ✅ Approve / ❌ Skip on a brain decision message, this
updates data/brain_decisions.csv approved status and confirms via reply."""
from __future__ import annotations
import os, time, json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import pandas as pd
from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
STATE = DATA / ".tg_listener_offset.json"
DECISIONS = DATA / "brain_decisions.csv"
IST = ZoneInfo("Asia/Kolkata")

TG = os.getenv("TELEGRAM_BOT_TOKEN","")  # Trade bot (not newswire)
CHAT = os.getenv("TELEGRAM_CHAT_ID","")
POLL_SEC = 5


def load_offset():
    if STATE.exists():
        try: return json.loads(STATE.read_text()).get("offset", 0)
        except: pass
    return 0


def save_offset(o): STATE.write_text(json.dumps({"offset": o}))


def update_decision(symbol, status):
    if not DECISIONS.exists(): return False
    try:
        df = pd.read_csv(DECISIONS)
        # Update most recent pending decision for this symbol
        mask = (df["symbol"].astype(str).str.upper() == symbol.upper()) & (df["approved"] == "pending")
        if mask.any():
            idx = df[mask].index[-1]
            df.loc[idx, "approved"] = status
            df.loc[idx, "approved_at"] = datetime.now(IST).isoformat()
            df.to_csv(DECISIONS, index=False)
            return True
    except Exception as e:
        print(f"[listener] update err: {e}")
    return False


def answer_callback(cb_id, text=""):
    try:
        requests.post(f"https://api.telegram.org/bot{TG}/answerCallbackQuery",
                      json={"callback_query_id": cb_id, "text": text}, timeout=5)
    except: pass


def send_msg(text):
    try:
        requests.post(f"https://api.telegram.org/bot{TG}/sendMessage",
                      json={"chat_id": CHAT, "text": text, "parse_mode": "Markdown"}, timeout=10)
    except: pass


def handle_callback(cq):
    data = cq.get("data", "")  # format: "approve:HFCL" or "skip:HFCL"
    if not data or ":" not in data: return
    action, sym = data.split(":", 1)
    cb_id = cq.get("id", "")
    user = cq.get("from", {}).get("first_name", "User")
    if action == "approve":
        if update_decision(sym, "YES"):
            answer_callback(cb_id, f"✅ {sym} approved")
            send_msg(f"✅ *{sym}* approved for execution by {user} at {datetime.now(IST).strftime('%H:%M IST')}")
        else:
            answer_callback(cb_id, f"No pending {sym} decision found")
    elif action == "skip":
        if update_decision(sym, "NO"):
            answer_callback(cb_id, f"❌ {sym} skipped")
            send_msg(f"❌ *{sym}* skipped by {user}")
        else:
            answer_callback(cb_id, f"No pending {sym} decision found")
    elif action == "details":
        try:
            df = pd.read_csv(DECISIONS)
            row = df[df["symbol"].astype(str).str.upper() == sym.upper()].iloc[-1]
            text = f"*{sym} Decision Detail*\nAction: {row['action']} | Conf: {row.get('confidence',0)}%\nEntry: ₹{row.get('entry','')} | SL: ₹{row.get('stop','')} | Tgt: ₹{row.get('target','')}\nReason: {str(row.get('reasoning',''))[:300]}"
            answer_callback(cb_id, "Sent details")
            send_msg(text)
        except Exception as e:
            answer_callback(cb_id, "No details found")


def main():
    if not TG or not CHAT:
        print("[listener] no creds; exiting"); return
    print(f"[{datetime.now(IST).isoformat()}] Telegram listener started")
    offset = load_offset()
    while True:
        try:
            r = requests.get(f"https://api.telegram.org/bot{TG}/getUpdates",
                             params={"offset": offset, "timeout": 30,
                                     "allowed_updates": json.dumps(["callback_query","message"])},
                             timeout=40)
            if r.ok:
                for up in r.json().get("result", []):
                    offset = up["update_id"] + 1
                    if "callback_query" in up:
                        handle_callback(up["callback_query"])
                save_offset(offset)
        except Exception as e:
            print(f"[listener] err: {e}")
            time.sleep(POLL_SEC)
        time.sleep(POLL_SEC)


if __name__ == "__main__": main()
