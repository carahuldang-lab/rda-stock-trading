"""Auto-Rebalance Advisor — daily EOD scan of portfolio sector concentration.
If any sector > 35% of deployed capital, suggest TRIM via Claude."""
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

CONCENTRATION_LIMIT = 0.35


def send_tg(text):
    if not TG or not CHAT: return False
    url = f"https://api.telegram.org/bot{TG}/sendMessage"
    for p in [{"chat_id":CHAT,"text":text,"parse_mode":"Markdown"},{"chat_id":CHAT,"text":text}]:
        try:
            if requests.post(url,json=p,timeout=10).ok: return True
        except: pass
    return False


def main():
    try:
        pos = pd.read_csv(DATA / "positions.csv")
        pos["quantity"] = pd.to_numeric(pos["quantity"], errors="coerce").fillna(0)
        pos["entry_price"] = pd.to_numeric(pos["entry_price"], errors="coerce").fillna(0)
        pos["value"] = pos["quantity"] * pos["entry_price"]
    except Exception as e:
        print(f"[rebal] no positions: {e}"); return
    if pos.empty: return
    total = pos["value"].sum()
    if total == 0: return
    if "sector" not in pos.columns or pos["sector"].fillna("").eq("").all():
        print("[rebal] no sector info"); return
    sectors = pos.groupby("sector").agg(value=("value","sum"), syms=("symbol", list)).reset_index()
    sectors["pct"] = sectors["value"] / total
    over = sectors[sectors["pct"] > CONCENTRATION_LIMIT]
    if over.empty:
        print(f"[rebal] no over-concentration (limit {CONCENTRATION_LIMIT*100:.0f}%)"); return
    if not CLAUDE_KEY: return
    system = """You are RDA Risk Rebalancer. The user's portfolio has over-concentration in one or more sectors.
Output (Markdown, <1500 chars):
*⚖️ REBALANCE ALERT*
*Over-concentrated sectors:* list with % and stocks
*Risk:* what specific systemic risk this creates (e.g., metals crash, IT slowdown, NPA cycle)
*Action:* which 1-2 specific stocks to TRIM and by how much, citing weakest fundamentals in the overweight sector
*Where to redeploy:* 1 underweight sector you should add to (from the data)
Be specific."""
    payload = {"total_deployed": float(total),
               "over_concentrated": over[["sector","pct","syms","value"]].to_dict("records"),
               "all_sectors": sectors[["sector","pct"]].to_dict("records")}
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key":CLAUDE_KEY,"anthropic-version":"2023-06-01","content-type":"application/json"},
            json={"model":CLAUDE_MODEL,"max_tokens":1200,"system":system,
                  "messages":[{"role":"user","content":json.dumps(payload, default=str)}]}, timeout=60)
        if r.ok:
            text = "".join(b.get("text","") for b in r.json().get("content",[]) if b.get("type")=="text").strip()
            if text: send_tg(text); print(f"[rebal] sent {len(text)} chars")
    except Exception as e:
        print(f"[rebal] err: {e}")


if __name__=="__main__": main()
