"""Claude Decision Brain — the central AI that makes ALL trade decisions.

Replaces rigid rule-based decisions with Claude-API-driven holistic analysis.

Workflow:
1. Collect EVERYTHING from all agents:
   - Positions (paper + groww)
   - Market regime (Nifty, VIX, breadth)
   - Per-symbol technical signals (RSI, MACD, EMA, volume, patterns)
   - News (last 24h, with sentiment)
   - Fundamentals (P/E, ROE, debt, market cap)
   - Analyst data (recommendations, price targets, upgrades)
   - Earnings calendar (next 30 days)
   - Sector strength
2. Detect stale agents (data > N hours old) → trigger refresh
3. Build comprehensive prompt → send to Claude API
4. Claude returns: ACTION (BUY/HOLD/SELL/EXIT/TRIM/ADD), with:
   - symbol, side, qty, target, stop, hold_days
   - confidence_pct
   - reasoning (cites data)
   - risk_flags
5. Send Telegram with proposed action + approval gate
6. Log decision to data/brain_decisions.csv for learning loop

Run:
    python -m agents.brain.claude_decision_brain                    # full portfolio review
    python -m agents.brain.claude_decision_brain --symbol SYNGENE   # single symbol
    python -m agents.brain.claude_decision_brain --review-only      # no approval, just analyze
"""
from __future__ import annotations
import re

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

load_dotenv()

DATA_DIR = Path(__file__).parent.parent.parent / "data"
DECISIONS_FILE = DATA_DIR / "brain_decisions.csv"
LOG_FILE = DATA_DIR.parent / "logs" / "brain.log"

CLAUDE_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_BRAIN_MODEL", "claude-sonnet-4-6")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# Staleness thresholds (hours) - if data older than this, flag for refresh
STALENESS_HOURS = {
    "market_regime.csv": 2,
    "news.csv": 6,
    "fundamentals.csv": 168,   # weekly
    "analyst_reports.csv": 168,
    "sector_strength.csv": 24,
    "earnings_calendar.csv": 24,
    "positions.csv": 1,
    "groww_holdings.csv": 1,
}


# ============================================================
# Data Collectors
# ============================================================

def _read_csv(name: str) -> pd.DataFrame:
    p = DATA_DIR / name
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def _file_age_hours(name: str) -> Optional[float]:
    p = DATA_DIR / name
    if not p.exists():
        return None
    age = datetime.now() - datetime.fromtimestamp(p.stat().st_mtime)
    return age.total_seconds() / 3600


def detect_stale_agents() -> List[str]:
    """Return list of agent data files that are stale or missing."""
    stale = []
    for filename, max_hours in STALENESS_HOURS.items():
        age = _file_age_hours(filename)
        if age is None:
            stale.append(f"{filename} (MISSING)")
        elif age > max_hours:
            stale.append(f"{filename} ({age:.1f}h old, max {max_hours}h)")
    return stale


def collect_full_context(symbol: Optional[str] = None) -> Dict:
    """Gather every data point the bot has into one structured dict."""
    ctx = {
        "timestamp": datetime.now().isoformat(),
        "market_regime": {},
        "positions": [],
        "groww_holdings": [],
        "stale_agents": detect_stale_agents(),
    }

    # Market regime
    regime_df = _read_csv("market_regime.csv")
    if not regime_df.empty:
        latest = regime_df.iloc[-1].to_dict()
        ctx["market_regime"] = {k: str(v) for k, v in latest.items() if pd.notna(v)}

    # Positions (paper)
    pos_df = _read_csv("positions.csv")
    if not pos_df.empty:
        for _, r in pos_df.iterrows():
            ctx["positions"].append({k: str(r[k]) if pd.notna(r.get(k)) else None for k in pos_df.columns})

    # Groww holdings (real money)
    groww_df = _read_csv("groww_holdings.csv")
    if not groww_df.empty:
        for _, r in groww_df.iterrows():
            ctx["groww_holdings"].append({k: str(r[k]) if pd.notna(r.get(k)) else None for k in groww_df.columns})

    # If specific symbol requested, deep-dive
    if symbol:
        ctx["focus_symbol"] = symbol.upper()
        ctx["symbol_signals"] = collect_symbol_signals(symbol.upper())
    else:
        # All held symbols get signal snapshots
        held = set()
        for p in ctx["positions"]:
            if p.get("symbol"):
                held.add(p["symbol"].upper())
        for h in ctx["groww_holdings"]:
            if h.get("symbol"):
                held.add(h["symbol"].upper())
        ctx["held_symbol_signals"] = {s: collect_symbol_signals(s) for s in list(held)[:8]}

    # Sector strength
    sec_df = _read_csv("sector_strength.csv")
    if not sec_df.empty:
        ctx["sector_strength"] = sec_df.head(20).to_dict(orient="records")

    # Earnings calendar (next 30 days)
    earn_df = _read_csv("earnings_calendar.csv")
    if not earn_df.empty:
        ctx["upcoming_earnings"] = earn_df.head(30).to_dict(orient="records")

    # Recent candidates (top scoring)
    cand_df = _read_csv("candidates.csv")
    if not cand_df.empty and "score" in cand_df.columns:
        top = cand_df.sort_values("score", ascending=False).head(10)
        ctx["top_candidates"] = top.to_dict(orient="records")

    return ctx


def collect_symbol_signals(symbol: str) -> Dict:
    """All known signals for one symbol — news, fundamentals, analyst, recent quote."""
    out = {"symbol": symbol}

    # News (last 7 days)
    news = _read_csv("news.csv")
    if not news.empty and "symbol" in news.columns:
        sub = news[news["symbol"].astype(str).str.upper() == symbol]
        if not sub.empty and "published_at" in sub.columns:
            try:
                sub = sub.copy()
                sub["published_at"] = pd.to_datetime(sub["published_at"], errors="coerce", utc=True)
                cutoff = pd.Timestamp.utcnow() - pd.Timedelta(days=7)
                sub = sub[sub["published_at"] >= cutoff].sort_values("published_at", ascending=False)
            except Exception:
                pass
            out["news"] = [
                {
                    "headline": str(r.get("headline", ""))[:140],
                    "publisher": str(r.get("publisher", "")),
                    "sentiment": str(r.get("sentiment", "neutral")),
                }
                for _, r in sub.head(5).iterrows()
            ]

    # Fundamentals
    fund = _read_csv("fundamentals.csv")
    if not fund.empty and "symbol" in fund.columns:
        row = fund[fund["symbol"].astype(str).str.upper() == symbol]
        if not row.empty:
            out["fundamentals"] = {k: str(v) for k, v in row.iloc[0].to_dict().items() if pd.notna(v)}

    # Analyst
    analyst = _read_csv("analyst_reports.csv")
    if not analyst.empty and "symbol" in analyst.columns:
        row = analyst[analyst["symbol"].astype(str).str.upper() == symbol]
        if not row.empty:
            out["analyst"] = {k: str(v) for k, v in row.iloc[-1].to_dict().items() if pd.notna(v)}

    # Live LTP
    ltp = _read_csv("live_ltp.csv")
    if not ltp.empty and "symbol" in ltp.columns:
        row = ltp[ltp["symbol"].astype(str).str.upper() == symbol]
        if not row.empty:
            out["live_ltp"] = {k: str(v) for k, v in row.iloc[-1].to_dict().items() if pd.notna(v)}

    return out


# ============================================================
# Claude API Call
# ============================================================

SYSTEM_PROMPT = """You are the BRAIN agent for an Indian equity swing/positional trading bot.
You take FULL responsibility for every trade decision. You see everything the bot sees:
market regime, news, fundamentals, analyst data, technical signals, sector strength, earnings calendar.

CRITICAL OPERATING PRINCIPLES (USER MANDATE — do not violate):

1. REGIME AND SECTOR ARE SIZING INPUTS, NOT VETOES.
   - BEARISH market does NOT mean block all BUYs. It means smaller position sizes.
   - WEAK sector does NOT mean reject the stock. It means deeper individual conviction required.
   - Only block a BUY when the STOCK ITSELF fails (no catalyst, overbought, broken structure, fraud news).
   - Examples of stocks that ran +50-140% while the broader Nifty was flat/down:
     HFCL, Bliss GVS Pharma, IFB Industries, KEC, BHEL, Cochin Shipyard, Mazagon Dock.
     All driven by ORDERS / RESULTS / RE-RATING — NOT by market regime.

2. PRIORITIZE INDIVIDUAL CATALYSTS OVER MARKET CONDITIONS:
   - Quarterly result beat (revenue + EPS up >15% YoY) → strong BUY signal.
   - Order-book updates / contract wins → high conviction BUY.
   - Sector-specific tailwinds (defense, capex, EV, semiconductors, healthcare) → over-weight.
   - Breakout with volume > 3x avg + RSI 55-70 + EMA aligned → swing entry.
   - Analyst upgrade or target hike >10% → ADD signal.
   - 52-week-high breakout in a fundamentally clean stock → BUY (do NOT pass it citing "near high").

3. AVOID THE USER'S KNOWN PAIN POINTS:
   - Don't blindly apply static -1%/-2% stops. Use ATR and support levels.
   - DO catch post-earnings rallies (70-100% in a month) — highest-value setups.
   - DO NOT trade when news is materially negative (RBI inquiry, scam, accounting fraud) — EXIT fast.
   - DO recommend HOLDING through volatility if fundamentals are intact AND no negative catalyst.

4. WHEN IN DOUBT, FAVOR ACTION OVER INACTION:
   - "PASS" with no entry is acceptable ONLY when the stock has zero catalyst AND broken structure.
   - In NEUTRAL/BEARISH regime: still recommend BUYS for stocks with strong individual setups,
     just at reduced size (size_mult 0.3-0.5). DO NOT default to HOLD/PASS on every candidate.
   - Confidence < 50% on a HOLD is worse than confidence 60% on a small-size BUY.

5. BE ADAPTIVE, NOT RIGID. Cite specific data for every decision.
   Flag STALE_AGENT entries as data gaps that may have led to a bad decision.

Output format (STRICT JSON only, no fences):
{
  "decisions": [
    {
      "symbol": "SYNGENE",
      "action": "BUY" | "SELL" | "HOLD" | "EXIT" | "TRIM" | "ADD",
      "side": "buy" | "sell" | null,
      "qty": 100,
      "entry_price": 890.5,
      "stop_loss": 850.0,
      "target": 970.0,
      "expected_hold_days": 7,
      "confidence_pct": 82,
      "reasoning": "2-3 sentence cite from data (must mention specific catalyst)",
      "risk_flags": ["earnings_in_5d", "low_liquidity"]
    }
  ],
  "market_view": "1 sentence overall market view",
  "data_gaps": ["news.csv missing", "fundamentals stale 5d"],
  "next_review_minutes": 60
}
"""


def call_claude(context: Dict) -> Optional[Dict]:
    if not CLAUDE_API_KEY:
        print("[brain] ANTHROPIC_API_KEY not set — cannot run Claude brain.")
        return None

    user_content = (
        "Here is the full bot state. Decide what to do for each held position "
        "and any top candidate worth entering. Be adaptive — don't use static stops.\n\n"
        + json.dumps(context, default=str, indent=2)[:30000]
    )

    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": CLAUDE_API_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 4500,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": user_content}],
            },
            timeout=120,
        )
        if not r.ok:
            print(f"[brain] Claude API error {r.status_code}: {r.text[:400]}")
            return None
        body = r.json()
        text = "".join(b.get("text", "") for b in body.get("content", []) if b.get("type") == "text")
        # Strip fences if present
        text = text.strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            decoder = json.JSONDecoder()
            text_stripped = text.lstrip()
            try:
                obj, _ = decoder.raw_decode(text_stripped)
                return obj
            except Exception:
                # Try to find first {...} block
                m = re.search(r'\{.*\}', text, re.DOTALL)
                if m:
                    try: return json.loads(m.group(0))
                    except Exception: pass
                print(f"[brain] Could not parse Claude response, raw text:\n{text[:1500]}")
                return None
    except Exception as e:
        print(f"[brain] Claude call exception: {e}")
        return None


# ============================================================
# Telegram + Logging
# ============================================================

def format_decision_for_telegram(decision: Dict) -> str:
    lines = [f"🧠 *BRAIN DECISION* — {datetime.now().strftime('%H:%M IST')}"]
    mv = decision.get("market_view")
    if mv:
        lines.append(f"📊 _{mv}_")
    lines.append("")

    decisions = decision.get("decisions", [])
    if not decisions:
        lines.append("✋ No actionable trades right now.")
    else:
        for d in decisions:
            action = d.get("action", "?")
            sym = d.get("symbol", "?")
            conf = d.get("confidence_pct", 0)
            emoji = {"BUY": "🟢", "ADD": "🟢", "SELL": "🔴", "EXIT": "🔴", "TRIM": "🟡", "HOLD": "⚪"}.get(action, "❔")
            lines.append(f"{emoji} *{action} {sym}* (conf {conf}%)")
            if d.get("entry_price"):
                lines.append(f"   Entry: ₹{d.get('entry_price')} | Stop: ₹{d.get('stop_loss')} | Target: ₹{d.get('target')} | Hold: {d.get('expected_hold_days')}d")
            r = d.get("reasoning", "")
            if r:
                lines.append(f"   _{r[:240]}_")
            flags = d.get("risk_flags", [])
            if flags:
                lines.append(f"   ⚠️ {', '.join(flags)}")
            lines.append("")

    gaps = decision.get("data_gaps", [])
    if gaps:
        lines.append("🔧 _Data gaps to refresh:_ " + ", ".join(gaps[:5]))

    if decisions:
        lines.append("")
        lines.append("Reply *YES* to approve all BUYs/SELLs, *NO* to skip, or *SYM YES* for individual.")

    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    """Send to Telegram. Try Markdown first; if Telegram rejects (400), retry as plain text.
    Returns True only on confirmed 200 OK. Logs failures."""
    if not TG_TOKEN or not TG_CHAT:
        print("[tg] no credentials")
        return False
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    for attempt, payload in enumerate([
        {"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"},
        {"chat_id": TG_CHAT, "text": text},  # fallback: no parse mode
    ]):
        try:
            r = requests.post(url, json=payload, timeout=10)
            if r.ok:
                if attempt == 1:
                    print("[tg] sent as plain text (markdown rejected)")
                return True
            print(f"[tg] attempt {attempt+1} HTTP {r.status_code}: {r.text[:300]}")
        except Exception as e:
            print(f"[tg] attempt {attempt+1} exception: {e}")
    return False


def log_decision(decision: Dict, context_summary: Dict) -> None:
    DECISIONS_FILE.parent.mkdir(exist_ok=True, parents=True)
    rows = []
    for d in decision.get("decisions", []):
        rows.append({
            "timestamp": datetime.now().isoformat(),
            "symbol": d.get("symbol"),
            "action": d.get("action"),
            "qty": d.get("qty"),
            "entry": d.get("entry_price"),
            "stop": d.get("stop_loss"),
            "target": d.get("target"),
            "hold_days": d.get("expected_hold_days"),
            "confidence": d.get("confidence_pct"),
            "reasoning": (d.get("reasoning") or "")[:500],
            "risk_flags": ",".join(d.get("risk_flags", [])),
            "market_view": (decision.get("market_view") or "")[:200],
            "data_gaps": ",".join(decision.get("data_gaps", [])),
            "approved": "pending",
            "outcome": "pending",
        })
    if not rows:
        return
    df_new = pd.DataFrame(rows)
    if DECISIONS_FILE.exists():
        df_old = pd.read_csv(DECISIONS_FILE)
        df_new = pd.concat([df_old, df_new], ignore_index=True)
    df_new.to_csv(DECISIONS_FILE, index=False)


# ============================================================
# Main
# ============================================================

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--symbol", help="Focus on a single symbol")
    parser.add_argument("--review-only", action="store_true", help="Don't send Telegram approval, just log")
    parser.add_argument("--silent", action="store_true", help="No Telegram send (testing)")
    args = parser.parse_args()

    print(f"[brain] Starting Claude Decision Brain at {datetime.now().isoformat()}")

    # 1. Collect context
    ctx = collect_full_context(symbol=args.symbol)
    print(f"[brain] Collected context: {len(ctx.get('positions', []))} paper, {len(ctx.get('groww_holdings', []))} groww, {len(ctx.get('stale_agents', []))} stale agents")

    # 2. Call Claude
    decision = call_claude(ctx)
    if not decision:
        print("[brain] No decision returned. Skipping.")
        return

    # 3. Format + log
    msg = format_decision_for_telegram(decision)
    print(msg)
    log_decision(decision, ctx)

    # 4. Telegram
    if not args.silent and not args.review_only:
        ok = send_telegram(msg)
        print(f"[brain] Telegram {'sent.' if ok else 'FAILED — message not delivered.'}")


if __name__ == "__main__":
    main()
