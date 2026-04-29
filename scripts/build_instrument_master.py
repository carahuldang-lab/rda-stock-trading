"""Build Nifty 500 instrument master file with Dhan security_id mapping.

What it does:
    1. Downloads NSE's official Nifty 500 constituent list.
    2. Downloads Dhan's full instrument master (scrip master).
    3. Joins them on symbol, producing data/nifty500.csv with:
           symbol, dhan_security_id, isin, lot_size, tick_size, sector

Run once a month (constituents change quarterly):
    python scripts/build_instrument_master.py

Output: data/nifty500.csv
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import requests

# --- URLs ---
NSE_NIFTY500_URL = "https://archives.nseindia.com/content/indices/ind_nifty500list.csv"
DHAN_SCRIP_MASTER_URL = "https://images.dhan.co/api-data/api-scrip-master.csv"

OUTPUT_DIR = Path(__file__).parent.parent / "data"
OUTPUT_FILE = OUTPUT_DIR / "nifty500.csv"


def download_nse_nifty500() -> pd.DataFrame:
    """Download the Nifty 500 constituent list from NSE."""
    print("[1/3] Downloading Nifty 500 list from NSE...")
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "text/csv,application/octet-stream,*/*",
    }
    r = requests.get(NSE_NIFTY500_URL, headers=headers, timeout=30)
    r.raise_for_status()

    # Save to temp + read
    tmp = OUTPUT_DIR / "_nse_nifty500_raw.csv"
    tmp.write_bytes(r.content)
    df = pd.read_csv(tmp)
    tmp.unlink(missing_ok=True)

    # Standard NSE columns: "Company Name", "Industry", "Symbol", "Series", "ISIN Code"
    df = df.rename(columns={
        "Symbol": "symbol",
        "Company Name": "company_name",
        "Industry": "sector",
        "ISIN Code": "isin",
    })
    print(f"      Loaded {len(df)} Nifty 500 constituents")
    return df[["symbol", "company_name", "sector", "isin"]]


def download_dhan_scrip_master() -> pd.DataFrame:
    """Download Dhan's full instrument master."""
    print("[2/3] Downloading Dhan scrip master...")
    r = requests.get(DHAN_SCRIP_MASTER_URL, timeout=60)
    r.raise_for_status()
    tmp = OUTPUT_DIR / "_dhan_scrip_master_raw.csv"
    tmp.write_bytes(r.content)
    df = pd.read_csv(tmp, low_memory=False)
    tmp.unlink(missing_ok=True)
    print(f"      Loaded {len(df)} instruments")
    return df


def filter_dhan_to_nse_equity(df: pd.DataFrame) -> pd.DataFrame:
    """Keep only NSE Equity instruments."""
    # Dhan column names (verified from their docs)
    # SEM_EXM_EXCH_ID = NSE | BSE
    # SEM_INSTRUMENT_NAME = EQUITY | INDEX | FUTSTK | OPTSTK ...
    # SEM_TRADING_SYMBOL = ticker symbol (e.g., RELIANCE)
    # SEM_SMST_SECURITY_ID = unique ID Dhan uses in API calls
    # SEM_LOT_UNITS = lot size
    # SEM_TICK_SIZE = price tick
    needed = [
        "SEM_EXM_EXCH_ID", "SEM_INSTRUMENT_NAME", "SEM_TRADING_SYMBOL",
        "SEM_SMST_SECURITY_ID", "SEM_LOT_UNITS", "SEM_TICK_SIZE",
    ]
    missing = [c for c in needed if c not in df.columns]
    if missing:
        print(f"      [WARN] Dhan columns missing: {missing}")
        print(f"      Available: {list(df.columns)[:15]}...")

    filt = df[
        (df.get("SEM_EXM_EXCH_ID", "") == "NSE")
        & (df.get("SEM_INSTRUMENT_NAME", "") == "EQUITY")
    ].copy()

    filt = filt.rename(columns={
        "SEM_TRADING_SYMBOL": "symbol",
        "SEM_SMST_SECURITY_ID": "dhan_security_id",
        "SEM_LOT_UNITS": "lot_size",
        "SEM_TICK_SIZE": "tick_size",
    })
    print(f"      Filtered to {len(filt)} NSE equity instruments")
    return filt[["symbol", "dhan_security_id", "lot_size", "tick_size"]]


def merge_and_save(nifty500: pd.DataFrame, dhan_eq: pd.DataFrame) -> None:
    """Inner-join Nifty 500 list with Dhan security_id."""
    print("[3/3] Joining Nifty 500 list with Dhan security IDs...")
    merged = nifty500.merge(dhan_eq, on="symbol", how="left")

    # Report unmatched (rare — may be due to symbol mismatch like NIFTYBEES)
    unmatched = merged[merged["dhan_security_id"].isna()]
    if len(unmatched):
        print(f"      [WARN] {len(unmatched)} symbols not matched in Dhan master:")
        for s in unmatched["symbol"].head(10):
            print(f"         - {s}")
        if len(unmatched) > 10:
            print(f"         ... and {len(unmatched) - 10} more")

    matched = merged.dropna(subset=["dhan_security_id"]).copy()
    matched["dhan_security_id"] = matched["dhan_security_id"].astype(int)

    # Add yfinance ticker (for backup data source)
    matched["yfinance_symbol"] = matched["symbol"] + ".NS"

    OUTPUT_DIR.mkdir(exist_ok=True)
    matched.to_csv(OUTPUT_FILE, index=False)
    print(f"\n[DONE] Saved {len(matched)} stocks to {OUTPUT_FILE}")
    print("\nFirst 5 rows:")
    print(matched.head().to_string(index=False))


if __name__ == "__main__":
    OUTPUT_DIR.mkdir(exist_ok=True)
    try:
        n500 = download_nse_nifty500()
        dhan = download_dhan_scrip_master()
        nse_eq = filter_dhan_to_nse_equity(dhan)
        merge_and_save(n500, nse_eq)
    except Exception as e:
        print(f"\n[ERROR] {type(e).__name__}: {e}")
        sys.exit(1)
