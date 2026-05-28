"""RDA Trading Terminal — LUXURY Edition.

A premium single-page dashboard served via Flask on port 8502.
Pure HTML/CSS/D3.js — no Streamlit constraints.

Run:
    python dashboard/luxury_app.py

Opens at http://localhost:8502
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, date
from zoneinfo import ZoneInfo
from pathlib import Path

import pandas as pd
from flask import Flask, jsonify, render_template_string

sys.path.insert(0, str(Path(__file__).parent.parent))

DATA_DIR = Path(__file__).parent.parent / "data"
app = Flask(__name__)


# ---------- Data loaders (lightweight, no Streamlit cache) ----------
def _read(filename: str) -> pd.DataFrame:
    f = DATA_DIR / filename
    if not f.exists():
        return pd.DataFrame()
    try:
        return pd.read_csv(f)
    except Exception:
        return pd.DataFrame()


def _read_universe() -> dict:
    df = _read("nifty500.csv")
    if df.empty or "company_name" not in df.columns:
        return {}
    return dict(zip(df["symbol"], df["company_name"]))


def _load_config():
    try:
        from utils.config_loader import load_config
        return load_config()
    except Exception:
        return {"account": {"capital": 2000000, "trading_mode": "PAPER"}}


def _clean_nan(obj):
    """Recursively replace NaN/Infinity with 0 — they break browser JSON.parse()."""
    import math as _math
    if isinstance(obj, float):
        if _math.isnan(obj) or _math.isinf(obj):
            return 0
        return obj
    if isinstance(obj, dict):
        return {k: _clean_nan(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_clean_nan(v) for v in obj]
    return obj


@app.route("/api/snapshot")
def api_snapshot():
    today = date.today().isoformat()
    cfg = _load_config()
    universe = _read_universe()

    intra_pos = _read("intraday_positions.csv")
    intra_trades = _read("intraday_trades.csv")
    swing_pos = _read("positions.csv")
    regime = _read("market_regime.csv")
    events_path = DATA_DIR / "events.jsonl"
    live_movers = _read("live_movers.csv")

    # ---- Today's intraday trades only ----
    today_trades = pd.DataFrame()
    if not intra_trades.empty and "exit_time" in intra_trades.columns:
        today_trades = intra_trades[
            intra_trades["exit_time"].astype(str).str.startswith(today)
        ]

    realized_pnl = float(today_trades["pnl_net"].astype(float).sum()) if not today_trades.empty else 0.0
    n_trades = len(today_trades)
    wins = int((today_trades["pnl_net"].astype(float) > 0).sum()) if not today_trades.empty else 0
    losses = int((today_trades["pnl_net"].astype(float) < 0).sum()) if not today_trades.empty else 0
    gross_win = float(today_trades[today_trades["pnl_net"].astype(float) > 0]["pnl_net"].sum()) if not today_trades.empty else 0
    gross_loss = float(today_trades[today_trades["pnl_net"].astype(float) < 0]["pnl_net"].sum()) if not today_trades.empty else 0
    profit_factor = (gross_win / abs(gross_loss)) if gross_loss != 0 else 0
    win_rate = (wins / n_trades * 100) if n_trades else 0

    # ---- Open positions ----
    open_pnl = 0.0
    positions_list = []
    if not intra_pos.empty:
        intra_pos = intra_pos.copy()
        intra_pos["unrealized_pnl"] = pd.to_numeric(intra_pos["unrealized_pnl"], errors="coerce").fillna(0)
        open_pnl = float(intra_pos["unrealized_pnl"].sum())
        for _, p in intra_pos.iterrows():
            sym = str(p["symbol"])
            entry = float(p.get("entry_price", 0))
            ltp = float(p.get("current_price", entry))
            sl = float(p.get("stop_loss", entry * 0.99))
            tgt = float(p.get("target", entry * 1.02))
            qty = int(p.get("quantity", 0))
            pnl = float(p.get("unrealized_pnl", 0))
            pnl_pct = ((ltp - entry) / entry * 100) if entry else 0
            etime = str(p.get("entry_time", ""))
            etime_short = etime[11:16] if "T" in etime else etime
            # Progress: 0=at SL, 50=at entry, 100=at TGT
            prog = ((ltp - sl) / (tgt - sl) * 100) if tgt > sl else 50
            prog = max(0, min(100, prog))
            entry_pos = ((entry - sl) / (tgt - sl) * 100) if tgt > sl else 50
            state = "winning" if pnl > 0 else "losing" if pnl < 0 else "flat"
            pct_to_tgt = (ltp - entry) / (tgt - entry) if tgt > entry else 0
            pct_to_sl = (entry - ltp) / (entry - sl) if entry > sl else 0
            if pct_to_tgt > 0.7:
                state = "near_tgt"
            elif pct_to_sl > 0.7:
                state = "near_sl"
            positions_list.append({
                "symbol": sym,
                "company": universe.get(sym, sym),
                "qty": qty,
                "entry": round(entry, 2),
                "ltp": round(ltp, 2),
                "sl": round(sl, 2),
                "tgt": round(tgt, 2),
                "pnl": round(pnl, 2),
                "pnl_pct": round(pnl_pct, 2),
                "value": round(qty * ltp, 0),
                "risk": round(qty * (entry - sl), 0),
                "strategy": str(p.get("strategy", "intraday")),
                "entry_time": etime_short,
                "state": state,
                "progress": round(prog, 1),
                "entry_pos": round(entry_pos, 1),
            })

    # ---- P&L curve through the day ----
    curve = []
    if not today_trades.empty:
        c = today_trades.sort_values("exit_time").copy()
        c["pnl_net"] = pd.to_numeric(c["pnl_net"], errors="coerce").fillna(0)
        c["cum"] = c["pnl_net"].cumsum()
        for _, r in c.iterrows():
            curve.append({
                "time": str(r["exit_time"])[11:16] if "T" in str(r["exit_time"]) else "",
                "symbol": str(r["symbol"]),
                "pnl": float(r["pnl_net"]),
                "cum": float(r["cum"]),
            })

    # ---- Strategy attribution ----
    strat_breakdown = []
    if not today_trades.empty and "strategy" in today_trades.columns:
        gb = today_trades.groupby("strategy").agg(
            trades=("pnl_net", "count"),
            wins=("pnl_net", lambda x: (x.astype(float) > 0).sum()),
            pnl=("pnl_net", lambda x: x.astype(float).sum()),
        ).reset_index().sort_values("pnl", ascending=False)
        for _, r in gb.iterrows():
            strat_breakdown.append({
                "strategy": str(r["strategy"]),
                "trades": int(r["trades"]),
                "wins": int(r["wins"]),
                "win_rate": round(r["wins"] / r["trades"] * 100, 0) if r["trades"] else 0,
                "pnl": float(r["pnl"]),
            })

    # ---- Top closed trades today ----
    trade_log = []
    if not today_trades.empty:
        for _, r in today_trades.sort_values("exit_time", ascending=False).head(20).iterrows():
            sym = str(r["symbol"])
            trade_log.append({
                "time": str(r["exit_time"])[11:16] if "T" in str(r["exit_time"]) else "",
                "symbol": sym,
                "company": universe.get(sym, sym),
                "qty": int(r.get("quantity", 0)),
                "entry": round(float(r.get("entry_price", 0)), 2),
                "exit": round(float(r.get("exit_price", 0)), 2),
                "pnl": float(r.get("pnl_net", 0)),
                "pnl_pct": float(str(r.get("pnl_pct", "0")).replace("%","").replace(",","").strip() or 0),
                "reason": str(r.get("exit_reason", "")),
                "strategy": str(r.get("strategy", "")),
                "held_min": int(r.get("holding_minutes", 0)),
            })

    # ---- Regime ----
    regime_now = {"label": "UNKNOWN", "size": 1.0, "vix": 0, "rsi": 0, "reason": ""}
    if not regime.empty:
        last = regime.iloc[-1]
        regime_now = {
            "label": str(last["regime"]),
            "size": float(last.get("size_mult", 1.0)),
            "vix": float(last.get("vix", 0)),
            "rsi": float(last.get("nifty_rsi", 50)),
            "nifty_pct": float(last.get("nifty_vs_200ema_pct", 0)),
            "reason": str(last.get("reasoning", ""))[:120],
        }

    # ---- Live movers (recent breakout candidates) ----
    movers = []
    if not live_movers.empty:
        for _, r in live_movers.head(8).iterrows():
            movers.append({
                "symbol": str(r["symbol"]),
                "pct": float(r.get("pct_change_today", 0)),
                "vol": float(r.get("vol_ratio", 0)),
                "price": float(r.get("last_close", 0)),
            })

    # ---- TODAY'S TRADING PLAN (what bot is looking to buy) ----
    intraday_picks = []
    intra_cand = _read("intraday_candidates.csv")
    if not intra_cand.empty:
        for _, r in intra_cand.head(10).iterrows():
            sym = str(r["symbol"])
            intraday_picks.append({
                "symbol": sym,
                "company": universe.get(sym, sym),
                "yest_close": float(r.get("yesterday_close", 0)),
                "pct": float(r.get("pct_change", 0)),
                "vol": float(r.get("volume_ratio", 0)),
                "score": float(r.get("score", 0)),
            })

    swing_picks = []
    sw_cand = _read("candidates.csv")
    if not sw_cand.empty:
        for _, r in sw_cand.head(10).iterrows():
            sym = str(r["symbol"])
            swing_picks.append({
                "rank": int(r.get("rank", 0)),
                "symbol": sym,
                "company": universe.get(sym, sym),
                "sector": str(r.get("sector", "")),
                "grade": str(r.get("grade", "")),
                "score": float(r.get("score", 0)),
                "price": float(r.get("last_close", 0)),
                "rsi": float(r.get("rsi", 0)),
                "trend_pct": float(r.get("trend_pct", 0)),
            })

    catalysts_today = []
    cat_cal = _read("catalyst_calendar.csv")
    if not cat_cal.empty and "date" in cat_cal.columns:
        cat_cal["date"] = cat_cal["date"].astype(str)
        upcoming = cat_cal[cat_cal["date"] >= today].sort_values(
            ["date", "impact_score"], ascending=[True, False]
        ).head(15)
        for _, r in upcoming.iterrows():
            catalysts_today.append({
                "symbol": str(r["symbol"]),
                "company": str(r.get("company", "")),
                "date": str(r["date"]),
                "type": str(r.get("type", "")),
                "headline": str(r.get("headline", ""))[:120],
                "impact": int(r.get("impact_score", 0)),
            })

    # ---- Recent events (last 30) ----
    recent_events = []
    if events_path.exists():
        try:
            lines = events_path.read_text(encoding="utf-8").splitlines()[-30:]
            for line in reversed(lines):
                try:
                    e = json.loads(line)
                    recent_events.append({
                        "ts": str(e.get("timestamp", ""))[11:19],
                        "agent": e.get("agent", ""),
                        "action": e.get("action", ""),
                        "symbol": e.get("symbol", ""),
                        "details": e.get("details", "")[:120],
                        "level": e.get("level", "info"),
                    })
                except Exception:
                    continue
        except Exception:
            pass

    capital = float(cfg["account"]["capital"])
    swing_value = 0.0
    swing_pnl = 0.0
    swing_list = []
    if not swing_pos.empty:
        swing_pos = swing_pos.copy()
        swing_pos["quantity"] = pd.to_numeric(swing_pos["quantity"], errors="coerce").fillna(0)
        swing_pos["entry_price"] = pd.to_numeric(swing_pos["entry_price"], errors="coerce").fillna(0)
        swing_pos["current_price"] = pd.to_numeric(swing_pos.get("current_price", swing_pos["entry_price"]), errors="coerce").fillna(swing_pos["entry_price"])
        swing_pos["unrealized_pnl"] = pd.to_numeric(swing_pos.get("unrealized_pnl", 0), errors="coerce").fillna(0)
        swing_value = float((swing_pos["quantity"] * swing_pos["entry_price"]).sum())
        swing_pnl = float(swing_pos["unrealized_pnl"].sum())
        for _, p in swing_pos.iterrows():
            sym = str(p["symbol"])
            entry = float(p.get("entry_price", 0))
            ltp = float(p.get("current_price", entry))
            sl = float(p.get("stop_loss", entry * 0.95))
            tgt = float(p.get("target", entry * 1.10))
            qty = int(p.get("quantity", 0))
            pnl = float(p.get("unrealized_pnl", 0))
            pnl_pct = ((ltp - entry) / entry * 100) if entry else 0
            etime = str(p.get("entry_time", ""))[:10]
            prog = ((ltp - sl) / (tgt - sl) * 100) if tgt > sl else 50
            prog = max(0, min(100, prog))
            entry_pos = ((entry - sl) / (tgt - sl) * 100) if tgt > sl else 50
            state = "winning" if pnl > 0 else "losing" if pnl < 0 else "flat"
            swing_list.append({
                "symbol": sym,
                "company": universe.get(sym, sym),
                "qty": qty, "entry": round(entry, 2), "ltp": round(ltp, 2),
                "sl": round(sl, 2), "tgt": round(tgt, 2),
                "pnl": round(pnl, 2), "pnl_pct": round(pnl_pct, 2),
                "value": round(qty * ltp, 0),
                "strategy": str(p.get("strategy", "swing")),
                "sector": str(p.get("sector", "")),
                "entry_date": etime,
                "state": state,
                "progress": round(prog, 1),
                "entry_pos": round(entry_pos, 1),
            })
    swing_count = len(swing_list)

    intra_value = sum(p["value"] for p in positions_list)
    deployed = swing_value + intra_value

    # === Groww integration — REAL portfolio + REAL volume shockers ===
    groww_holdings_list = []
    groww_holdings_summary = None
    gh = _read("groww_holdings.csv")
    if not gh.empty:
        for _, r in gh.iterrows():
            groww_holdings_list.append({
                "symbol": str(r["symbol"]),
                "company": str(r.get("company", "")),
                "qty": int(r.get("qty", 0)),
                "avg_price": float(r.get("avg_price", 0)),
                "ltp": float(r.get("ltp", 0)),
                "invested": float(r.get("invested", 0)),
                "pnl_pct": float(str(r.get("pnl_pct", "0")).replace("%","").replace(",","").strip() or 0),
                "pnl": round((float(r.get("ltp", 0)) - float(r.get("avg_price", 0))) * int(r.get("qty", 0)), 2),
            })
        total_inv = sum(p["invested"] for p in groww_holdings_list)
        total_pnl = sum(p["pnl"] for p in groww_holdings_list)
        groww_holdings_summary = {
            "count": len(groww_holdings_list),
            "total_invested": round(total_inv, 0),
            "total_pnl": round(total_pnl, 0),
            "total_pnl_pct": round(total_pnl / total_inv * 100, 2) if total_inv else 0,
        }

    groww_shockers = []
    gs = _read("groww_volume_shockers.csv")
    if not gs.empty:
        for _, r in gs.iterrows():
            groww_shockers.append({
                "symbol": str(r["symbol"]),
                "company": str(r.get("company", "")),
                "ltp": float(r.get("ltp", 0)),
                "prev_close": float(r.get("prev_close", 0)),
                "vol_ratio": float(r.get("vol_ratio", 0)),
                "volume": int(r.get("volume", 0)),
                "market_cap_cr": float(r.get("market_cap_cr", 0)),
            })

    groww_gainers = []
    gg = _read("groww_top_gainers.csv")
    if not gg.empty:
        for _, r in gg.iterrows():
            groww_gainers.append({
                "symbol": str(r["symbol"]),
                "company": str(r.get("company", "")),
                "ltp": float(r.get("ltp", 0)),
                "day_change_pct": float(r.get("day_change_pct", 0)),
            })

    groww_analyst = []
    ga = _read("groww_analyst_picks.csv")
    if not ga.empty:
        for _, r in ga.iterrows():
            groww_analyst.append({
                "symbol": str(r["symbol"]),
                "company": str(r.get("company", "")),
                "ltp": float(r.get("ltp") or 0),
                "rating": str(r.get("rating", "")),
            })

    # Coordinator decisions (multi-agent BUY/HOLD/SELL per stock)
    coordinator_decisions = {}
    cd = _read("coordinator_decisions.csv")
    if not cd.empty and "symbol" in cd.columns:
        for _, r in cd.iterrows():
            coordinator_decisions[str(r["symbol"])] = {
                "action": str(r.get("action", "HOLD")),
                "confidence": float(r.get("confidence", 0.5)),
                "size_mult": float(r.get("size_mult", 1.0)),
                "regime": str(r.get("regime", "")),
                "reasons": str(r.get("top_reasons", ""))[:240],
                "updated_at": str(r.get("updated_at", "")),
            }

    snapshot = {
        "ts": datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%H:%M:%S"),
        "date": datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%a, %d %b %Y"),
        "mode": os.getenv("TRADING_MODE", cfg["account"].get("trading_mode", "PAPER")),
        "capital": capital,
        "deployed": deployed,
        "cash": capital - deployed,
        "regime": regime_now,
        "intraday": {
            "realized": realized_pnl,
            "unrealized": open_pnl,
            "total": realized_pnl + open_pnl,
            "n_trades": n_trades,
            "wins": wins,
            "losses": losses,
            "win_rate": win_rate,
            "gross_win": gross_win,
            "gross_loss": gross_loss,
            "profit_factor": profit_factor,
            "open_count": len(positions_list),
            "open_value": intra_value,
        },
        "swing": {
            "count": swing_count,
            "value": swing_value,
            "unrealized": swing_pnl,
            "positions": swing_list,
        },
        "leverage_pct": round(deployed / capital * 100, 1) if capital else 0,
        "positions": positions_list,
        "curve": curve,
        "strategies": strat_breakdown,
        "trade_log": trade_log,
        "movers": movers,
        "events": recent_events,
        "today_plan": {
            "intraday_picks": intraday_picks,
            "swing_picks": swing_picks,
            "catalysts": catalysts_today,
        },
        "groww": {
            "holdings": groww_holdings_list,
            "holdings_summary": groww_holdings_summary,
            "volume_shockers": groww_shockers,
            "top_gainers": groww_gainers,
            "analyst_picks": groww_analyst,
        },
        "coordinator": coordinator_decisions,
    }
    # Strip NaN/Infinity — they break browser JSON.parse()
    return jsonify(_clean_nan(snapshot))


# ============================================================
# THE PREMIUM HTML — single self-contained page
# ============================================================
HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>RDA Trading Terminal</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Inter+Tight:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&family=IBM+Plex+Serif:wght@500;600&display=swap" rel="stylesheet">
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<script src="https://d3js.org/d3.v7.min.js"></script>
<style>
/* RDA TAX ADVISORY brand palette: Navy #0E2A5A · Green #1FE04B · Cream #F5F2E8 */
:root, [data-theme="light"] {
  /* Bright premium background */
  --bg-0: #F5F2E8;
  --bg-1: #FFFFFF;
  --bg-2: #FFFFFF;
  --bg-3: #F0EBD8;
  --border: rgba(14,42,90,0.14);
  --border-bright: rgba(14,42,90,0.24);

  /* Text — high contrast on cream/white */
  --t-1: #0A0A14;
  --t-2: #2A2E40;
  --t-3: #5A5E70;
  --t-4: #9A9EAE;

  /* Brand */
  --brand-navy: #0E2A5A;
  --brand-navy-deep: #0A1F44;
  --brand-green: #1FE04B;
  --brand-green-dark: #14B83B;

  /* Financial — gain green from brand, loss soft red */
  --gain: #14B83B;
  --gain-soft: rgba(31,224,75,0.14);
  --gain-glow: rgba(31,224,75,0.40);

  --loss: #D9434A;
  --loss-soft: rgba(217,67,74,0.10);
  --loss-glow: rgba(217,67,74,0.30);

  --gold: #B8941F;
  --gold-soft: rgba(184,148,31,0.14);

  --cyan: #0E2A5A;   /* secondary actions = navy */
  --violet: #7C5CF0;
  --orange: #EA7C2C;

  --aurora-1: rgba(31,224,75,0.06);
  --aurora-2: rgba(14,42,90,0.04);
  --aurora-3: rgba(184,148,31,0.04);

  --font-ui: 'Inter Tight', 'IBM Plex Sans', -apple-system, system-ui, sans-serif;
  --font-num: 'JetBrains Mono', 'SF Mono', Menlo, monospace;
  --font-serif: 'IBM Plex Serif', Georgia, serif;
}

/* DARK theme — for late-evening sessions */
[data-theme="dark"] {
  --bg-0: #0A1F44;             /* RDA navy deep */
  --bg-1: #0E2A5A;             /* RDA navy */
  --bg-2: #14305F;
  --bg-3: #1A3870;
  --border: rgba(255,255,255,0.08);
  --border-bright: rgba(255,255,255,0.15);
  --t-1: #FFFFFF;
  --t-2: #C5D0E8;
  --t-3: #8294B8;
  --t-4: #56678D;

  --gain: #1FE04B;
  --gain-soft: rgba(31,224,75,0.14);
  --gain-glow: rgba(31,224,75,0.45);

  --loss: #FF5A5F;
  --loss-soft: rgba(255,90,95,0.12);
  --loss-glow: rgba(255,90,95,0.40);

  --gold: #FFD166;
  --gold-soft: rgba(255,209,102,0.12);

  --cyan: #1FE04B;
  --violet: #A78BFA;
  --orange: #FB923C;

  --aurora-1: rgba(31,224,75,0.08);
  --aurora-2: rgba(14,42,90,0.06);
  --aurora-3: rgba(167,139,250,0.04);
}

* { margin: 0; padding: 0; box-sizing: border-box; }
html, body {
  font-family: var(--font-ui);
  background: var(--bg-0);
  color: var(--t-1);
  min-height: 100vh;
  overflow-x: hidden;
  letter-spacing: -0.011em;
  font-feature-settings: 'cv11' 1, 'ss03' 1;
}

/* Aurora background — subtle animated glow blobs */
body::before {
  content: '';
  position: fixed; inset: 0; z-index: 0; pointer-events: none;
  background:
    radial-gradient(800px 500px at 12% 8%, var(--aurora-1), transparent 65%),
    radial-gradient(700px 450px at 88% 12%, var(--aurora-2), transparent 65%),
    radial-gradient(900px 500px at 50% 95%, var(--aurora-3), transparent 70%);
  animation: aurora 24s ease-in-out infinite alternate;
}

/* Theme toggle button */
.theme-toggle {
  background: var(--bg-2); border: 1px solid var(--border);
  color: var(--t-2); border-radius: 6px;
  padding: 7px 14px; cursor: pointer; font-family: var(--font-num);
  font-size: 11px; font-weight: 600; letter-spacing: 0.06em;
  text-transform: uppercase;
  transition: all 0.2s; margin-left: 12px;
}
.theme-toggle:hover {
  background: var(--bg-3); border-color: var(--border-bright);
  color: var(--t-1);
}

/* Leverage bar */
.leverage-bar {
  display: flex; align-items: center; gap: 12px;
  padding: 14px 22px; margin-bottom: 14px;
  background: var(--bg-2); border: 1px solid var(--border);
  border-radius: 10px;
}
.lev-label {
  font-size: 10px; font-weight: 700; text-transform: uppercase;
  letter-spacing: 0.12em; color: var(--t-3); white-space: nowrap;
}
.lev-track {
  flex: 1; height: 8px; background: var(--bg-3); border-radius: 4px;
  position: relative; overflow: hidden;
}
.lev-fill {
  position: absolute; left: 0; top: 0; bottom: 0;
  background: linear-gradient(90deg, var(--gain) 0%, var(--gold) 70%, var(--loss) 100%);
  border-radius: 4px;
  transition: width 0.6s cubic-bezier(0.4,0,0.2,1);
}
.lev-tick {
  position: absolute; top: -3px; bottom: -3px; left: 100%; width: 2px;
  background: var(--t-3); margin-left: -1px;
}
.lev-stats {
  font-family: var(--font-num); font-size: 12px; color: var(--t-2);
  font-variant-numeric: tabular-nums; white-space: nowrap;
}
.lev-pct { font-weight: 700; font-size: 14px; }
.lev-pct.ok { color: var(--gain); }
.lev-pct.warn { color: var(--gold); }
.lev-pct.danger { color: var(--loss); }

/* Swing positions section */
.swing-pos {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 10px;
}

/* === DETAIL MODAL ============================================ */
.modal-overlay {
  position: fixed; inset: 0; background: rgba(10,20,40,0.6);
  z-index: 1000; backdrop-filter: blur(6px);
  display: none; align-items: center; justify-content: center;
  padding: 24px;
}
.modal-overlay.open { display: flex; }
.modal {
  background: var(--bg-1); border-radius: 14px;
  max-width: 800px; width: 100%; max-height: 90vh; overflow-y: auto;
  box-shadow: 0 24px 80px rgba(10,20,40,0.5);
  border: 1px solid var(--border-bright);
}
.modal-header {
  display: flex; justify-content: space-between; align-items: center;
  padding: 18px 24px; border-bottom: 1px solid var(--border);
  background: linear-gradient(180deg, var(--bg-2), var(--bg-1));
  position: sticky; top: 0; z-index: 2;
}
.modal-title {
  font-size: 20px; font-weight: 700; color: var(--brand-navy);
  letter-spacing: -0.02em;
}
.modal-sub {
  font-size: 12px; color: var(--t-3); margin-top: 3px;
}
.modal-close {
  background: var(--bg-3); border: 1px solid var(--border);
  width: 32px; height: 32px; border-radius: 8px;
  font-size: 18px; cursor: pointer; color: var(--t-1);
  display: flex; align-items: center; justify-content: center;
}
.modal-close:hover { background: var(--loss-soft); color: var(--loss); }
.modal-body { padding: 20px 24px; }
.modal-section {
  margin-bottom: 20px;
}
.modal-section-title {
  font-size: 11px; font-weight: 700; letter-spacing: 0.12em;
  text-transform: uppercase; color: var(--brand-navy);
  margin-bottom: 10px;
  padding-bottom: 6px; border-bottom: 2px solid var(--border);
}
.modal-suggestion {
  padding: 14px 18px; border-radius: 10px;
  background: var(--gain-soft);
  border-left: 4px solid var(--gain);
}
.modal-suggestion.warning { background: var(--gold-soft); border-left-color: var(--gold); }
.modal-suggestion.danger { background: var(--loss-soft); border-left-color: var(--loss); }
.modal-suggestion .action {
  font-size: 16px; font-weight: 700; color: var(--t-1); margin-bottom: 4px;
}
.modal-suggestion .why {
  font-size: 13px; color: var(--t-2);
}
.modal-grid {
  display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px;
}
.modal-stat {
  background: var(--bg-2); border: 1px solid var(--border);
  padding: 12px 14px; border-radius: 8px;
}
.modal-stat .label {
  font-size: 9px; font-weight: 700; letter-spacing: 0.1em;
  text-transform: uppercase; color: var(--t-3);
}
.modal-stat .val {
  font-family: var(--font-num); font-size: 18px; font-weight: 600;
  margin-top: 4px; color: var(--t-1);
  font-variant-numeric: tabular-nums;
}
.modal-stat .val.gain { color: var(--gain); }
.modal-stat .val.loss { color: var(--loss); }
.modal-table {
  width: 100%; font-size: 12px; border-collapse: collapse;
}
.modal-table th {
  text-align: left; padding: 8px 10px;
  font-size: 10px; font-weight: 700; text-transform: uppercase; letter-spacing: 0.08em;
  color: var(--t-3); border-bottom: 1px solid var(--border);
}
.modal-table td {
  padding: 8px 10px; border-bottom: 1px dashed var(--border);
  color: var(--t-2);
}
.modal-table tr:last-child td { border-bottom: none; }
.modal-table td.mono { font-family: var(--font-num); color: var(--t-1); font-variant-numeric: tabular-nums; }
.modal-table td.gain { color: var(--gain); font-weight: 600; }
.modal-table td.loss { color: var(--loss); font-weight: 600; }
.signal-card {
  background: var(--bg-2); border: 1px solid var(--border);
  border-left: 3px solid var(--brand-navy);
  padding: 10px 14px; margin-bottom: 8px; border-radius: 0 8px 8px 0;
}
.signal-header {
  display: flex; justify-content: space-between; margin-bottom: 4px;
}
.signal-strat {
  font-family: var(--font-num); font-size: 11px; font-weight: 700;
  color: var(--brand-navy); text-transform: uppercase; letter-spacing: 0.05em;
}
.signal-time { font-family: var(--font-num); font-size: 10px; color: var(--t-3); }
.signal-reason { font-size: 12px; color: var(--t-2); line-height: 1.5; }
.signal-prices {
  display: flex; gap: 14px; margin-top: 6px;
  font-family: var(--font-num); font-size: 11px; color: var(--t-3);
}
.clickable {
  cursor: pointer; transition: transform 0.15s, box-shadow 0.15s;
}
.clickable:hover {
  transform: translateY(-1px);
  box-shadow: 0 4px 14px rgba(14,42,90,0.10);
}

/* === NEWS PANEL — per-stock headlines ============================ */
.news-grid {
  display: grid; grid-template-columns: repeat(auto-fill, minmax(320px, 1fr));
  gap: 12px;
}
.news-card {
  background: var(--bg-1); border: 1px solid var(--border);
  border-radius: 10px; padding: 14px 16px;
  position: relative; overflow: hidden;
}
.news-card::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: var(--brand-navy, var(--cyan));
}
.news-card.positive::before { background: var(--gain); }
.news-card.negative::before { background: var(--loss); }
.news-header {
  display: flex; justify-content: space-between; align-items: baseline;
  margin-bottom: 8px; padding-bottom: 8px;
  border-bottom: 1px solid var(--border);
}
.news-sym {
  font-size: 14px; font-weight: 700; color: var(--t-1);
  letter-spacing: -0.01em;
}
.news-sentiment {
  font-family: var(--font-num); font-size: 10px; font-weight: 600;
  padding: 2px 8px; border-radius: 3px;
}
.news-sentiment.positive { background: var(--gain-soft); color: var(--gain); }
.news-sentiment.negative { background: var(--loss-soft); color: var(--loss); }
.news-sentiment.neutral { background: var(--bg-3); color: var(--t-2); }
.news-tags {
  display: flex; gap: 4px; flex-wrap: wrap; margin-bottom: 8px;
}
.news-tag {
  font-family: var(--font-num); font-size: 9px; font-weight: 600;
  padding: 1px 6px; border-radius: 3px;
  background: var(--bg-3); color: var(--t-2);
  text-transform: uppercase; letter-spacing: 0.04em;
}
.news-item {
  font-size: 11px; color: var(--t-2); line-height: 1.45;
  padding: 6px 0; border-bottom: 1px dashed var(--border);
  display: flex; gap: 8px;
}
.news-item:last-child { border-bottom: none; }
.news-item a {
  color: var(--t-1); text-decoration: none;
  transition: color 0.15s;
}
.news-item a:hover { color: var(--brand-navy, var(--cyan)); }
.news-bullet {
  flex-shrink: 0; width: 4px; height: 4px;
  background: var(--brand-green, var(--gain));
  border-radius: 50%; margin-top: 7px;
}
.news-source {
  font-family: var(--font-num); font-size: 9px;
  color: var(--t-3); margin-top: 2px;
}
.news-footer {
  margin-top: 8px; padding-top: 8px;
  border-top: 1px solid var(--border);
  display: flex; gap: 8px; font-size: 10px;
}
.news-footer a {
  color: var(--brand-navy, var(--cyan)); text-decoration: none;
  font-weight: 600;
}
.news-footer a:hover { text-decoration: underline; }

/* === TODAY'S TRADING PLAN — the main "what to watch" panel ===== */
.plan-bento {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 12px;
  margin-bottom: 18px;
}
.plan-panel {
  background: var(--bg-1);
  border: 1px solid var(--border);
  border-radius: 12px;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(14,42,90,0.04);
}
.plan-head {
  padding: 14px 18px 12px;
  border-bottom: 2px solid var(--border);
  background: linear-gradient(180deg, var(--bg-2), var(--bg-1));
}
.plan-head h3 {
  font-size: 13px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.1em;
  color: var(--brand-navy, var(--t-1));
  margin: 0;
}
.plan-head .meta {
  font-family: var(--font-num); font-size: 10px;
  color: var(--t-3); margin-top: 2px;
}
.plan-body { padding: 6px 0; max-height: 380px; overflow-y: auto; }
.plan-row {
  display: grid; grid-template-columns: 28px 1fr auto;
  gap: 10px; padding: 8px 16px;
  align-items: center;
  border-bottom: 1px dashed var(--border);
  transition: background 0.15s;
}
.plan-row:hover { background: var(--bg-3); }
.plan-row:last-child { border-bottom: none; }
.plan-rank {
  font-family: var(--font-num); font-size: 11px;
  font-weight: 700; color: var(--brand-navy, var(--t-3));
  text-align: center;
}
.plan-sym {
  font-size: 13px; font-weight: 700;
  color: var(--t-1); letter-spacing: -0.01em;
}
.plan-co {
  font-size: 10px; color: var(--t-3);
  margin-top: 1px;
  white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  max-width: 200px;
}
.plan-meta-cell {
  font-family: var(--font-num); font-size: 11px;
  text-align: right; color: var(--t-2);
  font-variant-numeric: tabular-nums;
  white-space: nowrap;
}
.plan-meta-cell .gain { color: var(--gain); font-weight: 600; }
.plan-meta-cell .loss { color: var(--loss); font-weight: 600; }
.plan-grade {
  display: inline-block;
  padding: 1px 6px; border-radius: 3px;
  font-family: var(--font-num); font-size: 9px;
  font-weight: 700; letter-spacing: 0.05em;
}
.plan-grade.A, .plan-grade.A\+ {
  background: var(--gain-soft); color: var(--gain);
}
.plan-grade.B {
  background: var(--gold-soft); color: var(--gold);
}
.plan-grade.C, .plan-grade.D {
  background: var(--loss-soft); color: var(--loss);
}
.plan-impact {
  display: inline-block;
  width: 22px; height: 22px;
  border-radius: 50%;
  background: var(--brand-navy, var(--cyan));
  color: white;
  font-family: var(--font-num); font-size: 10px; font-weight: 700;
  text-align: center; line-height: 22px;
}
.plan-impact.high { background: var(--gain); }
.plan-impact.medium { background: var(--gold); color: var(--t-1); }
.plan-impact.low { background: var(--t-3); }

@media (max-width: 1100px) {
  .plan-bento { grid-template-columns: 1fr; }
}

/* Strong contrast for KPI values in light mode */
[data-theme="light"] .kpi-value { color: var(--t-1); }
[data-theme="light"] .pos-sym { color: var(--t-1); }
[data-theme="light"] .pos-co { color: var(--t-3); }
[data-theme="light"] .pos-cell-v { color: var(--t-1); }
[data-theme="light"] .topbar-stat-value,
[data-theme="light"] .tstat-value { color: var(--t-1); }
[data-theme="light"] .brand-name { color: var(--brand-navy); }
[data-theme="light"] .panel-title { color: var(--brand-navy); font-weight: 700; }
[data-theme="light"] .section-title h2 { color: var(--brand-navy); }
[data-theme="light"] .empty { color: var(--t-3); }
[data-theme="light"] .hero-value.neutral { color: var(--brand-navy); }
[data-theme="light"] .event-text { color: var(--t-2); }
[data-theme="light"] .hero-meta-item .label { color: var(--t-3); }
[data-theme="light"] .hero-meta-item .val { color: var(--t-1); }
[data-theme="light"] .strat-name { color: var(--t-2); }
[data-theme="light"] .trade-meta { color: var(--t-3); }
[data-theme="light"] .trade-sym { color: var(--t-1); }
@keyframes aurora {
  0%   { transform: translate3d(0,0,0) scale(1); opacity: 0.85; }
  100% { transform: translate3d(20px,30px,0) scale(1.05); opacity: 1; }
}

/* Page grid */
.page {
  position: relative; z-index: 1;
  max-width: 1480px; margin: 0 auto;
  padding: 18px 24px 80px 24px;
}

/* === TOP BAR — RDA brand strip ============================ */
.topbar {
  position: relative;
  display: flex; justify-content: space-between; align-items: center;
  padding: 18px 26px; margin-bottom: 18px;
  background: var(--bg-1);
  border: 1px solid var(--border);
  border-radius: 12px;
  box-shadow: 0 1px 0 rgba(255,255,255,0.5) inset, 0 4px 20px rgba(14,42,90,0.06);
  overflow: hidden;
}
/* Navy stripe at top of topbar — RDA signature */
.topbar::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 4px;
  background: linear-gradient(90deg,
    var(--brand-navy, #0E2A5A) 0%,
    var(--brand-navy, #0E2A5A) 60%,
    var(--brand-green, #1FE04B) 100%);
}
.brand { display: flex; align-items: center; gap: 16px; }
.brand-mark {
  width: 42px; height: 42px;
  background: linear-gradient(135deg, var(--brand-navy, #0E2A5A) 0%, var(--brand-navy-deep, #0A1F44) 100%);
  border-radius: 8px;
  display: grid; place-items: center;
  font-family: var(--font-display, 'Inter Tight'); font-weight: 800;
  color: #FFFFFF; font-size: 18px;
  letter-spacing: -0.04em;
  box-shadow: 0 4px 14px rgba(14,42,90,0.30), 0 0 0 1px rgba(14,42,90,0.15);
  position: relative;
}
.brand-mark::after {
  content: ''; position: absolute; right: -3px; bottom: -3px;
  width: 12px; height: 12px;
  background: var(--brand-green, #1FE04B);
  border-radius: 50%;
  border: 2px solid var(--bg-1);
  box-shadow: 0 0 8px rgba(31,224,75,0.5);
}
.brand-name {
  font-size: 18px; font-weight: 700; letter-spacing: -0.02em;
  color: var(--brand-navy, var(--t-1));
}
.brand-sub {
  font-family: var(--font-num); font-size: 11px;
  color: var(--t-3); margin-top: 3px;
  letter-spacing: 0.02em;
}
.brand-mark span.green-dot {
  color: var(--brand-green);
}

.topbar-stats { display: flex; gap: 32px; align-items: center; }
.tstat-label {
  font-size: 9px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.14em;
  color: var(--t-3);
}
.tstat-value {
  font-family: var(--font-num); font-size: 16px;
  font-weight: 600; margin-top: 3px;
  font-variant-numeric: tabular-nums;
}
.live-dot {
  display: inline-block; width: 6px; height: 6px;
  background: var(--gain); border-radius: 50%;
  margin-right: 6px;
  box-shadow: 0 0 8px var(--gain-glow);
  animation: pulse 1.8s ease-in-out infinite;
}
@keyframes pulse {
  0%,100% { opacity: 0.5; transform: scale(1); }
  50%     { opacity: 1; transform: scale(1.4); }
}

.pill {
  display: inline-flex; align-items: center; gap: 5px;
  padding: 3px 10px; border-radius: 4px;
  font-family: var(--font-num); font-size: 10px;
  font-weight: 600; text-transform: uppercase;
  letter-spacing: 0.08em;
}
.pill.paper { background: rgba(167,139,250,0.12); color: var(--violet); }
.pill.live  { background: var(--gain-soft); color: var(--gain); }
.pill.gain  { background: var(--gain-soft); color: var(--gain); }
.pill.loss  { background: var(--loss-soft); color: var(--loss); }
.pill.gold  { background: var(--gold-soft); color: var(--gold); }
.pill.info  { background: rgba(6,182,212,0.12); color: var(--cyan); }

/* === HERO P&L ============================================ */
.hero {
  position: relative;
  padding: 38px 42px 30px;
  margin-bottom: 18px;
  background:
    radial-gradient(1000px 300px at 25% 0%, var(--hero-glow, rgba(16,217,135,0.06)), transparent 65%),
    linear-gradient(180deg, var(--bg-2) 0%, var(--bg-1) 100%);
  border: 1px solid var(--border-bright);
  border-radius: 16px;
  overflow: hidden;
  backdrop-filter: blur(12px);
  box-shadow: 0 1px 0 rgba(255,255,255,0.04) inset, 0 16px 48px rgba(0,0,0,0.4);
}
.hero::before {
  content: ''; position: absolute; top: 0; left: 0; right: 0; height: 1px;
  background: linear-gradient(90deg, transparent, var(--hero-glow-strong, rgba(16,217,135,0.5)), transparent);
}
.hero-eyebrow {
  display: flex; align-items: center; gap: 8px;
  font-size: 10px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.16em;
  color: var(--t-3);
}
.hero-value {
  font-family: var(--font-num); font-size: 64px;
  font-weight: 700; line-height: 1; letter-spacing: -0.035em;
  margin: 16px 0 10px 0;
  font-variant-numeric: tabular-nums;
  text-shadow: 0 0 60px var(--hero-glow-strong, rgba(16,217,135,0.3));
}
.hero-value.gain { color: var(--gain); }
.hero-value.loss { color: var(--loss); }
.hero-value.neutral { color: var(--t-1); }
.hero-meta {
  display: flex; gap: 28px; flex-wrap: wrap;
  margin-top: 18px; padding-top: 18px;
  border-top: 1px solid var(--border);
}
.hero-meta-item .label {
  font-size: 9px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.14em;
  color: var(--t-3);
}
.hero-meta-item .val {
  font-family: var(--font-num); font-size: 17px;
  font-weight: 600; margin-top: 4px;
  font-variant-numeric: tabular-nums;
}
.hero-meta-item .val.gain { color: var(--gain); }
.hero-meta-item .val.loss { color: var(--loss); }

/* === KPI Strip ========================================== */
.kpi-grid {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 12px; margin-bottom: 18px;
}
.kpi {
  padding: 18px 20px;
  background: linear-gradient(180deg, var(--bg-2), var(--bg-1));
  border: 1px solid var(--border); border-radius: 12px;
  transition: transform 0.25s, border-color 0.25s, box-shadow 0.25s;
  position: relative; overflow: hidden;
  cursor: pointer;
}
.kpi:hover {
  transform: translateY(-2px);
  border-color: var(--border-bright);
  box-shadow: 0 12px 36px rgba(0,0,0,0.4);
}
.kpi::after {
  content: '↗'; position: absolute; top: 14px; right: 14px;
  color: var(--t-4); font-size: 14px; opacity: 0; transition: opacity 0.2s;
}
.kpi:hover::after { opacity: 1; }
.kpi-label {
  font-size: 10px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.14em;
  color: var(--t-3);
}
.kpi-value {
  font-family: var(--font-num); font-size: 26px;
  font-weight: 600; margin-top: 10px;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}
.kpi-value.gain { color: var(--gain); }
.kpi-value.loss { color: var(--loss); }
.kpi-value.gold { color: var(--gold); }
.kpi-sub {
  font-family: var(--font-num); font-size: 11px;
  color: var(--t-2); margin-top: 6px;
  font-variant-numeric: tabular-nums;
}

/* === BENTO GRID ========================================= */
.bento {
  display: grid;
  grid-template-columns: 1.4fr 1fr;
  gap: 14px; margin-bottom: 18px;
}
.panel {
  background: var(--bg-1);
  border: 1px solid var(--border); border-radius: 14px;
  backdrop-filter: blur(12px);
  overflow: hidden;
}
.panel-head {
  display: flex; justify-content: space-between; align-items: center;
  padding: 14px 20px; border-bottom: 1px solid var(--border);
}
.panel-title {
  font-size: 11px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.14em;
  color: var(--t-2);
}
.panel-meta {
  font-family: var(--font-num); font-size: 11px; color: var(--t-3);
}
.panel-body { padding: 16px 20px; }

/* === POSITION CARDS ===================================== */
.pos-grid { display: grid; gap: 10px; }
.pos {
  position: relative; padding: 16px 18px;
  background: var(--bg-2);
  border: 1px solid var(--border); border-radius: 10px;
  transition: all 0.25s;
  overflow: hidden;
}
.pos::before {
  content: ''; position: absolute; left: 0; top: 0; bottom: 0; width: 3px;
  background: var(--t-3); transition: all 0.25s;
}
.pos.winning::before { background: var(--gain); box-shadow: 0 0 12px var(--gain-glow); }
.pos.losing::before  { background: var(--loss); box-shadow: 0 0 12px var(--loss-glow); }
.pos.near_tgt::before { background: var(--gain); box-shadow: 0 0 18px var(--gain-glow); }
.pos.near_sl::before { background: var(--loss); box-shadow: 0 0 18px var(--loss-glow); }
.pos:hover {
  transform: translateX(2px);
  border-color: var(--border-bright);
  background: var(--bg-3);
}
.pos-row1 {
  display: flex; justify-content: space-between; align-items: flex-start;
  margin-bottom: 8px;
}
.pos-sym {
  font-size: 17px; font-weight: 700; letter-spacing: -0.02em;
}
.pos-co {
  font-size: 11px; color: var(--t-3); margin-top: 2px;
}
.pos-pnl {
  font-family: var(--font-num); font-size: 20px;
  font-weight: 700; line-height: 1; text-align: right;
  font-variant-numeric: tabular-nums;
  letter-spacing: -0.02em;
}
.pos-pnl.gain { color: var(--gain); }
.pos-pnl.loss { color: var(--loss); }
.pos-pnl-pct {
  font-family: var(--font-num); font-size: 11px;
  font-weight: 500; margin-top: 4px; text-align: right;
}
.pos-pnl-pct.gain { color: var(--gain); }
.pos-pnl-pct.loss { color: var(--loss); }
.pos-tags { display: flex; gap: 6px; margin-bottom: 10px; }
.pos-strat-pill {
  font-family: var(--font-num); font-size: 9px;
  font-weight: 600; padding: 2px 7px; border-radius: 3px;
  background: rgba(6,182,212,0.1); color: var(--cyan);
  text-transform: uppercase; letter-spacing: 0.08em;
}
.pos-time-pill {
  font-family: var(--font-num); font-size: 9px;
  color: var(--t-3); padding: 2px 0;
}
.pos-bar {
  position: relative; height: 5px;
  background: var(--bg-3); border-radius: 3px;
  margin: 8px 0; overflow: hidden;
}
.pos-bar-fill {
  position: absolute; top: 0; left: 0; height: 100%;
  border-radius: 3px;
  transition: width 0.5s cubic-bezier(0.4,0,0.2,1);
}
.pos-bar-fill.gain { background: linear-gradient(90deg, var(--gain), var(--cyan)); }
.pos-bar-fill.loss { background: linear-gradient(90deg, var(--loss), var(--orange)); }
.pos-bar-tick {
  position: absolute; top: -2px; bottom: -2px; width: 1px;
  background: var(--t-2); opacity: 0.4;
}
.pos-prices {
  display: flex; justify-content: space-between;
  font-family: var(--font-num); font-size: 10px;
  color: var(--t-3); font-variant-numeric: tabular-nums;
}
.pos-grid4 {
  display: grid; grid-template-columns: repeat(4, 1fr);
  gap: 12px; margin-top: 12px; padding-top: 12px;
  border-top: 1px solid var(--border);
}
.pos-cell-l {
  font-size: 9px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.1em;
  color: var(--t-3);
}
.pos-cell-v {
  font-family: var(--font-num); font-size: 12px;
  font-weight: 600; margin-top: 3px;
  font-variant-numeric: tabular-nums;
}

/* === Strategy Donut ===================================== */
#donut-container { display: grid; place-items: center; padding: 8px; }
.donut-center {
  position: absolute; text-align: center;
  pointer-events: none;
}
.donut-center .v {
  font-family: var(--font-num); font-size: 22px;
  font-weight: 700; letter-spacing: -0.02em;
}
.donut-center .l {
  font-size: 9px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.14em;
  color: var(--t-3); margin-top: 4px;
}
.strat-legend { padding: 0 12px 12px; }
.strat-row {
  display: flex; align-items: center; justify-content: space-between;
  padding: 6px 0; font-size: 12px;
  border-bottom: 1px dashed var(--border);
}
.strat-row:last-child { border-bottom: none; }
.strat-dot {
  display: inline-block; width: 8px; height: 8px;
  border-radius: 2px; margin-right: 8px;
}
.strat-name {
  font-family: var(--font-num); font-size: 11px;
  color: var(--t-2); font-weight: 500;
}
.strat-pnl {
  font-family: var(--font-num); font-size: 12px;
  font-weight: 600; font-variant-numeric: tabular-nums;
}

/* === Trade log ========================================== */
.trade-row {
  display: grid;
  grid-template-columns: 50px 1fr 90px 80px;
  gap: 12px; padding: 10px 12px;
  border-bottom: 1px dashed var(--border);
  font-size: 12px; align-items: center;
  transition: background 0.15s;
}
.trade-row:hover { background: var(--bg-2); }
.trade-row:last-child { border-bottom: none; }
.trade-time {
  font-family: var(--font-num); font-size: 11px; color: var(--t-3);
}
.trade-sym { font-weight: 600; }
.trade-meta { font-size: 10px; color: var(--t-3); margin-top: 1px; }
.trade-pnl {
  font-family: var(--font-num); font-size: 13px;
  font-weight: 600; text-align: right;
  font-variant-numeric: tabular-nums;
}
.trade-pnl.gain { color: var(--gain); }
.trade-pnl.loss { color: var(--loss); }
.trade-reason {
  font-family: var(--font-num); font-size: 9px;
  text-align: right; color: var(--t-3);
  text-transform: uppercase; letter-spacing: 0.05em;
}

/* === Events feed ======================================== */
.event-row {
  display: flex; gap: 12px;
  padding: 10px 12px;
  border-left: 2px solid var(--cyan);
  background: var(--bg-2);
  border-radius: 0 6px 6px 0;
  margin-bottom: 6px;
  font-size: 12px;
}
.event-row.success { border-left-color: var(--gain); }
.event-row.warning { border-left-color: var(--orange); }
.event-row.error { border-left-color: var(--loss); }
.event-time {
  font-family: var(--font-num); font-size: 10px;
  color: var(--t-4); white-space: nowrap;
}
.event-action {
  font-family: var(--font-num); font-size: 10px;
  color: var(--cyan); font-weight: 600;
  margin-right: 6px;
}
.event-text { color: var(--t-2); }
.event-sym { color: var(--gold); font-family: var(--font-num); font-weight: 600; }

/* === Live movers ticker ================================= */
.movers-strip {
  display: flex; gap: 8px; overflow-x: auto;
  padding: 12px 20px;
  border-top: 1px solid var(--border);
}
.mover-chip {
  flex-shrink: 0;
  padding: 8px 12px; border-radius: 8px;
  background: var(--bg-3);
  border: 1px solid var(--border);
  font-family: var(--font-num); font-size: 11px;
  min-width: 110px;
}
.mover-sym { font-weight: 700; font-size: 12px; letter-spacing: -0.01em; }
.mover-pct { color: var(--gain); font-size: 11px; margin-top: 2px; }
.mover-vol { color: var(--t-3); font-size: 10px; margin-top: 1px; }

/* === Section title ====================================== */
.section-title {
  display: flex; align-items: center; gap: 10px;
  margin: 24px 0 10px 4px;
}
.section-title h2 {
  font-size: 13px; font-weight: 700;
  text-transform: uppercase; letter-spacing: 0.14em;
  color: var(--t-2);
}
.section-title::before {
  content: ''; width: 3px; height: 14px;
  background: var(--gold); border-radius: 1px;
}

/* === Empty state ======================================== */
.empty {
  padding: 32px; text-align: center;
  color: var(--t-3); font-size: 13px;
}
.empty::before {
  content: '∅'; display: block;
  font-size: 36px; color: var(--t-4); margin-bottom: 8px;
  font-family: var(--font-serif);
}

/* === P&L Curve Chart ==================================== */
#pnl-chart-wrap {
  padding: 8px 12px 12px; height: 200px;
}

@media (max-width: 900px) {
  .bento { grid-template-columns: 1fr; }
  .kpi-grid { grid-template-columns: repeat(2, 1fr); }
  .hero-value { font-size: 44px; }
}
</style>
</head>
<body>
<div class="page">
  <!-- TOP BAR -->
  <div class="topbar">
    <div class="brand">
      <div class="brand-mark">RDA</div>
      <div>
        <div class="brand-name">Trading Terminal</div>
        <div class="brand-sub" id="brand-sub">Loading…</div>
      </div>
    </div>
    <div class="topbar-stats">
      <div>
        <div class="tstat-label">Regime</div>
        <div class="tstat-value" id="t-regime">—</div>
      </div>
      <div>
        <div class="tstat-label">Capital</div>
        <div class="tstat-value" id="t-capital">—</div>
      </div>
      <div>
        <div class="tstat-label">Deployed</div>
        <div class="tstat-value" id="t-deployed">—</div>
      </div>
      <div>
        <div class="tstat-label"><span class="live-dot"></span>Live</div>
        <div class="tstat-value" id="t-time">—</div>
      </div>
      <button class="theme-toggle" id="theme-toggle">☀ Light</button>
    </div>
  </div>

  <!-- LEVERAGE BAR -->
  <div class="leverage-bar">
    <span class="lev-label">Capital Deployment</span>
    <div class="lev-track">
      <div class="lev-fill" id="lev-fill" style="width: 0%;"></div>
      <div class="lev-tick" id="lev-tick"></div>
    </div>
    <span class="lev-stats">
      <span class="lev-pct" id="lev-pct">0%</span>
      &nbsp;&nbsp;<span id="lev-deployed">₹0</span> / <span id="lev-capital">₹0</span>
    </span>
  </div>

  <!-- DETAIL MODAL — populated dynamically -->
  <div id="modal-overlay" class="modal-overlay" onclick="closeModal(event)">
    <div class="modal" onclick="event.stopPropagation()">
      <div class="modal-header">
        <div>
          <div class="modal-title" id="modal-title">—</div>
          <div class="modal-sub" id="modal-sub">—</div>
        </div>
        <button class="modal-close" onclick="closeModal()">×</button>
      </div>
      <div class="modal-body" id="modal-body">Loading…</div>
    </div>
  </div>

  <!-- TODAY'S TRADING PLAN — what bot is watching for you -->
  <div class="plan-bento">
    <div class="plan-panel">
      <div class="plan-head">
        <h3>⚡ Today's Intraday Picks</h3>
        <div class="meta">Pre-market scan · top 10 ranked by gap × volume</div>
      </div>
      <div class="plan-body" id="intra-picks-body">
        <div class="empty" style="padding: 20px;">No pre-market picks yet. Scan fires at 08:45.</div>
      </div>
    </div>
    <div class="plan-panel">
      <div class="plan-head">
        <h3>💎 Swing Watchlist</h3>
        <div class="meta">Latest scan · Fortress / VCP / Catalyst signals</div>
      </div>
      <div class="plan-body" id="swing-picks-body">
        <div class="empty" style="padding: 20px;">No swing candidates from last scan.</div>
      </div>
    </div>
    <div class="plan-panel">
      <div class="plan-head">
        <h3>📅 Upcoming Catalysts</h3>
        <div class="meta">Earnings · buybacks · dividends · board meetings</div>
      </div>
      <div class="plan-body" id="catalysts-body">
        <div class="empty" style="padding: 20px;">No catalysts loaded.</div>
      </div>
    </div>
  </div>

  <!-- GROWW REAL PORTFOLIO (NEW) -->
  <div class="panel" id="groww-holdings-panel" style="margin-bottom: 18px; display: none;">
    <div class="panel-head" style="background: linear-gradient(90deg, rgba(31,224,75,0.08), rgba(14,42,90,0.04));">
      <div>
        <div class="panel-title" style="color: var(--brand-navy);">💼 My Real Groww Portfolio</div>
        <div class="panel-meta" id="groww-holdings-meta">—</div>
      </div>
      <div style="text-align: right;">
        <div style="font-family: var(--font-num); font-size: 22px; font-weight: 700;" id="groww-total-pnl">—</div>
        <div style="font-family: var(--font-num); font-size: 11px; color: var(--t-3);" id="groww-total-pct">—</div>
      </div>
    </div>
    <div class="panel-body">
      <div id="groww-holdings-grid" class="swing-pos"></div>
    </div>
  </div>

  <!-- GROWW LIVE INSIGHTS — Volume Shockers + Top Gainers + Analyst Picks (NEW) -->
  <div class="plan-bento" id="groww-insights" style="display: none;">
    <div class="plan-panel">
      <div class="plan-head" style="border-bottom-color: var(--brand-green);">
        <h3 style="color: var(--brand-green);">🔥 Groww Volume Shockers</h3>
        <div class="meta">Live · ranked by vol/avg ratio</div>
      </div>
      <div class="plan-body" id="groww-shockers-body"></div>
    </div>
    <div class="plan-panel">
      <div class="plan-head">
        <h3 style="color: var(--brand-navy);">📈 Live Top Gainers</h3>
        <div class="meta">NSE · sorted by day %</div>
      </div>
      <div class="plan-body" id="groww-gainers-body"></div>
    </div>
    <div class="plan-panel">
      <div class="plan-head" style="background: linear-gradient(180deg, var(--gold-soft), transparent);">
        <h3 style="color: var(--gold);">⭐ 100% Buy (Analyst)</h3>
        <div class="meta">Consensus picks via Groww</div>
      </div>
      <div class="plan-body" id="groww-analyst-body"></div>
    </div>
  </div>

  <!-- HERO P&L -->
  <div class="hero" id="hero">
    <div class="hero-eyebrow">
      <span class="live-dot"></span>
      Today's Intraday Book · Realized + Open
    </div>
    <div class="hero-value neutral" id="hero-val">₹ 0</div>
    <div class="hero-meta">
      <div class="hero-meta-item">
        <div class="label">Realized</div>
        <div class="val" id="hero-realized">₹ 0</div>
      </div>
      <div class="hero-meta-item">
        <div class="label">Unrealized</div>
        <div class="val" id="hero-unrealized">₹ 0</div>
      </div>
      <div class="hero-meta-item">
        <div class="label">Trades</div>
        <div class="val" id="hero-trades">0</div>
      </div>
      <div class="hero-meta-item">
        <div class="label">Open Positions</div>
        <div class="val" id="hero-open">0</div>
      </div>
      <div class="hero-meta-item">
        <div class="label">Avg / Trade</div>
        <div class="val" id="hero-avg">₹ 0</div>
      </div>
    </div>
  </div>

  <!-- KPI STRIP -->
  <div class="kpi-grid">
    <div class="kpi">
      <div class="kpi-label">Win Rate</div>
      <div class="kpi-value" id="kpi-wr">—</div>
      <div class="kpi-sub" id="kpi-wr-sub">— W / — L</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Gross Profit</div>
      <div class="kpi-value gain" id="kpi-gp">₹ 0</div>
      <div class="kpi-sub" id="kpi-gp-sub">winners</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Gross Loss</div>
      <div class="kpi-value loss" id="kpi-gl">₹ 0</div>
      <div class="kpi-sub" id="kpi-gl-sub">losers</div>
    </div>
    <div class="kpi">
      <div class="kpi-label">Profit Factor</div>
      <div class="kpi-value gold" id="kpi-pf">—</div>
      <div class="kpi-sub" id="kpi-pf-sub">—</div>
    </div>
  </div>

  <!-- P&L CURVE -->
  <div class="panel" style="margin-bottom: 18px;">
    <div class="panel-head">
      <div class="panel-title">Intraday P&L Curve</div>
      <div class="panel-meta" id="curve-meta">—</div>
    </div>
    <div id="pnl-chart-wrap">
      <canvas id="pnl-chart"></canvas>
    </div>
  </div>

  <!-- BENTO: positions + strategy -->
  <div class="bento">
    <div class="panel">
      <div class="panel-head">
        <div class="panel-title">📍 Open Positions · Auto-exit 15:15</div>
        <div class="panel-meta" id="pos-meta">0 holding</div>
      </div>
      <div class="panel-body">
        <div id="pos-grid" class="pos-grid">
          <div class="empty">No open intraday positions.<br><small>Scanners running every 2-3 min — waiting for signal.</small></div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">
        <div class="panel-title">🎯 Strategy Attribution</div>
        <div class="panel-meta" id="strat-meta">today</div>
      </div>
      <div id="donut-container" style="position:relative; height: 240px;">
        <svg id="donut" width="200" height="200"></svg>
        <div class="donut-center">
          <div class="v" id="donut-v">₹0</div>
          <div class="l">net today</div>
        </div>
      </div>
      <div class="strat-legend" id="strat-legend">
        <div class="empty" style="padding: 16px;">No strategy data yet.</div>
      </div>
    </div>
  </div>

  <!-- BENTO: trade log + activity feed -->
  <div class="bento">
    <div class="panel">
      <div class="panel-head">
        <div class="panel-title">📜 Recent Trades</div>
        <div class="panel-meta" id="trades-meta">today</div>
      </div>
      <div class="panel-body" style="padding: 0; max-height: 420px; overflow-y: auto;">
        <div id="trade-log">
          <div class="empty">No trades booked today yet.</div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head">
        <div class="panel-title">⚡ Agent Activity</div>
        <div class="panel-meta">live feed</div>
      </div>
      <div class="panel-body" style="max-height: 420px; overflow-y: auto;">
        <div id="events-feed">
          <div class="empty">No events yet.</div>
        </div>
      </div>
    </div>
  </div>

  <!-- SWING POSITIONS -->
  <div class="panel" style="margin-top: 14px;">
    <div class="panel-head">
      <div class="panel-title">💎 Swing Positions · Multi-day Holdings</div>
      <div class="panel-meta" id="swing-meta">0 holding</div>
    </div>
    <div class="panel-body">
      <div id="swing-grid" class="swing-pos">
        <div class="empty">No swing positions.</div>
      </div>
    </div>
  </div>

  <!-- NEWS PER STOCK — for every holding + every shortlisted pick -->
  <div class="panel" style="margin-top: 14px;">
    <div class="panel-head">
      <div class="panel-title">📰 News on Your Stocks &amp; Watchlist</div>
      <div class="panel-meta" id="news-meta">live feed</div>
    </div>
    <div class="panel-body">
      <div id="news-grid" class="news-grid">
        <div class="empty">Loading news…</div>
      </div>
    </div>
  </div>

  <!-- LIVE MOVERS -->
  <div class="panel" style="margin-top: 14px;">
    <div class="panel-head">
      <div class="panel-title">🔥 Live Breakout Watchlist</div>
      <div class="panel-meta">updated every 3 min</div>
    </div>
    <div class="movers-strip" id="movers-strip">
      <div class="empty" style="padding: 16px;">No breakout candidates right now.</div>
    </div>
  </div>
</div>

<script>
const fmt = n => "₹ " + Math.abs(n).toLocaleString('en-IN', {maximumFractionDigits: 0});
const fmtSign = n => (n > 0 ? "+" : n < 0 ? "−" : "") + fmt(n);
const fmtPct = n => (n > 0 ? "+" : "") + n.toFixed(2) + "%";

const STRAT_COLORS = {
  live_breakout: '#10d987',
  intraday_momentum: '#06b6d4',
  vwap_pullback: '#a78bfa',
  range_breakout: '#fb923c',
  scalp_orb: '#ffd166',
  volume_spike_reversal: '#f472b6',
  sector_rotation: '#22c55e',
  eod_squeeze: '#facc15',
};
const stratColor = s => STRAT_COLORS[s] || '#94a3b8';

let pnlChart = null;

function renderHero(d) {
  const intra = d.intraday;
  const t = intra.total;
  const cls = t > 0 ? 'gain' : t < 0 ? 'loss' : 'neutral';
  const sign = t > 0 ? '+' : t < 0 ? '−' : '';
  document.getElementById('hero-val').className = 'hero-value ' + cls;
  document.getElementById('hero-val').textContent = sign + fmt(t);

  // Hero glow color
  const hero = document.getElementById('hero');
  if (t > 0) {
    hero.style.setProperty('--hero-glow', 'rgba(16,217,135,0.08)');
    hero.style.setProperty('--hero-glow-strong', 'rgba(16,217,135,0.45)');
  } else if (t < 0) {
    hero.style.setProperty('--hero-glow', 'rgba(255,90,95,0.08)');
    hero.style.setProperty('--hero-glow-strong', 'rgba(255,90,95,0.45)');
  } else {
    hero.style.setProperty('--hero-glow', 'rgba(148,163,184,0.05)');
    hero.style.setProperty('--hero-glow-strong', 'rgba(148,163,184,0.2)');
  }

  const realized = document.getElementById('hero-realized');
  realized.textContent = fmtSign(intra.realized);
  realized.className = 'val ' + (intra.realized > 0 ? 'gain' : intra.realized < 0 ? 'loss' : '');

  const unr = document.getElementById('hero-unrealized');
  unr.textContent = fmtSign(intra.unrealized);
  unr.className = 'val ' + (intra.unrealized > 0 ? 'gain' : intra.unrealized < 0 ? 'loss' : '');

  document.getElementById('hero-trades').textContent = intra.n_trades;
  document.getElementById('hero-open').textContent = intra.open_count;
  const avg = intra.n_trades ? intra.realized / intra.n_trades : 0;
  document.getElementById('hero-avg').textContent = fmtSign(avg);

  document.getElementById('kpi-wr').textContent = intra.win_rate.toFixed(0) + '%';
  document.getElementById('kpi-wr').className = 'kpi-value ' + (intra.win_rate >= 50 ? 'gain' : 'loss');
  document.getElementById('kpi-wr-sub').textContent = `${intra.wins}W / ${intra.losses}L`;

  document.getElementById('kpi-gp').textContent = fmtSign(intra.gross_win);
  document.getElementById('kpi-gp-sub').textContent = `from ${intra.wins} winners`;

  document.getElementById('kpi-gl').textContent = fmtSign(intra.gross_loss);
  document.getElementById('kpi-gl-sub').textContent = `from ${intra.losses} losers`;

  const pf = intra.profit_factor;
  document.getElementById('kpi-pf').textContent = pf ? pf.toFixed(2) : '—';
  document.getElementById('kpi-pf-sub').textContent = pf >= 2 ? 'excellent' : pf >= 1.5 ? 'healthy' : pf >= 1 ? 'thin' : '—';
}

function renderTopbar(d) {
  document.getElementById('brand-sub').innerHTML =
    `<span class="pill ${d.mode.toLowerCase()}">${d.mode}</span>  ${d.date}`;

  const r = d.regime;
  const rc = {BULLISH:'#10d987', NEUTRAL:'#ffd166', BEARISH:'#ff5a5f', CRASH:'#ff5a5f'}[r.label] || '#94a3b8';
  document.getElementById('t-regime').innerHTML =
    `<span style="color:${rc}">${r.label}</span> <small style="color:var(--t-3); font-size:11px;">·${(r.size*100).toFixed(0)}%</small>`;
  document.getElementById('t-capital').textContent = fmt(d.capital);
  document.getElementById('t-deployed').innerHTML =
    `${fmt(d.deployed)} <small style="color:var(--t-3); font-size:10px;">${(d.deployed/d.capital*100).toFixed(0)}%</small>`;
  document.getElementById('t-time').textContent = d.ts;
}

function renderLeverage(d) {
  const pct = d.leverage_pct || 0;
  const fill = document.getElementById('lev-fill');
  const pctEl = document.getElementById('lev-pct');
  // Cap visual at 130% but show real number
  fill.style.width = Math.min(pct, 130) + '%';
  pctEl.textContent = pct.toFixed(0) + '%';
  pctEl.className = 'lev-pct ' + (pct >= 100 ? 'danger' : pct >= 80 ? 'warn' : 'ok');
  document.getElementById('lev-deployed').textContent = fmt(d.deployed);
  document.getElementById('lev-capital').textContent = fmt(d.capital);
}

function renderSwing(d) {
  const wrap = document.getElementById('swing-grid');
  const sw = d.swing.positions || [];
  document.getElementById('swing-meta').innerHTML =
    `${sw.length} holding · ${fmt(d.swing.value)} value · ` +
    (d.swing.unrealized >= 0 ? '<span style="color:var(--gain)">' : '<span style="color:var(--loss)">') +
    fmtSign(d.swing.unrealized) + '</span> unrealized';

  if (!sw.length) {
    wrap.innerHTML = '<div class="empty">No swing positions.</div>';
    return;
  }
  wrap.innerHTML = sw.map(p => {
    const cls = p.state;
    const pcls = p.pnl > 0 ? 'gain' : p.pnl < 0 ? 'loss' : '';
    const barCls = p.pnl > 0 ? 'gain' : 'loss';
    return `<div class="pos ${cls} clickable" onclick="openDetail('${p.symbol}', 'position')">
      <div class="pos-row1">
        <div>
          <div class="pos-sym">${p.symbol}</div>
          <div class="pos-co">${p.company}</div>
        </div>
        <div>
          <div class="pos-pnl ${pcls}">${fmtSign(p.pnl)}</div>
          <div class="pos-pnl-pct ${pcls}">${fmtPct(p.pnl_pct)}</div>
        </div>
      </div>
      <div class="pos-tags">
        <span class="pos-strat-pill">${p.strategy}</span>
        <span class="pos-time-pill">${p.sector || ''} · since ${p.entry_date}</span>
      </div>
      <div class="pos-bar">
        <div class="pos-bar-fill ${barCls}" style="width:${p.progress}%;"></div>
        <div class="pos-bar-tick" style="left:${p.entry_pos}%;"></div>
      </div>
      <div class="pos-prices">
        <span>SL ₹${p.sl.toFixed(2)}</span>
        <span>Entry ₹${p.entry.toFixed(2)}</span>
        <span>TGT ₹${p.tgt.toFixed(2)}</span>
      </div>
      <div class="pos-grid4">
        <div><div class="pos-cell-l">Qty</div><div class="pos-cell-v">${p.qty.toLocaleString('en-IN')}</div></div>
        <div><div class="pos-cell-l">LTP</div><div class="pos-cell-v">₹${p.ltp.toFixed(2)}</div></div>
        <div><div class="pos-cell-l">Value</div><div class="pos-cell-v">${fmt(p.value)}</div></div>
        <div><div class="pos-cell-l">P&L %</div><div class="pos-cell-v ${pcls}">${fmtPct(p.pnl_pct)}</div></div>
      </div>
    </div>`;
  }).join('');
}

// Track which symbols we've already loaded news for to avoid refetch storms
const newsCache = new Map();
const NEWS_TTL_MS = 5 * 60 * 1000;   // 5 min

async function fetchNews(sym) {
  const cached = newsCache.get(sym);
  if (cached && (Date.now() - cached.ts < NEWS_TTL_MS)) {
    return cached.data;
  }
  try {
    const res = await fetch(`/api/news/${encodeURIComponent(sym)}`);
    const data = await res.json();
    newsCache.set(sym, { ts: Date.now(), data });
    return data;
  } catch (e) {
    return { news: [], sentiment: null };
  }
}

async function renderNewsGrid(d) {
  // Symbols to show news for: open positions (intra + swing) + top 5 swing picks + top 5 intra picks
  const symbols = new Set();
  (d.positions || []).forEach(p => symbols.add(p.symbol));
  ((d.swing && d.swing.positions) || []).forEach(p => symbols.add(p.symbol));
  ((d.today_plan && d.today_plan.swing_picks) || []).slice(0, 4).forEach(p => symbols.add(p.symbol));
  ((d.today_plan && d.today_plan.intraday_picks) || []).slice(0, 4).forEach(p => symbols.add(p.symbol));

  const grid = document.getElementById('news-grid');
  document.getElementById('news-meta').textContent = `${symbols.size} stocks`;
  if (symbols.size === 0) {
    grid.innerHTML = '<div class="empty">No stocks to track yet.</div>';
    return;
  }

  // Fetch news in parallel (bounded concurrency via Promise.all on small set)
  const symList = Array.from(symbols).slice(0, 12);
  const results = await Promise.all(symList.map(s => fetchNews(s).then(n => [s, n])));

  grid.innerHTML = results.map(([sym, data]) => {
    const sentiment = data.sentiment;
    let sentClass = 'neutral', sentLabel = '—', cardClass = '';
    if (sentiment) {
      const s = sentiment.score || 0;
      if (s > 15) { sentClass = 'positive'; sentLabel = `+${s.toFixed(0)}`; cardClass = 'positive'; }
      else if (s < -15) { sentClass = 'negative'; sentLabel = s.toFixed(0); cardClass = 'negative'; }
      else { sentClass = 'neutral'; sentLabel = s.toFixed(0); }
    }
    const tags = sentiment && sentiment.tags ? sentiment.tags.filter(t => t).slice(0, 3) : [];
    const newsList = (data.news || []).slice(0, 4);
    const items = newsList.length === 0
      ? '<div class="news-item" style="color:var(--t-3); font-style:italic;">No headlines yet.</div>'
      : newsList.map(n => `
          <div class="news-item">
            <span class="news-bullet"></span>
            <div>
              ${n.url
                ? `<a href="${n.url}" target="_blank" rel="noopener">${n.headline}</a>`
                : `<span>${n.headline}</span>`}
              <div class="news-source">${n.source}${n.ts ? ' · ' + n.ts : ''}</div>
            </div>
          </div>`).join('');
    return `
      <div class="news-card ${cardClass}">
        <div class="news-header">
          <span class="news-sym">${sym}</span>
          <span class="news-sentiment ${sentClass}">${sentLabel}</span>
        </div>
        ${tags.length ? `<div class="news-tags">${tags.map(t => `<span class="news-tag">${t}</span>`).join('')}</div>` : ''}
        ${items}
        <div class="news-footer">
          <a href="${data.moneycontrol_url}" target="_blank">MoneyControl ↗</a>
          <a href="${data.et_url}" target="_blank">Economic Times ↗</a>
        </div>
      </div>`;
  }).join('');
}

function renderTodayPlan(d) {
  const p = d.today_plan || {};

  // Intraday picks
  const ib = document.getElementById('intra-picks-body');
  if (!p.intraday_picks || !p.intraday_picks.length) {
    ib.innerHTML = '<div class="empty" style="padding: 20px;">No pre-market picks yet. Scan fires at 08:45.</div>';
  } else {
    ib.innerHTML = p.intraday_picks.map((s, i) => {
      const pctCls = s.pct > 0 ? 'gain' : s.pct < 0 ? 'loss' : '';
      return `<div class="plan-row clickable" onclick="openDetail('${s.symbol}', 'watchlist')">
        <div class="plan-rank">${i+1}</div>
        <div>
          <div class="plan-sym">${s.symbol}</div>
          <div class="plan-co">${s.company}</div>
        </div>
        <div class="plan-meta-cell">
          <span class="${pctCls}">${s.pct > 0 ? '+' : ''}${s.pct.toFixed(2)}%</span>
          &nbsp;·&nbsp; ${s.vol.toFixed(1)}× vol
          <br><small style="color:var(--t-3); font-size:10px;">score ${s.score.toFixed(1)} · ₹${s.yest_close.toFixed(2)}</small>
        </div>
      </div>`;
    }).join('');
  }

  // Swing picks
  const sb = document.getElementById('swing-picks-body');
  if (!p.swing_picks || !p.swing_picks.length) {
    sb.innerHTML = '<div class="empty" style="padding: 20px;">No swing candidates from last scan.</div>';
  } else {
    sb.innerHTML = p.swing_picks.map(s => {
      const grade = (s.grade || '').replace('+','\\+');
      return `<div class="plan-row clickable" onclick="openDetail('${s.symbol}', 'watchlist')">
        <div class="plan-rank">${s.rank}</div>
        <div>
          <div class="plan-sym">${s.symbol} <span class="plan-grade ${grade}">${s.grade}</span></div>
          <div class="plan-co">${s.sector} · score ${s.score.toFixed(1)}</div>
        </div>
        <div class="plan-meta-cell">
          ₹${s.price.toFixed(2)}
          <br><small style="color:var(--t-3); font-size:10px;">RSI ${s.rsi.toFixed(0)} · ${s.trend_pct > 0 ? '+' : ''}${s.trend_pct.toFixed(1)}%</small>
        </div>
      </div>`;
    }).join('');
  }

  // Catalysts
  const cb = document.getElementById('catalysts-body');
  if (!p.catalysts || !p.catalysts.length) {
    cb.innerHTML = '<div class="empty" style="padding: 20px;">No catalysts loaded.</div>';
  } else {
    cb.innerHTML = p.catalysts.map(c => {
      const impactCls = c.impact >= 7 ? 'high' : c.impact >= 5 ? 'medium' : 'low';
      return `<div class="plan-row clickable" onclick="openDetail('${c.symbol}', 'catalyst')">
        <div class="plan-impact ${impactCls}">${c.impact}</div>
        <div>
          <div class="plan-sym">${c.symbol}</div>
          <div class="plan-co" title="${c.headline}">${c.type} · ${c.headline.substring(0,60)}${c.headline.length>60?'…':''}</div>
        </div>
        <div class="plan-meta-cell">
          <small style="font-size:10px;">${c.date}</small>
        </div>
      </div>`;
    }).join('');
  }
}

// ============ GROWW REAL PORTFOLIO ============
function renderGrowwHoldings(d) {
  const g = d.groww || {};
  const wrap = document.getElementById('groww-holdings-panel');
  const grid = document.getElementById('groww-holdings-grid');
  if (!g.holdings || g.holdings.length === 0) {
    wrap.style.display = 'none';
    return;
  }
  wrap.style.display = 'block';
  const s = g.holdings_summary || {};
  document.getElementById('groww-holdings-meta').textContent =
    `${s.count || 0} holdings · ₹${(s.total_invested || 0).toLocaleString('en-IN')} invested`;
  const pnlEl = document.getElementById('groww-total-pnl');
  const pctEl = document.getElementById('groww-total-pct');
  const pnl = s.total_pnl || 0;
  pnlEl.textContent = (pnl > 0 ? '+' : pnl < 0 ? '−' : '') + '₹ ' + Math.abs(pnl).toLocaleString('en-IN', {maximumFractionDigits: 0});
  pnlEl.style.color = pnl > 0 ? 'var(--gain)' : pnl < 0 ? 'var(--loss)' : 'var(--t-2)';
  pctEl.textContent = (s.total_pnl_pct || 0).toFixed(2) + '% overall';
  pctEl.style.color = pnl > 0 ? 'var(--gain)' : pnl < 0 ? 'var(--loss)' : 'var(--t-3)';

  grid.innerHTML = g.holdings.map(p => {
    const cls = p.pnl > 0 ? 'winning' : p.pnl < 0 ? 'losing' : 'flat';
    const pcls = p.pnl > 0 ? 'gain' : p.pnl < 0 ? 'loss' : '';
    return `<div class="pos ${cls} clickable" onclick="openDetail('${p.symbol}', 'groww_holding')">
      <div class="pos-row1">
        <div>
          <div class="pos-sym">${p.symbol}</div>
          <div class="pos-co">${p.company}</div>
        </div>
        <div>
          <div class="pos-pnl ${pcls}">${fmtSign(p.pnl)}</div>
          <div class="pos-pnl-pct ${pcls}">${fmtPct(p.pnl_pct)}</div>
        </div>
      </div>
      <div class="pos-grid4">
        <div><div class="pos-cell-l">Qty</div><div class="pos-cell-v">${p.qty.toLocaleString('en-IN')}</div></div>
        <div><div class="pos-cell-l">Avg</div><div class="pos-cell-v">₹${p.avg_price.toFixed(2)}</div></div>
        <div><div class="pos-cell-l">LTP</div><div class="pos-cell-v">₹${p.ltp.toFixed(2)}</div></div>
        <div><div class="pos-cell-l">Invested</div><div class="pos-cell-v">${fmt(p.invested)}</div></div>
      </div>
    </div>`;
  }).join('');
}

function renderGrowwInsights(d) {
  const g = d.groww || {};
  const wrap = document.getElementById('groww-insights');
  const hasAny = (g.volume_shockers && g.volume_shockers.length) ||
                 (g.top_gainers && g.top_gainers.length) ||
                 (g.analyst_picks && g.analyst_picks.length);
  if (!hasAny) { wrap.style.display = 'none'; return; }
  wrap.style.display = 'grid';

  // Volume Shockers
  const sb = document.getElementById('groww-shockers-body');
  if (!g.volume_shockers || !g.volume_shockers.length) {
    sb.innerHTML = '<div class="empty" style="padding:20px;">No shockers yet.</div>';
  } else {
    sb.innerHTML = g.volume_shockers.slice(0, 10).map((s, i) => {
      const pct = s.prev_close ? ((s.ltp - s.prev_close) / s.prev_close * 100) : 0;
      const pctCls = pct > 0 ? 'gain' : pct < 0 ? 'loss' : '';
      return `<div class="plan-row clickable" onclick="openDetail('${s.symbol}', 'watchlist')">
        <div class="plan-rank">${i+1}</div>
        <div>
          <div class="plan-sym">${s.symbol}</div>
          <div class="plan-co">${s.company}</div>
        </div>
        <div class="plan-meta-cell">
          <span style="color: var(--brand-green); font-weight: 700;">${s.vol_ratio.toFixed(1)}× vol</span>
          <br><small style="color:var(--t-3); font-size:10px;">₹${s.ltp.toFixed(2)} · <span class="${pctCls}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</span></small>
        </div>
      </div>`;
    }).join('');
  }

  // Top Gainers
  const gb = document.getElementById('groww-gainers-body');
  if (!g.top_gainers || !g.top_gainers.length) {
    gb.innerHTML = '<div class="empty" style="padding:20px;">—</div>';
  } else {
    gb.innerHTML = g.top_gainers.slice(0, 10).map((s, i) => `
      <div class="plan-row clickable" onclick="openDetail('${s.symbol}', 'watchlist')">
        <div class="plan-rank">${i+1}</div>
        <div>
          <div class="plan-sym">${s.symbol}</div>
          <div class="plan-co">${s.company}</div>
        </div>
        <div class="plan-meta-cell">
          <span class="gain" style="font-weight: 700;">+${s.day_change_pct.toFixed(2)}%</span>
          <br><small style="color:var(--t-3); font-size:10px;">₹${s.ltp.toFixed(2)}</small>
        </div>
      </div>`).join('');
  }

  // Analyst Picks
  const ab = document.getElementById('groww-analyst-body');
  if (!g.analyst_picks || !g.analyst_picks.length) {
    ab.innerHTML = '<div class="empty" style="padding:20px;">No 100% Buy ratings.</div>';
  } else {
    ab.innerHTML = g.analyst_picks.slice(0, 10).map((s, i) => `
      <div class="plan-row clickable" onclick="openDetail('${s.symbol}', 'watchlist')">
        <div class="plan-rank">${i+1}</div>
        <div>
          <div class="plan-sym">${s.symbol}</div>
          <div class="plan-co">${s.company}</div>
        </div>
        <div class="plan-meta-cell">
          <span style="color: var(--gold); font-weight: 700;">${s.rating}</span>
          ${s.ltp ? `<br><small style="color:var(--t-3); font-size:10px;">₹${s.ltp.toFixed(2)}</small>` : ''}
        </div>
      </div>`).join('');
  }
}

// ============ MODAL: click-to-drill-down ============
async function openDetail(symbol, kind) {
  const overlay = document.getElementById('modal-overlay');
  const body = document.getElementById('modal-body');
  document.getElementById('modal-title').textContent = symbol;
  document.getElementById('modal-sub').textContent = `Loading ${kind} details…`;
  body.innerHTML = '<div style="padding:40px;text-align:center;color:var(--t-3)">Loading…</div>';
  overlay.classList.add('open');
  try {
    const res = await fetch(`/api/detail/${kind}/${encodeURIComponent(symbol)}`);
    const d = await res.json();
    renderDetailModal(d, symbol, kind);
  } catch (e) {
    body.innerHTML = `<div style="color:var(--loss); padding:20px;">Failed to load: ${e.message}</div>`;
  }
}

function closeModal(e) {
  if (e && e.target.id !== 'modal-overlay' && e.type === 'click') return;
  document.getElementById('modal-overlay').classList.remove('open');
}
document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeModal(); });

function renderDetailModal(d, symbol, kind) {
  const sub = [];
  if (d.current) sub.push(`${d.current.kind} position`);
  if (d.watchlist) sub.push(`watchlist rank #${d.watchlist.rank} (${d.watchlist.grade})`);
  if (d.today_trades.length) sub.push(`${d.today_trades.length} trades today`);
  document.getElementById('modal-sub').textContent = sub.join(' · ') || 'Detail view';

  let html = '';

  // === SUGGESTION (top) ===
  if (d.suggestion && d.suggestion.action) {
    const sCls = d.suggestion.action.includes('STOP') || d.suggestion.action.includes('REVIEW') ? 'danger'
               : d.suggestion.action.includes('NEAR') || d.suggestion.action.includes('TRAIL') ? 'warning' : '';
    html += `<div class="modal-suggestion ${sCls}">
      <div class="action">${d.suggestion.action}</div>
      <div class="why">${d.suggestion.reason}</div>
    </div>`;
  }

  // === CURRENT POSITION ===
  if (d.current) {
    const p = d.current;
    const pnlCls = p.pnl > 0 ? 'gain' : p.pnl < 0 ? 'loss' : '';
    const pnlPct = p.entry ? ((p.ltp - p.entry) / p.entry * 100) : 0;
    html += `<div class="modal-section">
      <div class="modal-section-title">Position</div>
      <div class="modal-grid">
        <div class="modal-stat"><div class="label">Qty</div><div class="val">${p.qty.toLocaleString('en-IN')}</div></div>
        <div class="modal-stat"><div class="label">Entry</div><div class="val">₹${p.entry.toFixed(2)}</div></div>
        <div class="modal-stat"><div class="label">LTP</div><div class="val">₹${p.ltp.toFixed(2)}</div></div>
        <div class="modal-stat"><div class="label">P&L</div><div class="val ${pnlCls}">${p.pnl > 0 ? '+' : ''}₹${p.pnl.toLocaleString('en-IN',{maximumFractionDigits:0})} <small style="font-size:11px;">(${pnlPct >= 0 ? '+' : ''}${pnlPct.toFixed(2)}%)</small></div></div>
        <div class="modal-stat"><div class="label">Stop Loss</div><div class="val">₹${p.sl.toFixed(2)}</div></div>
        <div class="modal-stat"><div class="label">Target</div><div class="val">₹${p.tgt.toFixed(2)}</div></div>
        <div class="modal-stat"><div class="label">Strategy</div><div class="val" style="font-size:13px;">${p.strategy}</div></div>
        <div class="modal-stat"><div class="label">${p.kind === 'intraday' ? 'Entry time' : 'Entry date'}</div><div class="val" style="font-size:13px;">${p.entry_time}</div></div>
      </div>
    </div>`;
  }

  // === WHY ENTERED — signal log ===
  if (d.signals && d.signals.length) {
    html += `<div class="modal-section">
      <div class="modal-section-title">Why bot entered (signal log)</div>
      ${d.signals.map(s => `
        <div class="signal-card">
          <div class="signal-header">
            <span class="signal-strat">${s.strategy}</span>
            <span class="signal-time">${s.time} · conf ${(s.confidence*100).toFixed(0)}%</span>
          </div>
          <div class="signal-reason">${s.reasoning}</div>
          <div class="signal-prices">
            <span>Entry ₹${s.entry.toFixed(2)}</span>
            <span>SL ₹${s.sl.toFixed(2)}</span>
            <span>TGT ₹${s.target.toFixed(2)}</span>
          </div>
        </div>`).join('')}
    </div>`;
  }

  // === WATCHLIST SCORE BREAKDOWN ===
  if (d.watchlist) {
    const w = d.watchlist;
    html += `<div class="modal-section">
      <div class="modal-section-title">Watchlist scoring</div>
      <div class="modal-grid">
        <div class="modal-stat"><div class="label">Grade</div><div class="val">${w.grade}</div></div>
        <div class="modal-stat"><div class="label">Score</div><div class="val">${w.score.toFixed(1)}</div></div>
        <div class="modal-stat"><div class="label">Sector</div><div class="val" style="font-size:13px;">${w.sector}</div></div>
        <div class="modal-stat"><div class="label">RSI</div><div class="val">${w.rsi.toFixed(0)}</div></div>
        <div class="modal-stat"><div class="label">Trend</div><div class="val ${w.trend_pct >= 0 ? 'gain' : 'loss'}">${w.trend_pct >= 0 ? '+' : ''}${w.trend_pct.toFixed(1)}%</div></div>
        <div class="modal-stat"><div class="label">From 52w-hi</div><div class="val">${w.distance_from_high_pct.toFixed(1)}%</div></div>
        <div class="modal-stat"><div class="label">Vol ratio</div><div class="val">${w.volume_ratio.toFixed(2)}×</div></div>
        <div class="modal-stat"><div class="label">EMA aligned</div><div class="val">${w.ema_aligned}</div></div>
      </div>
    </div>`;
  }

  // === TODAY'S TRADES ===
  if (d.today_trades && d.today_trades.length) {
    html += `<div class="modal-section">
      <div class="modal-section-title">All trades today (${d.today_trades.length})</div>
      <table class="modal-table">
        <tr><th>Time</th><th>Strategy</th><th>Entry</th><th>Exit</th><th>P&L</th><th>%</th><th>Held</th><th>Why exited</th></tr>
        ${d.today_trades.map(t => {
          const cls = t.pnl > 0 ? 'gain' : t.pnl < 0 ? 'loss' : '';
          return `<tr>
            <td class="mono">${t.exit_time}</td>
            <td>${t.strategy}</td>
            <td class="mono">₹${t.entry_price.toFixed(2)}</td>
            <td class="mono">₹${t.exit_price.toFixed(2)}</td>
            <td class="mono ${cls}">${t.pnl > 0 ? '+' : ''}${t.pnl.toLocaleString('en-IN',{maximumFractionDigits:0})}</td>
            <td class="mono ${cls}">${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(2)}%</td>
            <td class="mono">${t.held_min}m</td>
            <td>${t.reason}</td>
          </tr>`;
        }).join('')}
      </table>
    </div>`;
  }

  // === CATALYSTS ===
  if (d.catalysts && d.catalysts.length) {
    html += `<div class="modal-section">
      <div class="modal-section-title">Upcoming catalysts</div>
      ${d.catalysts.map(c => `
        <div style="display:flex; gap:12px; padding:8px 0; border-bottom:1px dashed var(--border);">
          <div style="background:${c.impact >= 7 ? 'var(--gain)' : c.impact >= 5 ? 'var(--gold)' : 'var(--t-3)'}; color:white; width:28px; height:28px; border-radius:50%; display:flex; align-items:center; justify-content:center; font-weight:700; font-family:var(--font-num); font-size:12px; flex-shrink:0;">${c.impact}</div>
          <div style="flex:1;">
            <div style="font-weight:600; font-size:13px;">${c.type} · <small style="color:var(--t-3); font-family:var(--font-num);">${c.date}</small></div>
            <div style="font-size:12px; color:var(--t-2); margin-top:2px;">${c.headline}</div>
          </div>
        </div>`).join('')}
    </div>`;
  }

  // === BULL/BEAR THESIS (multi-agent synthesis) ===
  if (d.thesis) {
    const t = d.thesis;
    const actCol = t.action === 'BUY' ? 'var(--gain)' : t.action === 'SELL' ? 'var(--loss)' : 'var(--gold)';
    html += `<div class="modal-section">
      <div class="modal-section-title">🧠 Multi-Agent Thesis</div>
      <div class="callout" style="margin-bottom: 12px; border-left: 4px solid ${actCol};">
        <div style="font-size: 16px; font-weight: 700; color: ${actCol};">
          ${t.action} · ${(t.confidence*100).toFixed(0)}% conviction
        </div>
        <div style="font-size: 13px; color: var(--t-2); margin-top: 4px;">${t.summary}</div>
      </div>
      <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 12px;">
        <div style="background: var(--gain-soft); border-left: 3px solid var(--gain); padding: 12px 14px; border-radius: 0 8px 8px 0;">
          <div style="font-size: 11px; font-weight: 700; color: var(--gain); text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 6px;">📈 Bull Case</div>
          ${t.bull && t.bull.length ? '<ul style="margin: 0; padding-left: 18px; font-size: 12px; color: var(--t-2); line-height: 1.6;">' + t.bull.map(p => `<li>${p}</li>`).join('') + '</ul>' : '<div style="font-size: 12px; color: var(--t-3); font-style: italic;">No bullish factors detected</div>'}
        </div>
        <div style="background: var(--loss-soft); border-left: 3px solid var(--loss); padding: 12px 14px; border-radius: 0 8px 8px 0;">
          <div style="font-size: 11px; font-weight: 700; color: var(--loss); text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 6px;">📉 Bear Case</div>
          ${t.bear && t.bear.length ? '<ul style="margin: 0; padding-left: 18px; font-size: 12px; color: var(--t-2); line-height: 1.6;">' + t.bear.map(p => `<li>${p}</li>`).join('') + '</ul>' : '<div style="font-size: 12px; color: var(--t-3); font-style: italic;">No bearish factors detected</div>'}
        </div>
      </div>
      ${t.risks && t.risks.length ? `<div style="background: var(--gold-soft); border-left: 3px solid var(--gold); padding: 12px 14px; border-radius: 0 8px 8px 0; margin-top: 10px;">
        <div style="font-size: 11px; font-weight: 700; color: var(--gold); text-transform: uppercase; letter-spacing: 0.1em; margin-bottom: 6px;">⚠️ Risks to Watch</div>
        <ul style="margin: 0; padding-left: 18px; font-size: 12px; color: var(--t-2); line-height: 1.6;">${t.risks.map(p => `<li>${p}</li>`).join('')}</ul>
      </div>` : ''}
    </div>`;
  }

  // === ANALYST RECOMMENDATION ===
  if (d.analyst) {
    const a = d.analyst;
    const ltp = (d.current && d.current.ltp) || 0;
    let upside = '';
    if (a.target_price && ltp) {
      const u = ((a.target_price - ltp) / ltp * 100);
      upside = `<span style="color:${u>=0?'var(--gain)':'var(--loss)'}; font-weight:600;"> (${u>=0?'+':''}${u.toFixed(1)}%)</span>`;
    }
    const totalRatings = (a.buys||0) + (a.sells||0) + (a.holds||0);
    const buyPct = totalRatings ? ((a.buys||0)/totalRatings*100).toFixed(0) : 0;
    html += `<div class="modal-section">
      <div class="modal-section-title">📊 Analyst View (MoneyControl consensus)</div>
      <div class="modal-grid">
        ${a.target_price ? `<div class="modal-stat"><div class="label">Target Price</div><div class="val" style="font-size:18px;">₹${a.target_price.toFixed(2)}${upside}</div></div>` : ''}
        ${a.buys != null ? `<div class="modal-stat"><div class="label">Buy</div><div class="val gain">${a.buys}</div></div>` : ''}
        ${a.holds != null ? `<div class="modal-stat"><div class="label">Hold</div><div class="val">${a.holds}</div></div>` : ''}
        ${a.sells != null ? `<div class="modal-stat"><div class="label">Sell</div><div class="val loss">${a.sells}</div></div>` : ''}
      </div>
      ${totalRatings ? `<div style="margin-top:8px; font-size:11px; color:var(--t-3);">${buyPct}% of analysts rate BUY (${totalRatings} ratings)</div>` : ''}
    </div>`;
  }

  // === UPCOMING RESULTS ===
  if (d.upcoming_results && d.upcoming_results.date) {
    const u = d.upcoming_results;
    const probColor = u.beat_probability >= 0.65 ? 'var(--gain)' : u.beat_probability >= 0.5 ? 'var(--gold)' : 'var(--loss)';
    html += `<div class="modal-section">
      <div class="modal-section-title">📅 Upcoming Quarterly Results</div>
      <div class="callout" style="margin-bottom:0; padding: 16px 20px;">
        <div style="font-size:18px; font-weight:700; color:var(--brand-navy); margin-bottom:6px;">
          ${u.quarter || 'Results'} · <span style="font-family:var(--font-num);">${u.date}</span>
        </div>
        <div style="font-size:13px; color:var(--t-2);">
          Beat probability: <b style="color:${probColor}">${(u.beat_probability*100).toFixed(0)}%</b>
          ${u.last_4q_moves ? `<br>Last 4Q price reactions: <span style="font-family:var(--font-num);">[${u.last_4q_moves}%]</span>` : ''}
        </div>
      </div>
    </div>`;
  }

  // === NATIVE GROWW CHART (Chart.js OHLC + volume, no third-party widgets) ===
  if (d.chart && d.chart.candles && d.chart.candles.length) {
    const cid = 'grw_' + symbol.replace(/[^A-Z0-9]/gi, '');
    const vid = 'grwv_' + symbol.replace(/[^A-Z0-9]/gi, '');
    const candles = d.chart.candles;
    const first = candles[0].close, last = candles[candles.length-1].close;
    const pct = ((last - first) / first * 100).toFixed(2);
    const color = pct >= 0 ? 'var(--gain)' : 'var(--loss)';
    html += `<div class="modal-section">
      <div class="modal-section-title">📈 Price Chart · ${symbol} <span style="font-weight:400; color:var(--text-2); font-size:11px;">· ${d.chart.source} · ${candles.length} sessions · <span style="color:${color};">${pct >= 0 ? '+' : ''}${pct}%</span></span></div>
      <div style="position:relative; height:300px;"><canvas id="${cid}"></canvas></div>
      <div style="position:relative; height:90px; margin-top:8px;"><canvas id="${vid}"></canvas></div>
    </div>`;
    setTimeout(function() {
      function ensureChartJS(cb) {
        if (window.Chart) return cb();
        var s = document.createElement('script');
        s.src = 'https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js';
        s.onload = cb;
        s.onerror = function() {
          var el = document.getElementById(cid);
          if (el && el.parentNode) el.parentNode.innerHTML = '<div style="padding:14px; color:#888; text-align:center;">Chart library failed to load. Check internet.</div>';
        };
        document.head.appendChild(s);
      }
      ensureChartJS(function() {
        var labels = candles.map(function(c){ return c.date.slice(5); });
        var closes = candles.map(function(c){ return c.close; });
        var highs  = candles.map(function(c){ return c.high; });
        var lows   = candles.map(function(c){ return c.low; });
        var vols   = candles.map(function(c){ return c.volume / 1e6; });
        var volColors = candles.map(function(c, i) {
          var prev = i > 0 ? candles[i-1].close : c.open;
          return c.close >= prev ? 'rgba(31,224,75,0.55)' : 'rgba(255,85,119,0.55)';
        });
        var priceEl = document.getElementById(cid);
        var volEl   = document.getElementById(vid);
        if (!priceEl || !volEl) { console.error('Canvas missing', cid, vid); return; }
        try {
          new Chart(priceEl, {
            type: 'line',
            data: {
              labels: labels,
              datasets: [
                { label: 'High', data: highs, borderColor: 'rgba(31,224,75,0.30)', borderWidth: 1, pointRadius: 0, tension: 0.2, fill: false },
                { label: 'Low',  data: lows,  borderColor: 'rgba(255,85,119,0.30)', borderWidth: 1, pointRadius: 0, tension: 0.2, fill: '-1', backgroundColor: 'rgba(14,42,90,0.06)' },
                { label: 'Close', data: closes, borderColor: '#0E2A5A', borderWidth: 2.2, pointRadius: 0, tension: 0.15, fill: false }
              ]
            },
            options: {
              responsive: true, maintainAspectRatio: false, animation: false,
              interaction: { mode: 'index', intersect: false },
              plugins: {
                legend: { display: false },
                tooltip: {
                  callbacks: {
                    title: function(items){ return candles[items[0].dataIndex].date; },
                    label: function(item){
                      var c = candles[item.dataIndex];
                      return 'O ' + c.open + '  H ' + c.high + '  L ' + c.low + '  C ' + c.close;
                    }
                  }
                }
              },
              scales: {
                x: { grid: { display: false }, ticks: { maxTicksLimit: 8, color: '#64748b', font: {size: 10} } },
                y: { grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { color: '#64748b', font: {size: 11} } }
              }
            }
          });
          new Chart(volEl, {
            type: 'bar',
            data: { labels: labels, datasets: [{ label: 'Vol (M)', data: vols, backgroundColor: volColors, borderWidth: 0 }] },
            options: {
              responsive: true, maintainAspectRatio: false, animation: false,
              plugins: { legend: { display: false }, tooltip: { callbacks: { label: function(i){ return 'Vol: ' + i.parsed.y.toFixed(1) + 'M'; } } } },
              scales: {
                x: { grid: { display: false }, ticks: { display: false } },
                y: { grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { color: '#64748b', font: {size: 10}, callback: function(v){ return v + 'M'; } } }
              }
            }
          });
        } catch(err) {
          console.error('Chart render error:', err);
          if (priceEl.parentNode) priceEl.parentNode.innerHTML = '<div style="padding:14px; color:#c00;">Chart error: ' + err.message + '</div>';
        }
      });
    }, 80);
  } else {
    // No local candle data — fall back to a clean message + Groww/TradingView links
    html += `<div class="modal-section">
      <div class="modal-section-title">📈 Chart</div>
      <div style="padding:14px; background:var(--bg-2); border:1px solid var(--border); border-radius:8px; color:var(--text-2); font-size:12px;">
        Daily candles will appear here after the next Groww sync.
        <br>For now: <a href="https://groww.in/stocks/${symbol.toLowerCase()}" target="_blank" style="color:var(--brand-navy); font-weight:600;">View on Groww ↗</a>
        · <a href="https://in.tradingview.com/chart/?symbol=NSE%3A${symbol}" target="_blank" style="color:var(--brand-navy); font-weight:600;">TradingView ↗</a>
      </div>
    </div>`;
  }

  // === QUICK LINKS ===
  html += `<div class="modal-section">
    <div class="modal-section-title">External research</div>
    <div style="display:flex; gap:10px; flex-wrap:wrap;">
      <a href="https://www.moneycontrol.com/news/tags/${symbol.toLowerCase()}.html" target="_blank" rel="noopener"
         style="padding:8px 14px; background:var(--bg-2); border:1px solid var(--border); border-radius:6px; color:var(--brand-navy); text-decoration:none; font-size:12px; font-weight:600;">MoneyControl ↗</a>
      <a href="https://economictimes.indiatimes.com/topic/${symbol.toLowerCase()}" target="_blank" rel="noopener"
         style="padding:8px 14px; background:var(--bg-2); border:1px solid var(--border); border-radius:6px; color:var(--brand-navy); text-decoration:none; font-size:12px; font-weight:600;">Economic Times ↗</a>
      <a href="https://in.tradingview.com/chart/?symbol=NSE%3A${symbol}" target="_blank" rel="noopener"
         style="padding:8px 14px; background:var(--bg-2); border:1px solid var(--border); border-radius:6px; color:var(--brand-navy); text-decoration:none; font-size:12px; font-weight:600;">TradingView Chart ↗</a>
      <a href="https://www.screener.in/company/${symbol}/consolidated/" target="_blank" rel="noopener"
         style="padding:8px 14px; background:var(--bg-2); border:1px solid var(--border); border-radius:6px; color:var(--brand-navy); text-decoration:none; font-size:12px; font-weight:600;">Screener.in ↗</a>
    </div>
  </div>`;

  document.getElementById('modal-body').innerHTML = html;
}

// Theme toggle — DEFAULT = light/bright with RDA branding
function initTheme() {
  const saved = localStorage.getItem('rda-theme') || 'light';
  document.documentElement.setAttribute('data-theme', saved);
  const btn = document.getElementById('theme-toggle');
  btn.textContent = saved === 'light' ? '🌙 Dark' : '☀ Light';
  btn.onclick = () => {
    const cur = document.documentElement.getAttribute('data-theme');
    const next = cur === 'light' ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', next);
    localStorage.setItem('rda-theme', next);
    btn.textContent = next === 'light' ? '🌙 Dark' : '☀ Light';
    refresh();
  };
}
initTheme();

function renderPositions(d) {
  const wrap = document.getElementById('pos-grid');
  document.getElementById('pos-meta').textContent = `${d.positions.length} holding · ${fmt(d.intraday.open_value)} deployed`;
  if (!d.positions.length) {
    wrap.innerHTML = '<div class="empty">No open intraday positions.<br><small>Scanners running every 2-3 min — waiting for signal.</small></div>';
    return;
  }
  wrap.innerHTML = d.positions.map(p => {
    const cls = p.state;
    const pcls = p.pnl > 0 ? 'gain' : p.pnl < 0 ? 'loss' : '';
    const barCls = p.pnl > 0 ? 'gain' : 'loss';
    const stateLabel = {
      winning: '🟢 Winning',
      losing: '🔴 Losing',
      near_tgt: '🎯 Near Target',
      near_sl: '⚠️ Near Stop',
      flat: '⚪ Flat',
    }[p.state] || '';
    return `<div class="pos ${cls} clickable" onclick="openDetail('${p.symbol}', 'position')">
      <div class="pos-row1">
        <div>
          <div class="pos-sym">${p.symbol}</div>
          <div class="pos-co">${p.company}</div>
        </div>
        <div>
          <div class="pos-pnl ${pcls}">${fmtSign(p.pnl)}</div>
          <div class="pos-pnl-pct ${pcls}">${fmtPct(p.pnl_pct)}</div>
        </div>
      </div>
      <div class="pos-tags">
        <span class="pos-strat-pill">${p.strategy}</span>
        <span class="pos-time-pill">${stateLabel} · since ${p.entry_time}</span>
      </div>
      <div class="pos-bar">
        <div class="pos-bar-fill ${barCls}" style="width:${p.progress}%;"></div>
        <div class="pos-bar-tick" style="left:${p.entry_pos}%;"></div>
      </div>
      <div class="pos-prices">
        <span>SL ₹${p.sl.toFixed(2)}</span>
        <span>Entry ₹${p.entry.toFixed(2)}</span>
        <span>TGT ₹${p.tgt.toFixed(2)}</span>
      </div>
      <div class="pos-grid4">
        <div><div class="pos-cell-l">Qty</div><div class="pos-cell-v">${p.qty.toLocaleString('en-IN')}</div></div>
        <div><div class="pos-cell-l">LTP</div><div class="pos-cell-v">₹${p.ltp.toFixed(2)}</div></div>
        <div><div class="pos-cell-l">Value</div><div class="pos-cell-v">${fmt(p.value)}</div></div>
        <div><div class="pos-cell-l">Risk</div><div class="pos-cell-v">${fmt(Math.abs(p.risk))}</div></div>
      </div>
    </div>`;
  }).join('');
}

function renderPnlCurve(d) {
  document.getElementById('curve-meta').textContent =
    d.curve.length ? `${d.curve.length} trades · last ${d.curve.length ? d.curve[d.curve.length-1].time : '—'}` : 'no trades yet';

  const ctx = document.getElementById('pnl-chart').getContext('2d');
  const labels = d.curve.map(c => c.time);
  const data = d.curve.map(c => c.cum);
  const lastVal = data.length ? data[data.length-1] : 0;
  const lineColor = lastVal >= 0 ? '#10d987' : '#ff5a5f';
  const fillColor = lastVal >= 0 ? 'rgba(16,217,135,0.15)' : 'rgba(255,90,95,0.15)';

  if (pnlChart) pnlChart.destroy();
  pnlChart = new Chart(ctx, {
    type: 'line',
    data: {
      labels,
      datasets: [{
        data, borderColor: lineColor, backgroundColor: fillColor,
        borderWidth: 2.5, tension: 0.3, fill: true,
        pointBackgroundColor: d.curve.map(c => c.pnl >= 0 ? '#10d987' : '#ff5a5f'),
        pointBorderColor: '#0a0f1c', pointBorderWidth: 1.5, pointRadius: 5, pointHoverRadius: 7,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      animation: { duration: 600, easing: 'easeOutQuart' },
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: 'rgba(10,15,28,0.95)',
          titleFont: { family: 'JetBrains Mono', size: 11 },
          bodyFont: { family: 'JetBrains Mono', size: 12 },
          borderColor: '#1a2238', borderWidth: 1,
          callbacks: {
            title: (ctx) => d.curve[ctx[0].dataIndex].symbol + ' · ' + d.curve[ctx[0].dataIndex].time,
            label: (ctx) => 'Cum: ' + fmtSign(ctx.parsed.y),
          },
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { color: '#64748b', font: { family: 'JetBrains Mono', size: 10 } },
        },
        y: {
          grid: { color: 'rgba(255,255,255,0.04)' },
          ticks: {
            color: '#64748b', font: { family: 'JetBrains Mono', size: 10 },
            callback: (v) => '₹' + v.toLocaleString('en-IN', {maximumFractionDigits: 0}),
          },
        },
      },
    },
  });
}

function renderStrategies(d) {
  const total = d.intraday.realized;
  document.getElementById('donut-v').textContent = (total > 0 ? '+' : total < 0 ? '−' : '') + fmt(total);
  document.getElementById('donut-v').style.color = total > 0 ? '#10d987' : total < 0 ? '#ff5a5f' : '#f8fafc';

  const svg = d3.select('#donut');
  svg.selectAll('*').remove();
  if (!d.strategies.length) {
    document.getElementById('strat-legend').innerHTML =
      '<div class="empty" style="padding: 16px;">No strategy data yet today.</div>';
    return;
  }

  // Show only profitable strategies in donut (the wins to celebrate)
  const positives = d.strategies.filter(s => s.pnl > 0);
  if (positives.length) {
    const w = 200, h = 200, r = 90;
    const g = svg.append('g').attr('transform', `translate(${w/2},${h/2})`);
    const pie = d3.pie().value(x => x.pnl).sort(null);
    const arc = d3.arc().innerRadius(60).outerRadius(r).cornerRadius(4).padAngle(0.02);
    g.selectAll('path').data(pie(positives)).enter().append('path')
      .attr('d', arc)
      .attr('fill', x => stratColor(x.data.strategy))
      .attr('stroke', '#0a0f1c').attr('stroke-width', 2)
      .style('filter', 'drop-shadow(0 2px 8px rgba(0,0,0,0.4))');
  }

  document.getElementById('strat-legend').innerHTML = d.strategies.map(s => `
    <div class="strat-row">
      <div>
        <span class="strat-dot" style="background:${stratColor(s.strategy)}"></span>
        <span class="strat-name">${s.strategy}</span>
        <small style="color: var(--t-4); font-family: var(--font-num); margin-left: 6px;">${s.trades}t · ${s.win_rate.toFixed(0)}%</small>
      </div>
      <div class="strat-pnl" style="color:${s.pnl >= 0 ? '#10d987' : '#ff5a5f'}">${fmtSign(s.pnl)}</div>
    </div>
  `).join('');
}

function renderTradeLog(d) {
  const wrap = document.getElementById('trade-log');
  document.getElementById('trades-meta').textContent = `${d.trade_log.length} trades`;
  if (!d.trade_log.length) {
    wrap.innerHTML = '<div class="empty">No trades booked today yet.</div>';
    return;
  }
  wrap.innerHTML = d.trade_log.map(t => {
    const cls = t.pnl > 0 ? 'gain' : t.pnl < 0 ? 'loss' : '';
    return `<div class="trade-row clickable" onclick="openDetail('${t.symbol}', 'trade')">
      <div class="trade-time">${t.time}</div>
      <div>
        <div class="trade-sym">${t.symbol} <small style="color:var(--t-3); font-weight:400; font-family:var(--font-ui)">${t.company}</small></div>
        <div class="trade-meta">${t.qty}× @ ₹${t.entry.toFixed(2)} → ₹${t.exit.toFixed(2)} · ${t.strategy} · ${t.held_min}m</div>
      </div>
      <div>
        <div class="trade-pnl ${cls}">${fmtSign(t.pnl)}</div>
        <div class="trade-pnl ${cls}" style="font-size:10px; font-weight:500;">${fmtPct(t.pnl_pct)}</div>
      </div>
      <div class="trade-reason">${t.reason}</div>
    </div>`;
  }).join('');
}

function renderEvents(d) {
  const wrap = document.getElementById('events-feed');
  if (!d.events.length) {
    wrap.innerHTML = '<div class="empty">No events yet.</div>';
    return;
  }
  wrap.innerHTML = d.events.slice(0, 20).map(e => `
    <div class="event-row ${e.level}">
      <span class="event-time">${e.ts}</span>
      <div>
        <span class="event-action">${e.action}</span>
        ${e.symbol ? '<span class="event-sym">' + e.symbol + '</span> ' : ''}
        <span class="event-text">${e.details || ''}</span>
      </div>
    </div>
  `).join('');
}

function renderMovers(d) {
  const wrap = document.getElementById('movers-strip');
  if (!d.movers.length) {
    wrap.innerHTML = '<div class="empty" style="padding: 16px;">No breakout candidates right now.</div>';
    return;
  }
  wrap.innerHTML = d.movers.map(m => `
    <div class="mover-chip">
      <div class="mover-sym">${m.symbol}</div>
      <div class="mover-pct">+${m.pct.toFixed(2)}%</div>
      <div class="mover-vol">${m.vol.toFixed(1)}× vol · ₹${m.price.toFixed(2)}</div>
    </div>
  `).join('');
}

async function refresh() {
  try {
    const res = await fetch('/api/snapshot');
    const d = await res.json();
    renderTopbar(d);
    renderLeverage(d);
    renderGrowwHoldings(d);   // NEW — real Groww portfolio at top
    renderGrowwInsights(d);    // NEW — volume shockers + top gainers + analyst
    renderTodayPlan(d);
    renderHero(d);
    renderPositions(d);
    renderSwing(d);
    renderPnlCurve(d);
    renderStrategies(d);
    renderTradeLog(d);
    renderEvents(d);
    renderMovers(d);
    renderNewsGrid(d);
  } catch (e) {
    console.error(e);
  }
}

refresh();
setInterval(refresh, 5000);
</script>
<div style="margin-top:30px;padding:20px;background:#0f1419;border-radius:12px;"><h2 style="color:#88d3ce;margin-bottom:10px;">🤖 RDA Advisor Chat</h2><iframe src="http://141.148.196.105:8503" style="width:100%;height:600px;border:0;border-radius:8px;background:#0f1419;"></iframe></div>
</body>
</html>"""


@app.route("/api/news/<symbol>")
def api_news(symbol: str):
    """Return FRESH news for a single stock — last 7 days only.

    Live MoneyControl + Economic Times first. Cached news.csv filtered to last 7d.
    Stale articles (>7 days old) are filtered out so user never sees 2025 news.
    """
    import urllib.parse
    import re
    from datetime import datetime as _dt, timedelta as _td

    symbol = symbol.upper()
    out = []
    SEVEN_DAYS_AGO = _dt.now() - _td(days=7)

    # 1. LIVE MoneyControl fetch (highest priority — actual Indian source)
    try:
        mc_url = f"https://www.moneycontrol.com/news/tags/{symbol.lower()}.html"
        r = requests.get(mc_url, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept-Language": "en-IN,en;q=0.9",
        }, timeout=6)
        if r.ok:
            # MoneyControl uses multiple patterns — try several
            patterns = [
                r'<li[^>]*class="[^"]*clearfix[^"]*"[^>]*>.*?<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>',
                r'<a[^>]+class="[^"]*news[^"]*"[^>]+href="([^"]+)"[^>]*>([^<]+)</a>',
                r'<h2[^>]*>\s*<a[^>]+href="([^"]+)"[^>]*>([^<]+)</a>',
            ]
            for pat in patterns:
                matches = re.findall(pat, r.text, re.DOTALL)
                for url, hl in matches[:6]:
                    hl = re.sub(r'\s+', ' ', hl.strip())
                    if not hl or len(hl) < 15 or len(hl) > 250:
                        continue
                    if any(o["headline"] == hl for o in out):
                        continue
                    # Try to extract date from URL pattern (.../yyyy/mm/dd/...)
                    date_match = re.search(r'/(\d{4})/(\d{2})/(\d{2})/', url)
                    ts = ""
                    if date_match:
                        try:
                            d = _dt(int(date_match.group(1)), int(date_match.group(2)),
                                     int(date_match.group(3)))
                            if d < SEVEN_DAYS_AGO:
                                continue   # skip stale URL-dated articles
                            ts = d.strftime("%Y-%m-%d")
                        except ValueError:
                            pass
                    out.append({
                        "headline": hl, "source": "MoneyControl",
                        "url": url, "ts": ts,
                    })
                if len(out) >= 5:
                    break
    except Exception:
        pass

    # 2. LIVE Economic Times stocks page
    if len(out) < 5:
        try:
            et_url = f"https://economictimes.indiatimes.com/topic/{symbol.lower()}"
            r = requests.get(et_url, headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            }, timeout=6)
            if r.ok:
                # ET headlines pattern
                matches = re.findall(
                    r'<a[^>]+href="(/[^"]+\.cms)"[^>]*>\s*([^<]{15,200})\s*</a>',
                    r.text,
                )
                for url, hl in matches[:8]:
                    hl = re.sub(r'\s+', ' ', hl.strip())
                    if any(o["headline"].lower() == hl.lower() for o in out):
                        continue
                    if not re.search(r'\b(' + re.escape(symbol) + r'|results|earnings|q[1-4]|stock|share|rally|fall)\b', hl, re.IGNORECASE):
                        continue
                    out.append({
                        "headline": hl, "source": "Economic Times",
                        "url": f"https://economictimes.indiatimes.com{url}",
                        "ts": "",
                    })
                    if len(out) >= 8:
                        break
        except Exception:
            pass

    # 3. Cached news.csv — LAST 7 DAYS ONLY (skip stale)
    n = _read("news.csv")
    if not n.empty and "symbol" in n.columns and len(out) < 6:
        hits = n[n["symbol"].astype(str) == symbol]
        # Parse published_at, filter to last 7 days
        # IMPORTANT: Google News RSS returns tz-aware timestamps. Strip tz so
        # comparison with naive SEVEN_DAYS_AGO works.
        if "published_at" in hits.columns:
            hits = hits.copy()
            hits["_dt"] = pd.to_datetime(hits["published_at"], errors="coerce", utc=True)
            try:
                hits["_dt"] = hits["_dt"].dt.tz_localize(None)
            except (AttributeError, TypeError):
                pass    # already naive
            hits = hits.dropna(subset=["_dt"])
            hits = hits[hits["_dt"] >= SEVEN_DAYS_AGO]
            hits = hits.sort_values("_dt", ascending=False).head(5)
        for _, r in hits.iterrows():
            hl = str(r.get("headline") or r.get("title") or "")
            if not hl or any(o["headline"].lower() == hl.lower() for o in out):
                continue
            out.append({
                "headline": hl[:200],
                "source": str(r.get("publisher", "cache")),
                "url": str(r.get("url", "")),
                "ts": str(r.get("published_at", ""))[:10],
            })


    # 4. Cached news_scored sentiment row
    ns = _read("news_scored.csv")
    sentiment = None
    if not ns.empty and "symbol" in ns.columns:
        row = ns[ns["symbol"] == symbol]
        if not row.empty:
            r0 = row.iloc[0]
            sentiment = {
                "score": float(r0.get("sentiment", 0)),
                "tags": [t for t in str(r0.get("tags", "")).split(",") if t],
                "top_pos": [h for h in str(r0.get("top_pos", "")).split(" | ") if h],
                "top_neg": [h for h in str(r0.get("top_neg", "")).split(" | ") if h],
                "n": int(r0.get("n_headlines", 0)),
            }

    # 5. Final fallback — provide search links
    if not out:
        out.append({
            "headline": f"No fresh news in last 7 days. Click sources below to search live.",
            "source": "",
            "url": "",
            "ts": "",
        })

    return jsonify({
        "symbol": symbol,
        "news": out[:8],
        "sentiment": sentiment,
        "moneycontrol_url": f"https://www.moneycontrol.com/news/tags/{symbol.lower()}.html",
        "et_url": f"https://economictimes.indiatimes.com/topic/{symbol.lower()}",
        "bse_url": f"https://www.bseindia.com/corporates/anndet_new.aspx?strcd=&search={urllib.parse.quote(symbol)}",
    })



@app.route("/api/detail/<kind>/<symbol>")
def api_detail(kind: str, symbol: str):
    """Rich detail for modal — positions, signals, analyst, results, chart."""
    symbol = symbol.upper()
    today = date.today().isoformat()
    out = {"symbol": symbol, "kind": kind}

    # Today's trades
    it = _read("intraday_trades.csv")
    out["today_trades"] = []
    if not it.empty and "exit_time" in it.columns:
        rows = it[(it["symbol"].astype(str) == symbol)
                   & (it["exit_time"].astype(str).str.startswith(today))]
        for _, r in rows.iterrows():
            out["today_trades"].append({
                "exit_time": str(r.get("exit_time", ""))[11:16],
                "entry_price": float(r.get("entry_price", 0)),
                "exit_price": float(r.get("exit_price", 0)),
                "qty": int(r.get("quantity", 0)),
                "pnl": float(r.get("pnl_net", 0)),
                "pnl_pct": float(str(r.get("pnl_pct", "0")).replace("%","").replace(",","").strip() or 0),
                "reason": str(r.get("exit_reason", "")),
                "strategy": str(r.get("strategy", "")),
                "held_min": int(r.get("holding_minutes", 0)),
            })

    # Current position — intraday, swing, or Groww
    cur_pos = None
    for fname, klabel in [("intraday_positions.csv", "intraday"),
                            ("positions.csv", "swing"),
                            ("groww_holdings.csv", "groww")]:
        df_p = _read(fname)
        if df_p.empty or "symbol" not in df_p.columns:
            continue
        match = df_p[df_p["symbol"].astype(str) == symbol]
        if match.empty:
            continue
        r = match.iloc[0]
        if klabel == "groww":
            cur_pos = {
                "qty": int(r.get("qty", 0)),
                "entry": float(r.get("avg_price", 0)),
                "ltp": float(r.get("ltp", 0)),
                "sl": 0, "tgt": 0,
                "pnl": round((float(r.get("ltp", 0)) - float(r.get("avg_price", 0))) * int(r.get("qty", 0)), 2),
                "strategy": "groww_holding", "entry_time": "",
                "kind": "groww",
                "invested": float(r.get("invested", 0)),
            }
        else:
            cur_pos = {
                "qty": int(r.get("quantity", 0)),
                "entry": float(r.get("entry_price", 0)),
                "ltp": float(r.get("current_price", r.get("entry_price", 0))),
                "sl": float(r.get("stop_loss", 0)),
                "tgt": float(r.get("target", 0)),
                "pnl": float(r.get("unrealized_pnl", 0)),
                "strategy": str(r.get("strategy", "")),
                "entry_time": str(r.get("entry_time", "")),
                "kind": klabel,
            }
        break
    out["current"] = cur_pos
    out["today_trades"] = out.get("today_trades", [])
    out["signals"] = out.get("signals", [])
    out["catalysts"] = out.get("catalysts", [])
    out["watchlist"] = out.get("watchlist", None)

    # Suggestion
    suggestion, suggestion_reason = "OK HOLD", "Position behaving normally"
    if cur_pos:
        pnl_pct = ((cur_pos["ltp"] - cur_pos["entry"]) / cur_pos["entry"] * 100) if cur_pos["entry"] else 0
        if pnl_pct < -3:
            suggestion, suggestion_reason = "WARN REVIEW THESIS", f"Down {pnl_pct:.1f}%"
        elif pnl_pct > 20:
            suggestion, suggestion_reason = "TGT BOOK PARTIAL", f"Up {pnl_pct:.1f}% — lock some gains"
    out["suggestion"] = {"action": suggestion, "reason": suggestion_reason}

    # Bull/Bear thesis
    try:
        from agents.research.stock_thesis import build_thesis
        out["thesis"] = build_thesis(symbol)
    except Exception:
        out["thesis"] = None

    # Analyst placeholder
    out["analyst"] = None

    # Groww 100% Buy cross-check
    ga = _read("groww_analyst_picks.csv")
    if not ga.empty and "symbol" in ga.columns:
        match = ga[ga["symbol"].astype(str) == symbol]
        if not match.empty:
            out["groww_rating"] = str(match.iloc[0].get("rating", ""))

    out["upcoming_results"] = None


    # Native Groww candle chart — replaces broken TradingView (Apple chart bug)
    out["chart"] = None
    candle_file = DATA_DIR / f"groww_candles_{symbol}.csv"
    if candle_file.exists():
        try:
            cdf = pd.read_csv(candle_file)
            if not cdf.empty:
                cdf = cdf.tail(60)
                out["chart"] = {
                    "source": "Groww (live)",
                    "interval": "1D",
                    "candles": [
                        {
                            "date": str(r["date"]),
                            "open": float(r["open"]),
                            "high": float(r["high"]),
                            "low": float(r["low"]),
                            "close": float(r["close"]),
                            "volume": float(r.get("volume", 0)),
                        }
                        for _, r in cdf.iterrows()
                    ],
                }
        except Exception:
            pass

    # Live LTP refresh — pulls latest from data/live_ltp.csv (refreshed every 2 min)
    live_ltp_file = DATA_DIR / "live_ltp.csv"
    if live_ltp_file.exists():
        try:
            lt = pd.read_csv(live_ltp_file)
            row = lt[lt["symbol"].astype(str) == symbol]
            if not row.empty:
                live_ltp = float(row.iloc[0].get("ltp", 0))
                live_ts = str(row.iloc[0].get("updated_at", ""))
                if live_ltp > 0:
                    out["live_ltp"] = live_ltp
                    out["live_ltp_updated_at"] = live_ts
                    # Override stale LTP in current position
                    if out.get("current"):
                        out["current"]["ltp"] = live_ltp
                        if out["current"].get("entry"):
                            out["current"]["pnl"] = round(
                                (live_ltp - out["current"]["entry"]) * out["current"]["qty"], 2)
        except Exception:
            pass

    return jsonify(_clean_nan(out))


@app.route("/")
def index():
    return render_template_string(HTML)


if __name__ == "__main__":
    print("\n  RDA Trading Terminal (LUXURY) starting on http://localhost:8502\n")
    app.run(host="0.0.0.0", port=8502, debug=False)
