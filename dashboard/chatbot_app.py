"""RDA Stock Advisor v2 — SME-grade chatbot with:
- Claude native web_search tool (catches latest corporate actions, brokerage notes)
- NSE corporate filings scraper (official announcements: results, demergers, dividends, board meetings)
- Fresh yfinance news
- Tavily web search (gated by TAVILY_API_KEY)
- Symbol resolver (company name → NSE ticker)
- Mobile-responsive UI"""
from __future__ import annotations
import os, json, sys, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
import pandas as pd
from flask import Flask, jsonify, request, render_template_string
from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).parent.parent
DATA = ROOT / "data"
IST = ZoneInfo("Asia/Kolkata")
sys.path.insert(0, str(ROOT))

CLAUDE_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-5-20250929")
TAVILY_KEY = os.getenv("TAVILY_API_KEY", "")

app = Flask(__name__)

NSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
}

_nse_session = None
def get_nse_session() -> requests.Session:
    global _nse_session
    if _nse_session is None:
        s = requests.Session()
        s.headers.update(NSE_HEADERS)
        try:
            s.get("https://www.nseindia.com", timeout=10); time.sleep(1)
            s.get("https://www.nseindia.com/market-data/live-equity-market", timeout=10); time.sleep(1)
        except Exception: pass
        _nse_session = s
    return _nse_session


def resolve_symbol(query: str) -> str:
    if not query: return ""
    q = query.upper().strip()
    aliases = {"VEDANTA":"VEDL","INFOSYS":"INFY","HDFC BANK":"HDFCBANK","ICICI":"ICICIBANK",
               "BHARTI AIRTEL":"BHARTIARTL","VODAFONE IDEA":"IDEA","VODAFONE":"IDEA","SBI":"SBIN",
               "STATE BANK":"SBIN","BAJAJ FINANCE":"BAJFINANCE","HUL":"HINDUNILVR",
               "MARUTI":"MARUTI","TATA MOTORS":"TATAMOTORS","RELIANCE":"RELIANCE","TCS":"TCS"}
    if q in aliases: return aliases[q]
    try:
        df = pd.read_csv(DATA / "nifty500.csv")
        if q in df["symbol"].astype(str).str.upper().values: return q
        col = "company_name" if "company_name" in df.columns else df.columns[1]
        m = df[df[col].astype(str).str.upper().str.contains(q, na=False, regex=False)]
        if not m.empty: return str(m.iloc[0]["symbol"]).upper()
    except Exception: pass
    return q


def fetch_nse_filings(symbol: str, limit: int = 10) -> list:
    """Pull recent NSE corporate filings/announcements for a symbol."""
    try:
        s = get_nse_session()
        # Corporate announcements
        r = s.get(f"https://www.nseindia.com/api/corporate-announcements?index=equities&symbol={symbol}", timeout=15)
        if not r.ok: return []
        data = r.json()
        items = data if isinstance(data, list) else data.get("rows", data.get("data", []))
        out = []
        for it in items[:limit]:
            out.append({
                "subject": it.get("desc", it.get("subject", ""))[:200],
                "details": it.get("attchmntText", it.get("attchmntFile", it.get("details", "")))[:300],
                "date": it.get("an_dt", it.get("date", "")),
                "type": it.get("desc", "")[:50]
            })
        return out
    except Exception as e:
        print(f"[nse_filings] {symbol}: {e}")
        return []


def fetch_yf_news(symbol: str, limit: int = 5) -> list:
    try:
        import yfinance as yf
        t = yf.Ticker(f"{symbol}.NS")
        if not hasattr(t, "news") or not t.news: return []
        out = []
        for it in t.news[:limit]:
            c = it.get("content", it) if isinstance(it, dict) else {}
            out.append({
                "title": c.get("title", it.get("title", ""))[:200],
                "summary": str(c.get("summary", ""))[:300],
                "publisher": (c.get("provider") or {}).get("displayName", it.get("publisher", "")) if isinstance(c.get("provider"), dict) else it.get("publisher", "")
            })
        return out
    except Exception: return []


def tavily_search(query: str, max_results: int = 5) -> list:
    if not TAVILY_KEY: return []
    try:
        from tavily import TavilyClient
        c = TavilyClient(api_key=TAVILY_KEY)
        r = c.search(query=query, search_depth="advanced", max_results=max_results, include_domains=["moneycontrol.com","economictimes.indiatimes.com","livemint.com","business-standard.com","bseindia.com","nseindia.com","investing.com","reuters.com"])
        return [{"title": x.get("title",""), "url": x.get("url",""), "content": x.get("content","")[:500]} for x in r.get("results",[])]
    except Exception as e:
        print(f"[tavily] err: {e}"); return []



BRAVE_KEY = os.getenv("BRAVE_API_KEY", "")

def brave_search(query: str, count: int = 5) -> list:
    """Free 2000 queries/mo. Better Indian context than Google sometimes."""
    if not BRAVE_KEY: return []
    try:
        r = requests.get("https://api.search.brave.com/res/v1/web/search",
                         headers={"Accept":"application/json", "X-Subscription-Token": BRAVE_KEY},
                         params={"q": query, "count": count, "country": "IN"}, timeout=15)
        if not r.ok: return []
        d = r.json()
        return [{"title": x.get("title",""), "url": x.get("url",""),
                 "description": x.get("description","")[:400]}
                for x in d.get("web", {}).get("results", [])]
    except Exception as e:
        print(f"[brave] err: {e}"); return []


def gather_context(symbol: str) -> dict:
    sym = symbol.upper()
    ctx = {"symbol": sym, "timestamp": datetime.now(IST).isoformat()}
    for f, key in [("news.csv","news"),("fundamentals.csv","fundamentals"),
                   ("analyst_reports.csv","analyst"),("groww_holdings.csv","groww"),
                   ("positions.csv","positions"),("market_regime.csv","regime"),
                   ("sector_strength.csv","sectors"),("swing_candidates.csv","swing_score")]:
        p = DATA / f
        if not p.exists(): continue
        try:
            df = pd.read_csv(p)
            if "symbol" in df.columns:
                sub = df[df["symbol"].astype(str).str.upper() == sym]
                if not sub.empty: ctx[key] = sub.tail(3).to_dict("records")
            elif f == "market_regime.csv":
                ctx[key] = df.iloc[-1].to_dict()
        except Exception: pass
    ctx["fresh_news"] = fetch_yf_news(sym, 5)
    ctx["nse_filings"] = fetch_nse_filings(sym, 10)
    if BRAVE_KEY: ctx["brave_search"] = brave_search(f"{sym} stock news corporate action india latest")
    if TAVILY_KEY:
        ctx["web_research"] = tavily_search(f"{sym} stock latest news corporate action 2026")
    return ctx


SYSTEM_PROMPT = """You are RDA Stock Advisor — a 50-year SME-level Indian equity analyst.

CRITICAL DATA HIERARCHY:
1. CSV data + NSE filings + yfinance news = ground truth for recent facts
2. Your training knowledge = corporate actions (mergers/demergers/splits/bonus), sector dynamics, management quality, regulatory context, longer-term thesis
3. Use the web_search tool AGGRESSIVELY when:
   - User asks about a stock's recent move ("why is X falling?")
   - Corporate action context is needed (Vedanta demerger, Adani group restructuring, IT sector AI demand)
   - Latest brokerage targets / SEBI filings / promoter pledge changes
   - Anything time-sensitive (last 30 days)

ANSWERING STYLE:
- Lead with the BIGGEST factor first. If a demerger/SEBI action/result beat is the real story, say so before technicals.
- Cite specifics: RSI %, P/E, distance from 52w high, analyst target vs current.
- Give entry / SL / target / hold period. State confidence (0-100%).
- Compare your view vs analyst consensus (two-way check).
- Use 🟢 BUY / 🔴 SELL / 🟡 TRIM / ⚪ HOLD emojis.
- Don't be over-conservative. The user wants decisive analysis, not hedged fluff.
- Keep under 2000 chars (mobile readability). Markdown OK."""


def ask_claude(question: str, symbol: str | None) -> str:
    if not CLAUDE_KEY: return "Claude API key not configured."
    ctx = gather_context(symbol) if symbol else {"note": "no specific symbol — answer broadly"}
    user_msg = f"Question: {question}\n\nLocal data for {symbol or 'portfolio'}:\n{json.dumps(ctx, default=str, indent=2)[:18000]}\n\nUse web_search tool to verify recent events / corporate actions before final answer."
    try:
        # Use Claude's native web_search tool
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json", "anthropic-beta": "web-search-2025-03-05"},
            json={
                "model": CLAUDE_MODEL, "max_tokens": 2000, "system": SYSTEM_PROMPT,
                "tools": [{"type": "web_search_20250305", "name": "web_search", "max_uses": 4}],
                "messages": [{"role":"user","content": user_msg}]
            }, timeout=120)
        if not r.ok: return f"Claude error {r.status_code}: {r.text[:400]}"
        body = r.json()
        # Stitch text blocks together (skip tool_use/tool_result internal blocks)
        return "".join(b.get("text","") for b in body.get("content",[]) if b.get("type")=="text").strip() or "No answer returned."
    except Exception as e:
        return f"Exception: {e}"


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True)
    question = (data.get("question") or "").strip()
    raw_sym = (data.get("symbol") or "").strip().upper()
    symbol = resolve_symbol(raw_sym) if raw_sym else None
    if not question: return jsonify({"error":"empty question"}), 400
    answer = ask_claude(question, symbol)
    return jsonify({"question": question, "symbol": symbol, "raw_symbol": raw_sym,
                    "answer": answer, "ts": datetime.now(IST).strftime("%H:%M IST")})


HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>RDA Advisor v2</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif}
body{background:#0f1419;color:#e8e6e3;min-height:100vh;padding:0}
.wrap{max-width:760px;margin:0 auto;padding:16px;display:flex;flex-direction:column;height:100vh}
h1{font-size:18px;color:#88d3ce;margin-bottom:4px}
.sub{font-size:11px;color:#6b7280;margin-bottom:12px}
.features{font-size:10px;color:#4ade80;margin-bottom:12px;display:flex;gap:8px;flex-wrap:wrap}
.features span{background:#1a1f2e;padding:3px 8px;border-radius:10px}
.controls{display:flex;gap:8px;margin-bottom:12px}
#sym{flex:0 0 130px;background:#1a1f2e;border:1px solid #2a3142;color:#e8e6e3;padding:10px;border-radius:8px;font-size:14px;text-transform:uppercase}
#q{flex:1;background:#1a1f2e;border:1px solid #2a3142;color:#e8e6e3;padding:10px;border-radius:8px;font-size:14px}
#ask{background:#88d3ce;color:#0f1419;border:0;padding:10px 18px;border-radius:8px;font-weight:600;cursor:pointer}
#ask:disabled{opacity:0.5;cursor:wait}
#feed{flex:1;overflow-y:auto}
.msg{background:#1a1f2e;border-left:3px solid #88d3ce;padding:12px;border-radius:6px;margin-bottom:12px;font-size:13px;line-height:1.55;white-space:pre-wrap}
.msg .head{color:#88d3ce;font-weight:600;margin-bottom:6px;font-size:12px}
.msg.user{border-left-color:#fbbf24}
.msg.user .head{color:#fbbf24}
.loading{color:#6b7280;font-style:italic}
.suggestions{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.suggestions span{background:#1a1f2e;border:1px solid #2a3142;padding:5px 10px;border-radius:12px;font-size:11px;cursor:pointer;color:#88d3ce}
@media(max-width:480px){.wrap{padding:10px}.controls{flex-wrap:wrap}#sym{flex:1 1 100%}h1{font-size:16px}}
</style></head>
<body><div class="wrap">
<h1>🤖 RDA Advisor v2 — SME Edition</h1>
<div class="sub">Native web search · NSE filings · yfinance news · symbol resolver</div>
<div class="features"><span>🔍 Live web</span><span>📋 NSE filings</span><span>📰 yfinance</span><span>🎯 Smart resolver</span></div>
<div class="suggestions">
  <span data-q="Why is Vedanta falling? Is the demerger priced in?" data-s="VEDANTA">Vedanta demerger?</span>
  <span data-q="HFCL still a buy after the rally?" data-s="HFCL">HFCL after rally?</span>
  <span data-q="Latest concall takeaways" data-s="">Latest concall</span>
  <span data-q="Promoter pledge any red flags?" data-s="">Promoter pledge?</span>
  <span data-q="What's driving CGPOWER today?" data-s="CGPOWER">CGPOWER today?</span>
</div>
<div class="controls">
  <input id="sym" placeholder="SYMBOL or NAME">
  <input id="q" placeholder="Ask anything..." onkeypress="if(event.key==='Enter')ask()">
  <button id="ask" onclick="ask()">Ask</button>
</div>
<div id="feed"></div>
</div><script>
const feed=document.getElementById('feed'),q=document.getElementById('q'),sym=document.getElementById('sym'),btn=document.getElementById('ask');
document.querySelectorAll('.suggestions span').forEach(s=>{s.onclick=()=>{q.value=s.dataset.q;sym.value=s.dataset.s;ask();}});
async function ask(){
  const Q=q.value.trim();if(!Q)return;
  const S=sym.value.trim().toUpperCase();
  feed.insertAdjacentHTML('afterbegin',`<div class="msg user"><div class="head">YOU${S?' · '+S:''}</div>${esc(Q)}</div>`);
  const lid='l'+Date.now();
  feed.insertAdjacentHTML('afterbegin',`<div class="msg loading" id="${lid}"><div class="head">RDA</div>Searching web + filings + computing...</div>`);
  q.value='';btn.disabled=true;
  try{
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({question:Q,symbol:S})});
    const j=await r.json();
    const resolvedTag=(j.raw_symbol&&j.symbol&&j.raw_symbol!==j.symbol)?` (resolved: ${j.symbol})`:'';
    document.getElementById(lid).outerHTML=`<div class="msg"><div class="head">RDA · ${j.ts||''}${resolvedTag}</div>${esc(j.answer||j.error||'no response')}</div>`;
  }catch(e){document.getElementById(lid).outerHTML=`<div class="msg"><div class="head">RDA</div>Error: ${e.message}</div>`;}
  btn.disabled=false;
}
function esc(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
</script></body></html>"""

@app.route("/")
def home(): return render_template_string(HTML)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8503, debug=False)
