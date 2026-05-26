"""Daily Review — every EOD, Claude analyzes the day and tells us what to fix.

Workflow:
1. Pull last 24h of:
   - Trades (entries + exits + P&L)
   - Brain decisions (approved + rejected)
   - Market regime changes
   - News alerts
   - Stale agent events
2. Pass everything to Claude with prompt:
   "What worked? What didn't? What parameters should we adjust tomorrow?"
3. Claude returns:
   - Day grade (A-F)
   - Top wins (what worked)
   - Top losses (what failed)
   - Adjustments (specific parameter changes)
   - Tomorrow priorities
4. Save to data/daily_reviews/YYYY-MM-DD.json
5. Send Telegram summary
6. Optionally apply parameter adjustments (with config flag)

Run:
    python -m agents.learning.daily_review                    # standard EOD
    python -m agents.learning.daily_review --auto-apply       # apply tweaks to config
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List

import pandas as pd
import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv()

DATA_DIR = Path(__file__).parent.parent.parent / "data"
REVIEWS_DIR = DATA_DIR / "daily_reviews"

CLAUDE_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_BRAIN_MODEL", "claude-sonnet-4-6")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")


def _read_csv(name: str) -> pd.DataFrame:
    p = DATA_DIR / name
    if not p.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(p)
    except Exception:
        return pd.DataFrame()


def filter_last_24h(df: pd.DataFrame, time_col: str) -> pd.DataFrame:
    if df.empty or time_col not in df.columns:
        return df
    try:
        df = df.copy()
        df[time_col] = pd.to_datetime(df[time_col], errors="coerce")
        cutoff = datetime.now() - timedelta(hours=24)
        return df[df[time_col] >= cutoff]
    except Exception:
        return df


def collect_day_context() -> Dict:
    ctx: Dict = {"date": datetime.now().date().isoformat()}

    # Trades last 24h
    trades = _read_csv("trades.csv")
    trades_24 = filter_last_24h(trades, "exit_time" if "exit_time" in trades.columns else "entry_time")
    ctx["trades_24h"] = trades_24.to_dict(orient="records") if not trades_24.empty else []

    # Realized P&L summary
    if not trades_24.empty:
        if "realized_pnl" in trades_24.columns:
            ctx["pnl_total"] = float(trades_24["realized_pnl"].sum())
            ctx["pnl_wins"] = int((trades_24["realized_pnl"] > 0).sum())
            ctx["pnl_losses"] = int((trades_24["realized_pnl"] < 0).sum())
            ctx["pnl_win_rate"] = (trades_24["realized_pnl"] > 0).mean() * 100 if len(trades_24) else 0
            if "symbol" in trades_24.columns and ctx["pnl_total"] != 0:
                best = trades_24.nlargest(1, "realized_pnl").iloc[0].to_dict() if (trades_24["realized_pnl"] > 0).any() else {}
                worst = trades_24.nsmallest(1, "realized_pnl").iloc[0].to_dict() if (trades_24["realized_pnl"] < 0).any() else {}
                ctx["best_trade"] = {k: str(v) for k, v in best.items()}
                ctx["worst_trade"] = {k: str(v) for k, v in worst.items()}

    # Brain decisions
    decisions = _read_csv("brain_decisions.csv")
    dec_24 = filter_last_24h(decisions, "timestamp")
    if not dec_24.empty:
        ctx["brain_decisions_24h"] = dec_24.tail(30).to_dict(orient="records")

    # Open positions
    pos = _read_csv("positions.csv")
    if not pos.empty:
        ctx["open_positions"] = pos.to_dict(orient="records")

    # Market regime today
    regime = _read_csv("market_regime.csv")
    if not regime.empty:
        ctx["market_regime"] = regime.iloc[-1].to_dict()

    # Sector strength
    sec = _read_csv("sector_strength.csv")
    if not sec.empty:
        ctx["sector_strength_top10"] = sec.head(10).to_dict(orient="records")

    return ctx


SYSTEM_PROMPT = """You are the LEARNING agent for a paper-trading bot in Indian markets.
Your job: review today's trading activity and tell the bot how to improve TOMORROW.

You see:
- All trades (entry/exit/P&L)
- All decisions the brain made (approved + skipped)
- Open positions
- Market regime
- Sector strength

Output STRICT JSON (no fences):
{
  "day_grade": "A" | "B" | "C" | "D" | "F",
  "pnl_summary": "1 sentence",
  "what_worked": ["bullet 1", "bullet 2"],
  "what_failed": ["bullet 1", "bullet 2"],
  "root_causes": ["root cause 1"],
  "adjustments_for_tomorrow": [
    {"setting": "max_open_positions", "from": 5, "to": 3, "reason": "Choppy day, concentrate"},
    {"setting": "fortress_min_grade", "from": "B", "to": "A", "reason": "B-grade lost 60%"}
  ],
  "watch_tomorrow": ["specific symbols/sectors to monitor"],
  "priority_first_hour": "what to do at market open tomorrow"
}

Be SPECIFIC. Cite actual symbols and P&L. Don't be generic.
"""


def call_claude(ctx: Dict) -> Dict | None:
    if not CLAUDE_API_KEY:
        print("[review] ANTHROPIC_API_KEY missing")
        return None
    body = json.dumps(ctx, default=str, indent=2)[:60000]
    try:
        r = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": CLAUDE_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": 3000,
                "system": SYSTEM_PROMPT,
                "messages": [{"role": "user", "content": f"Here is today's data:\n\n{body}"}],
            },
            timeout=90,
        )
        if not r.ok:
            print(f"[review] Claude error {r.status_code}: {r.text[:400]}")
            return None
        text = "".join(b.get("text", "") for b in r.json().get("content", []) if b.get("type") == "text").strip()
        if text.startswith("```"):
            lines = text.split("\n")
            text = "\n".join(lines[1:-1] if lines[-1].startswith("```") else lines[1:])
        return json.loads(text)
    except Exception as e:
        print(f"[review] exception: {e}")
        return None


def format_telegram(review: Dict) -> str:
    grade = review.get("day_grade", "?")
    grade_emoji = {"A": "🌟", "B": "✅", "C": "⚠️", "D": "🔻", "F": "🚨"}.get(grade, "❔")
    lines = [
        f"{grade_emoji} *EOD REVIEW — Grade {grade}* — {datetime.now().strftime('%Y-%m-%d')}",
        f"💰 {review.get('pnl_summary', '')}",
        "",
        "*✅ What worked*",
    ]
    lines += [f"• {x}" for x in review.get("what_worked", [])[:4]]
    lines.append("")
    lines.append("*❌ What failed*")
    lines += [f"• {x}" for x in review.get("what_failed", [])[:4]]

    adj = review.get("adjustments_for_tomorrow", [])
    if adj:
        lines.append("")
        lines.append("*🔧 Tomorrow's adjustments*")
        for a in adj[:5]:
            lines.append(f"• `{a.get('setting')}`: {a.get('from')} → *{a.get('to')}* _({a.get('reason', '')[:90]})_")

    watch = review.get("watch_tomorrow", [])
    if watch:
        lines.append("")
        lines.append("*👀 Watch tomorrow*: " + ", ".join(map(str, watch[:8])))

    prio = review.get("priority_first_hour")
    if prio:
        lines.append("")
        lines.append(f"*🎯 First hour*: {prio[:300]}")

    return "\n".join(lines)


def send_telegram(text: str) -> bool:
    if not TG_TOKEN or not TG_CHAT:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        return r.ok
    except Exception:
        return False


def save_review(review: Dict, ctx: Dict) -> Path:
    REVIEWS_DIR.mkdir(exist_ok=True, parents=True)
    today = datetime.now().date().isoformat()
    fname = REVIEWS_DIR / f"{today}.json"
    payload = {"date": today, "review": review, "context_summary": {k: v for k, v in ctx.items() if k not in {"trades_24h", "brain_decisions_24h"}}}
    fname.write_text(json.dumps(payload, default=str, indent=2))
    return fname


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--silent", action="store_true")
    args = parser.parse_args()

    print(f"[review] Starting EOD review at {datetime.now().isoformat()}")
    ctx = collect_day_context()

    if not ctx.get("trades_24h") and not ctx.get("brain_decisions_24h"):
        print("[review] No trading activity today. Skipping.")
        return

    review = call_claude(ctx)
    if not review:
        return

    fname = save_review(review, ctx)
    msg = format_telegram(review)
    print(msg)
    print(f"\n[review] Saved to {fname}")

    if not args.silent:
        send_telegram(msg)


if __name__ == "__main__":
    main()
