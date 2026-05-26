"""Groww sync — pulls live holdings + LTPs via official Groww Trading API
and writes data/groww_holdings.csv in the format dashboard + bot expect."""
from __future__ import annotations
import os, sys, csv
from datetime import datetime
from pathlib import Path
from dotenv import load_dotenv
load_dotenv()
import pandas as pd
from growwapi import GrowwAPI

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
OUT = DATA / "groww_holdings.csv"


def company_map():
    try:
        df = pd.read_csv(DATA / "nifty500.csv")
        if {"symbol", "company_name"}.issubset(df.columns):
            return dict(zip(df["symbol"], df["company_name"]))
    except Exception:
        pass
    return {}


def main():
    key = os.environ.get("GROWW_API_KEY")
    secret = os.environ.get("GROWW_API_SECRET")
    if not key or not secret:
        print("ERROR: GROWW_API_KEY / GROWW_API_SECRET missing in .env"); sys.exit(1)
    token = GrowwAPI.get_access_token(api_key=key, secret=secret)
    cli = GrowwAPI(token)
    resp = cli.get_holdings_for_user()
    holdings = resp.get("holdings", []) if isinstance(resp, dict) else []
    DATA.mkdir(parents=True, exist_ok=True)
    if not holdings:
        with OUT.open("w", newline="") as f:
            csv.writer(f).writerow(["symbol","company","qty","avg_price","ltp","invested","pnl_pct","isin"])
        print(f"[{datetime.now().isoformat()}] no holdings; wrote empty CSV"); return
    pairs = [f"NSE_{h['trading_symbol']}" for h in holdings]
    try:
        ltps = cli.get_ltp(exchange_trading_symbols=pairs, segment=GrowwAPI.SEGMENT_CASH)
    except Exception as e:
        print(f"WARN: LTP fetch failed: {e}; using avg_price fallback")
        ltps = {p: float(h["average_price"]) for p, h in zip(pairs, holdings)}
    cmap = company_map()
    with OUT.open("w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol","company","qty","avg_price","ltp","invested","pnl_pct","isin"])
        for h in holdings:
            sym = h["trading_symbol"]
            qty = float(h.get("quantity", 0))
            avg = float(h.get("average_price", 0))
            ltp = float(ltps.get(f"NSE_{sym}", avg))
            inv = round(qty * avg, 2)
            pnl = round((ltp - avg) / avg * 100, 2) if avg else 0
            w.writerow([sym, cmap.get(sym, sym), int(qty), avg, ltp, inv, pnl, h.get("isin","")])
    print(f"[{datetime.now().isoformat()}] wrote {len(holdings)} holdings → {OUT}")


if __name__ == "__main__":
    main()
