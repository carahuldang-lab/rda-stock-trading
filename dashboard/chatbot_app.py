"""RDA Chatbot — interactive Q&A on any stock. Asks the multi-agent brain
context + Claude API. Mobile-responsive single-page UI on port 8503."""
from __future__ import annotations
import os, json, sys
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

app = Flask(__name__)



def resolve_symbol(query: str) -> str:
    """Convert 'VEDANTA' or 'vodafone idea' to NSE ticker via nifty500.csv."""
    if not query: return ""
    q = query.upper().strip()
    try:
        import pandas as pd
        df = pd.read_csv(DATA / "nifty500.csv")
        # Direct symbol hit
        if q in df["symbol"].astype(str).str.upper().values:
            return q
        # Fuzzy by company name (partial match)
        col = "company_name" if "company_name" in df.columns else df.columns[1]
        m = df[df[col].astype(str).str.upper().str.contains(q, na=False, regex=False)]
        if not m.empty:
            return str(m.iloc[0]["symbol"]).upper()
        # Common alias map
        aliases = {"VEDANTA":"VEDL","INFOSYS":"INFY","RELIANCE":"RELIANCE",
                   "HDFC BANK":"HDFCBANK","ICICI":"ICICIBANK","TCS":"TCS",
                   "BHARTI AIRTEL":"BHARTIARTL","VODAFONE IDEA":"IDEA",
                   "VODAFONE":"IDEA","SBI":"SBIN","STATE BANK":"SBIN"}
        return aliases.get(q, q)
    except Exception:
        return q


def gather_symbol_context(symbol: str) -> dict:
    """Pull everything we know about a symbol from CSVs."""
    sym = symbol.upper()
    ctx = {"symbol": sym, "timestamp": datetime.now(IST).isoformat()}
    for f, key in [("news.csv","news"),("fundamentals.csv","fundamentals"),
                   ("analyst_reports.csv","analyst"),("groww_holdings.csv","groww"),
                   ("positions.csv","positions"),("market_regime.csv","regime"),
                   ("sector_strength.csv","sectors"),("swing_candidates.csv","swing_score"),
                   ("groww_top_gainers.csv","gainers"),("groww_volume_shockers.csv","vol_shockers")]:
        p = DATA / f
        if not p.exists(): continue
        try:
            df = pd.read_csv(p)
            if "symbol" in df.columns:
                sub = df[df["symbol"].astype(str).str.upper() == sym]
                if not sub.empty:
                    ctx[key] = sub.tail(5).to_dict("records")
            elif f == "market_regime.csv":
                ctx[key] = df.iloc[-1].to_dict()
        except Exception: pass
    return ctx


def ask_claude(question: str, symbol: str | None) -> str:
    if not CLAUDE_KEY: return "Claude API key not configured."
    system = """You are RDA Stock Advisor — sharp Indian equity analyst.
Answer the user's question DIRECTLY and DECISIVELY. No fluff, no hedging.

Use ONLY the data provided below. If data is missing, say what's missing and what your
best inference is given the gap.

When recommending buy/sell/hold:
- Cite specific numbers (RSI, P/E, analyst target, % from 52w high)
- Give entry price, stop loss, target — be precise
- State confidence % (0-100)
- Compare your view vs analyst consensus (two-way check)

Format with short Markdown. Use 🟢 BUY / 🔴 SELL / 🟡 TRIM / ⚪ HOLD emojis when relevant.
Keep response under 1500 chars for mobile readability."""
    ctx = gather_symbol_context(symbol) if symbol else {"note": "no specific symbol"}
    user = f"Question: {question}\n\nData about {symbol or 'portfolio'}:\n{json.dumps(ctx, default=str, indent=2)[:15000]}"
    try:
        r = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_KEY, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": CLAUDE_MODEL, "max_tokens": 1500, "system": system,
                  "messages": [{"role":"user","content": user}]}, timeout=60)
        if not r.ok: return f"Claude error {r.status_code}: {r.text[:300]}"
        return "".join(b.get("text","") for b in r.json().get("content",[]) if b.get("type")=="text").strip()
    except Exception as e:
        return f"Exception: {e}"


@app.route("/api/chat", methods=["POST"])
def api_chat():
    data = request.get_json(force=True)
    question = (data.get("question") or "").strip()
    raw_sym = (data.get("symbol") or "").strip().upper()
    symbol = resolve_symbol(raw_sym) if raw_sym else None
    if not question:
        return jsonify({"error": "empty question"}), 400
    answer = ask_claude(question, symbol)
    return jsonify({
        "question": question, "symbol": symbol, "answer": answer,
        "ts": datetime.now(IST).strftime("%H:%M IST")
    })


HTML = """<!doctype html>
<html lang="en"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, maximum-scale=1">
<title>RDA Advisor</title>
<style>
*{box-sizing:border-box;margin:0;padding:0;font-family:-apple-system,Segoe UI,Roboto,sans-serif}
body{background:#0f1419;color:#e8e6e3;min-height:100vh;padding:0}
.wrap{max-width:720px;margin:0 auto;padding:16px;display:flex;flex-direction:column;height:100vh}
h1{font-size:18px;color:#88d3ce;margin-bottom:4px;letter-spacing:.5px}
.sub{font-size:11px;color:#6b7280;margin-bottom:12px}
.controls{display:flex;gap:8px;margin-bottom:12px}
#sym{flex:0 0 120px;background:#1a1f2e;border:1px solid #2a3142;color:#e8e6e3;padding:10px;border-radius:8px;font-size:14px;text-transform:uppercase}
#q{flex:1;background:#1a1f2e;border:1px solid #2a3142;color:#e8e6e3;padding:10px;border-radius:8px;font-size:14px}
#ask{background:#88d3ce;color:#0f1419;border:0;padding:10px 18px;border-radius:8px;font-weight:600;cursor:pointer;font-size:14px}
#ask:hover{opacity:0.85}
#ask:disabled{opacity:0.5;cursor:wait}
#feed{flex:1;overflow-y:auto;padding-right:4px}
.msg{background:#1a1f2e;border-left:3px solid #88d3ce;padding:12px;border-radius:6px;margin-bottom:12px;font-size:13px;line-height:1.55;white-space:pre-wrap}
.msg .head{color:#88d3ce;font-weight:600;margin-bottom:6px;font-size:12px}
.msg.user{border-left-color:#fbbf24}
.msg.user .head{color:#fbbf24}
.loading{color:#6b7280;font-style:italic}
.suggestions{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:10px}
.suggestions span{background:#1a1f2e;border:1px solid #2a3142;padding:5px 10px;border-radius:12px;font-size:11px;cursor:pointer;color:#88d3ce}
.suggestions span:hover{background:#252b3e}
@media(max-width:480px){
  .wrap{padding:10px}
  .controls{flex-wrap:wrap}
  #sym{flex:1 1 100%}
  h1{font-size:16px}
}
</style></head>
<body>
<div class="wrap">
<h1>🤖 RDA Stock Advisor</h1>
<div class="sub">Ask about any stock — buy/sell/hold, news, fundamentals, technicals</div>
<div class="suggestions">
  <span data-q="Should I buy or hold?" data-s="">Should I buy or hold?</span>
  <span data-q="What's the technical setup?" data-s="">Technical setup?</span>
  <span data-q="What are analysts saying?" data-s="">Analyst view?</span>
  <span data-q="Is HFCL still a good entry?" data-s="HFCL">HFCL entry?</span>
  <span data-q="Should I trim VEDL?" data-s="VEDL">Trim VEDL?</span>
</div>
<div class="controls">
  <input id="sym" placeholder="SYMBOL">
  <input id="q" placeholder="Ask anything..." onkeypress="if(event.key==='Enter')ask()">
  <button id="ask" onclick="ask()">Ask</button>
</div>
<div id="feed"></div>
</div>
<script>
const feed=document.getElementById('feed'), q=document.getElementById('q'), sym=document.getElementById('sym'), btn=document.getElementById('ask');
document.querySelectorAll('.suggestions span').forEach(s=>{s.onclick=()=>{q.value=s.dataset.q;sym.value=s.dataset.s;ask();}});
async function ask(){
  const Q=q.value.trim(); if(!Q)return;
  const S=sym.value.trim().toUpperCase();
  feed.insertAdjacentHTML('afterbegin', `<div class="msg user"><div class="head">YOU${S?' · '+S:''}</div>${escapeHtml(Q)}</div>`);
  const loadId='l'+Date.now();
  feed.insertAdjacentHTML('afterbegin', `<div class="msg loading" id="${loadId}"><div class="head">RDA</div>Thinking…</div>`);
  q.value=''; btn.disabled=true;
  try{
    const r=await fetch('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},
      body:JSON.stringify({question:Q,symbol:S})});
    const j=await r.json();
    document.getElementById(loadId).outerHTML = `<div class="msg"><div class="head">RDA · ${j.ts||''}</div>${escapeHtml(j.answer||j.error||'no response')}</div>`;
  }catch(e){
    document.getElementById(loadId).outerHTML = `<div class="msg"><div class="head">RDA</div>Error: ${e.message}</div>`;
  }
  btn.disabled=false;
}
function escapeHtml(s){return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');}
</script></body></html>"""


@app.route("/")
def home(): return render_template_string(HTML)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8503, debug=False)
