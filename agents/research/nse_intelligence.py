"""NSE Intelligence Bundle — pulls 6 high-signal datasets daily:
1. Promoter pledge changes (weekly via NSE)
2. SEBI ASM (Additional Surveillance Measure) list
3. SEBI GSM (Graded Surveillance Measure) list
4. FII/DII daily flows
5. Insider trading SAST disclosures
6. Bulk + Block deals

Filters to user's watchlist + Groww holdings only.
Sends Claude-analyzed alerts to RDA Newswire bot.
Writes per-feed CSVs for dashboard consumption."""
from __future__ import annotations
import os, sys, json, time, hashlib
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import pandas as pd
from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
SEEN_FILE = DATA / ".nse_intel_seen.json"
IST = ZoneInfo("Asia/Kolkata")

TG_TOKEN = os.getenv("TELEGRAM_NEWS_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")
CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")

NSE_BASE = "https://www.nseindia.com"
NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}


def nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(NSE_HEADERS)
    try:
        s.get(NSE_BASE, timeout=10); time.sleep(1)
        s.get(f"{NSE_BASE}/market-data/live-equity-market", timeout=10); time.sleep(1)
    except Exception: pass
    return s


def load_universe() -> set:
    uni = set()
    for f in ["watchlist.csv", "groww_holdings.csv"]:
        try:
            df = pd.read_csv(DATA / f)
            for s in df["symbol"].dropna().astype(str): uni.add(s.upper())
        except Exception: pass
    return uni


def load_seen() -> set:
    if SEEN_FILE.exists():
        try: return set(json.loads(SEEN_FILE.read_text()).get("hashes", []))
        except: pass
    return set()


def save_seen(h: set) -> None:
    SEEN_FILE.write_text(json.dumps({"hashes": list(h)[-5000:], "updated": datetime.now(IST).isoformat()}))


def hash_event(*parts) -> str:
    return hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()


def send_tg(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT: return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for p in [{"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"},
              {"chat_id": TG_CHAT, "text": text}]:
        try:
            r = requests.post(url, json=p, timeout=10)
            if r.ok: return True
        except: pass
    return False


def claude_score(event_type: str, symbol: str, details: dict, position_info: dict | None) -> dict:
    if not CLAUDE_KEY: return {"impact":"?", "severity":5, "advice":"Claude not configured"}
    system = f"""You are RDA Stock Intelligence. The following event happened: {event_type}.
Decide:
- IMPACT (single word): POSITIVE / NEGATIVE / NEUTRAL
- SEVERITY (0-10): how material for the stock in next 1-30 days
- ADVICE (2 sentences): actionable for an Indian retail trader holding this stock

Output STRICT JSON only: {{"impact":"","severity":N,"advice":""}}"""
    user = f"Stock: {symbol}\nEvent details: {json.dumps(details, default=str)[:1500]}"
    if position_info: user += f"\nUser holds: {position_info}"
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": CLAUDE_MODEL, "max_tokens": 400,
                  "system": system, "messages":[{"role":"user","content":user}]}, timeout=45)
        if not r.ok: return {"impact":"?", "severity":5, "advice": f"API err {r.status_code}"}
        text = "".join(b.get("text","") for b in r.json().get("content",[]) if b.get("type")=="text").strip()
        if text.startswith("```"): text = "\n".join(text.split("\n")[1:-1])
        return json.loads(text)
    except Exception as e:
        return {"impact":"?", "severity":5, "advice": str(e)[:200]}


def fmt_alert(event_type: str, symbol: str, body: str, analysis: dict) -> str:
    emoji = {"POSITIVE":"🟢", "NEGATIVE":"🔴", "NEUTRAL":"⚪"}.get(analysis.get("impact","?"), "❔")
    sev = analysis.get("severity", 5)
    bar = "█" * int(sev) + "░" * (10 - int(sev))
    return (f"{emoji} *{event_type}* — *{symbol}*\n"
            f"{body}\n\n"
            f"*Impact:* {analysis.get('impact','?')} ({sev}/10) `{bar}`\n"
            f"*Advice:* {analysis.get('advice','-')[:400]}\n\n"
            f"_NSE intel · {datetime.now(IST).strftime('%H:%M IST')}_")


# === FEED 1: Promoter Pledge ===
def fetch_pledge(s: requests.Session, symbol: str) -> list:
    try:
        r = s.get(f"{NSE_BASE}/api/corporate-shareholding-pledged-data?index=equities&symbol={symbol}", timeout=15)
        if not r.ok: return []
        d = r.json()
        items = d.get("data", d) if isinstance(d, dict) else d
        return items[:5] if isinstance(items, list) else []
    except Exception: return []


# === FEED 2 + 3: ASM + GSM ===
def fetch_asm_gsm(s: requests.Session) -> dict:
    out = {"ASM": [], "GSM": []}
    try:
        r = s.get(f"{NSE_BASE}/api/reportASM", timeout=15)
        if r.ok:
            d = r.json()
            out["ASM"] = [x.get("symbol","") for x in d.get("data", []) if isinstance(x, dict)]
    except Exception: pass
    try:
        r = s.get(f"{NSE_BASE}/api/reportGSM", timeout=15)
        if r.ok:
            d = r.json()
            out["GSM"] = [x.get("symbol","") for x in d.get("data", []) if isinstance(x, dict)]
    except Exception: pass
    return out


# === FEED 4: FII/DII flows ===
def fetch_fii_dii(s: requests.Session) -> list:
    try:
        r = s.get(f"{NSE_BASE}/api/fiidiiTradeReact", timeout=15)
        if r.ok: return r.json() if isinstance(r.json(), list) else []
    except Exception: pass
    return []


# === FEED 5: Insider trading SAST ===
def fetch_insider(s: requests.Session) -> list:
    try:
        r = s.get(f"{NSE_BASE}/api/corporates-pit?index=equities", timeout=15)
        if r.ok:
            d = r.json()
            return d.get("data", d) if isinstance(d, dict) else d
    except Exception: pass
    return []


# === FEED 6: Bulk + Block deals ===
def fetch_bulk_block(s: requests.Session) -> dict:
    out = {"bulk": [], "block": []}
    for kind, key in [("bulk-deals", "bulk"), ("block-deal", "block")]:
        try:
            r = s.get(f"{NSE_BASE}/api/{kind}", timeout=15)
            if r.ok:
                d = r.json()
                items = d.get("data", d.get("BULK_DEALS_DATA", d)) if isinstance(d, dict) else d
                out[key] = items[:30] if isinstance(items, list) else []
        except Exception: pass
    return out


def get_position_info() -> dict:
    out = {}
    try:
        gh = pd.read_csv(DATA / "groww_holdings.csv")
        for _, r in gh.iterrows():
            out[str(r["symbol"]).upper()] = {
                "qty": int(r.get("qty",0)), "avg_price": float(r.get("avg_price",0)),
                "pnl_pct": float(r.get("pnl_pct",0))
            }
    except Exception: pass
    return out


def main():
    print(f"[{datetime.now(IST).isoformat()}] nse_intelligence start")
    universe = load_universe()
    if not universe: print("[intel] empty universe"); return
    print(f"[intel] monitoring {len(universe)} symbols")
    s = nse_session()
    seen = load_seen()
    positions = get_position_info()
    new_alerts = 0
    rows_out = {"pledge": [], "asm_gsm": [], "fii_dii": [], "insider": [], "bulk_block": []}

    # --- 1. Pledge changes (per symbol — query held + watchlist) ---
    for sym in list(universe)[:25]:  # cap to avoid NSE rate limits
        items = fetch_pledge(s, sym)
        for it in items[:2]:
            qty = it.get("pledgedShare", it.get("totalPledgedShare", ""))
            pct = it.get("percentPledgedShare", it.get("pledgedSharePer", ""))
            date = it.get("date", "")
            if not qty or not date: continue
            h = hash_event("pledge", sym, qty, date)
            if h in seen: continue
            seen.add(h)
            body = f"Pledged qty: {qty} ({pct}%)\nDate: {date}"
            analysis = claude_score("Promoter Pledge Update", sym, it, positions.get(sym))
            if analysis.get("severity", 0) >= 4:
                if send_tg(fmt_alert("PROMOTER PLEDGE", sym, body, analysis)):
                    new_alerts += 1
            rows_out["pledge"].append({"symbol":sym, "qty":qty, "pct":pct, "date":date,
                                       "severity":analysis.get("severity",0)})
        time.sleep(1)

    # --- 2+3. ASM / GSM list ---
    asm_gsm = fetch_asm_gsm(s)
    asm_hits = universe & set(asm_gsm.get("ASM", []))
    gsm_hits = universe & set(asm_gsm.get("GSM", []))
    for sym in asm_hits:
        h = hash_event("asm", sym, datetime.now(IST).strftime("%Y-%m-%d"))
        if h in seen: continue
        seen.add(h)
        analysis = claude_score("SEBI ASM (Additional Surveillance)", sym,
                                 {"event":"Entered ASM list — trading restrictions apply"}, positions.get(sym))
        analysis["severity"] = max(analysis.get("severity",0), 7)  # always material
        if send_tg(fmt_alert("⚠️ SEBI ASM", sym, "Stock entered Additional Surveillance Measure. Margins increased, intraday restricted.", analysis)):
            new_alerts += 1
        rows_out["asm_gsm"].append({"symbol":sym, "list":"ASM", "date":datetime.now(IST).isoformat()})
    for sym in gsm_hits:
        h = hash_event("gsm", sym, datetime.now(IST).strftime("%Y-%m-%d"))
        if h in seen: continue
        seen.add(h)
        analysis = claude_score("SEBI GSM (Graded Surveillance)", sym,
                                 {"event":"Entered GSM list — strict price band limits"}, positions.get(sym))
        analysis["severity"] = max(analysis.get("severity",0), 8)  # very material
        if send_tg(fmt_alert("🚨 SEBI GSM", sym, "Stock entered Graded Surveillance. Severe price band restrictions.", analysis)):
            new_alerts += 1
        rows_out["asm_gsm"].append({"symbol":sym, "list":"GSM", "date":datetime.now(IST).isoformat()})

    # --- 4. FII/DII daily flow ---
    flows = fetch_fii_dii(s)
    if flows:
        latest = flows[0] if isinstance(flows, list) else flows
        flow_date = str(latest.get("date", datetime.now(IST).strftime("%Y-%m-%d")))
        h = hash_event("fii_dii", flow_date)
        if h not in seen:
            seen.add(h)
            fii_net = 0; dii_net = 0
            try:
                for r in flows[:2]:
                    if "FII" in str(r.get("category","")).upper(): fii_net = float(r.get("netValue", 0))
                    if "DII" in str(r.get("category","")).upper(): dii_net = float(r.get("netValue", 0))
            except Exception: pass
            body = f"FII net: ₹{fii_net:,.0f}cr | DII net: ₹{dii_net:,.0f}cr"
            analysis = claude_score("Daily FII/DII Flows", "INDICES",
                                     {"FII_net_cr": fii_net, "DII_net_cr": dii_net,
                                      "holdings": list(positions.keys())},
                                     None)
            if analysis.get("severity", 0) >= 5:
                if send_tg(fmt_alert("FII/DII FLOWS", "INDICES", body, analysis)):
                    new_alerts += 1
            rows_out["fii_dii"].append({"date":flow_date, "fii":fii_net, "dii":dii_net})

    # --- 5. Insider trading SAST ---
    insider = fetch_insider(s)
    for it in insider[:50] if isinstance(insider, list) else []:
        sym = str(it.get("symbol", it.get("SYMBOL",""))).upper()
        if sym not in universe: continue
        qty = it.get("secAcq", it.get("qty",""))
        action = it.get("tdpTransactionType", it.get("acqMode",""))
        date = it.get("date", it.get("acquisitionDate",""))
        h = hash_event("insider", sym, qty, action, date)
        if h in seen: continue
        seen.add(h)
        body = f"Action: {action}\nQty: {qty}\nDate: {date}"
        analysis = claude_score("Insider Trading (SAST)", sym, it, positions.get(sym))
        if analysis.get("severity", 0) >= 5:
            if send_tg(fmt_alert("👁 INSIDER TRADE", sym, body, analysis)):
                new_alerts += 1
        rows_out["insider"].append({"symbol":sym, "action":action, "qty":qty, "date":date})

    # --- 6. Bulk + Block deals ---
    bb = fetch_bulk_block(s)
    for kind, items in bb.items():
        for it in items if isinstance(items, list) else []:
            sym = str(it.get("symbol", it.get("BD_SYMBOL", it.get("SYMBOL","")))).upper()
            if sym not in universe: continue
            client = it.get("clientName", it.get("BD_CLIENT_NAME", ""))
            qty = it.get("quantityTraded", it.get("BD_QTY_TRD", ""))
            price = it.get("watp", it.get("BD_TP_WATP", ""))
            buysell = it.get("buySell", it.get("BD_BUY_SELL", ""))
            date = it.get("date", it.get("BD_DT_MATCH", ""))
            h = hash_event(kind, sym, client, qty, date)
            if h in seen: continue
            seen.add(h)
            body = f"{kind.upper()} DEAL · {buysell}\nClient: {client[:60]}\nQty: {qty} @ ₹{price}\nDate: {date}"
            analysis = claude_score(f"{kind.title()} Deal", sym, it, positions.get(sym))
            if analysis.get("severity", 0) >= 5:
                if send_tg(fmt_alert(f"📦 {kind.upper()} DEAL", sym, body, analysis)):
                    new_alerts += 1
            rows_out["bulk_block"].append({"symbol":sym, "kind":kind, "client":client[:80],
                                            "qty":qty, "price":price, "side":buysell, "date":date})

    # Write CSV outputs
    DATA.mkdir(parents=True, exist_ok=True)
    for key, rows in rows_out.items():
        if rows:
            f = DATA / f"nse_intel_{key}.csv"
            df_new = pd.DataFrame(rows)
            if f.exists():
                try:
                    df_old = pd.read_csv(f)
                    df_new = pd.concat([df_old, df_new], ignore_index=True).tail(1000)
                except Exception: pass
            df_new.to_csv(f, index=False)

    save_seen(seen)
    print(f"[intel] {new_alerts} alerts sent, hashes seen {len(seen)}")


if __name__ == "__main__":
    main()
