"""NSE feeds — replaces Groww web-scrape data for volume shockers, top gainers, analyst picks.
Writes:
  data/groww_volume_shockers.csv
  data/groww_top_gainers.csv
  data/groww_analyst_picks.csv
Cloud-autonomous: no MCP, no laptop, no Claude."""
from __future__ import annotations
import csv, time
from datetime import datetime
from pathlib import Path
import requests
import pandas as pd

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"

NSE_BASE = "https://www.nseindia.com"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}


def nse_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    try:
        s.get(NSE_BASE, timeout=10); time.sleep(1)
        s.get(f"{NSE_BASE}/market-data/live-equity-market", timeout=10); time.sleep(1)
    except Exception as e:
        print(f"[nse] handshake: {e}")
    return s


def company_map() -> dict:
    try:
        df = pd.read_csv(DATA / "nifty500.csv")
        if {"symbol","company_name"}.issubset(df.columns):
            return dict(zip(df["symbol"], df["company_name"]))
    except Exception:
        pass
    return {}


def fetch_volume_gainers(s) -> list[dict]:
    try:
        r = s.get(f"{NSE_BASE}/api/live-analysis-volume-gainers", timeout=15)
        if not r.ok: return []
        data = r.json()
        items = data.get("data", []) if isinstance(data, dict) else data
        out = []
        for x in items[:25]:
            sym = x.get("symbol",""); 
            if not sym: continue
            ltp = float(x.get("ltp", 0) or 0)
            pchg = float(x.get("pChange", 0) or 0)
            prev = round(ltp / (1 + pchg/100), 2) if (ltp and pchg != -100) else ltp
            out.append({
                "symbol": sym,
                "company": x.get("companyName", sym),
                "ltp": ltp,
                "prev_close": prev,
                "vol_ratio": round(float(x.get("week1volChange", 0) or 0), 2),
                "volume": int(float(x.get("volume", 0) or 0)),
                "market_cap_cr": round(float(x.get("turnover", 0) or 0), 2),
            })
        return out
    except Exception as e:
        print(f"[nse] vol err: {e}"); return []


def fetch_top_gainers(s) -> list[dict]:
    try:
        r = s.get(f"{NSE_BASE}/api/live-analysis-variations?index=gainers", timeout=15)
        if not r.ok: return []
        data = r.json()
        items = []
        if isinstance(data, dict):
            for key in ("NIFTY","NIFTYNEXT50","SecGtr20","allSec","FOSec"):
                bucket = data.get(key, {})
                if isinstance(bucket, dict):
                    items = bucket.get("data", [])
                    if items: break
        cmap = company_map()
        out = []
        for x in items[:15]:
            sym = x.get("symbol","")
            if not sym: continue
            out.append({
                "symbol": sym,
                "company": cmap.get(sym, sym),
                "ltp": float(x.get("ltp", 0) or 0),
                "day_change_pct": round(float(x.get("perChange", 0) or 0), 2),
            })
        return out
    except Exception as e:
        print(f"[nse] gainers err: {e}"); return []


def fetch_analyst_picks(symbols, limit=30) -> list[dict]:
    try:
        import yfinance as yf
    except ImportError:
        return []
    out = []
    for sym in symbols[:limit]:
        try:
            t = yf.Ticker(f"{sym}.NS")
            info = t.info
            rec = info.get("recommendationKey","")
            if rec in ("strong_buy","buy"):
                out.append({
                    "symbol": sym,
                    "company": info.get("shortName", sym),
                    "ltp": float(info.get("currentPrice", 0) or 0),
                    "rating": "100% Buy" if rec == "strong_buy" else "Buy",
                })
            time.sleep(0.3)
        except Exception:
            pass
    return out


def write_csv(path: Path, rows, cols):
    DATA.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows: w.writerow({c: r.get(c, "") for c in cols})


def main():
    print(f"[{datetime.now().isoformat()}] NSE feeds starting")
    s = nse_session()
    shockers = fetch_volume_gainers(s)
    write_csv(DATA/"groww_volume_shockers.csv", shockers,
              ["symbol","company","ltp","prev_close","vol_ratio","volume","market_cap_cr"])
    print(f"  volume shockers: {len(shockers)}")
    gainers = fetch_top_gainers(s)
    write_csv(DATA/"groww_top_gainers.csv", gainers,
              ["symbol","company","ltp","day_change_pct"])
    print(f"  top gainers: {len(gainers)}")
    try:
        universe = pd.read_csv(DATA/"nifty500.csv")["symbol"].head(50).tolist()
    except Exception:
        universe = []
    picks = fetch_analyst_picks(universe, limit=30)
    write_csv(DATA/"groww_analyst_picks.csv", picks,
              ["symbol","company","ltp","rating"])
    print(f"  analyst picks: {len(picks)}")
    print(f"[{datetime.now().isoformat()}] NSE feeds done")


if __name__ == "__main__":
    main()
