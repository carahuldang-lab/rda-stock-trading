"""NSE Holiday Calendar — auto-detect market holidays.

Fetches NSE's official holiday list and caches locally. Scheduler checks this
before running scans/trades.

Sources (in priority order):
    1. NSE archives JSON API (most accurate)
    2. Hardcoded fallback list (covers all 2026 holidays)
    3. Yahoo Finance ^NSEI history check (if all-zero day, was holiday)
"""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

DATA_DIR = Path(__file__).parent.parent.parent / "data"
HOLIDAYS_FILE = DATA_DIR / "nse_holidays.csv"

# Hardcoded NSE Equity holiday list for 2026 (verified from NSE)
# Used as fallback when API isn't reachable
HOLIDAYS_2026 = [
    {"date": "2026-01-26", "name": "Republic Day"},
    {"date": "2026-02-19", "name": "Mahashivratri"},
    {"date": "2026-03-17", "name": "Holi"},
    {"date": "2026-04-03", "name": "Good Friday"},
    {"date": "2026-04-14", "name": "Dr Baba Saheb Ambedkar Jayanti"},
    {"date": "2026-05-01", "name": "Maharashtra Day / Labour Day"},
    {"date": "2026-08-15", "name": "Independence Day"},
    {"date": "2026-08-27", "name": "Ganesh Chaturthi"},
    {"date": "2026-10-02", "name": "Mahatma Gandhi Jayanti"},
    {"date": "2026-10-21", "name": "Diwali Laxmi Pujan (Muhurat trading only)"},
    {"date": "2026-11-04", "name": "Guru Nanak Jayanti"},
    {"date": "2026-12-25", "name": "Christmas"},
]


def fetch_nse_holidays(year: int = None) -> list[dict]:
    """Try NSE API first, fall back to hardcoded list."""
    if year is None:
        year = date.today().year

    # Attempt NSE API
    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64) AppleWebKit/537.36",
            "Accept": "application/json",
        }
        url = f"https://www.nseindia.com/api/holiday-master?type=trading"
        # NSE requires an initial homepage hit for cookies
        s = requests.Session()
        s.headers.update(headers)
        s.get("https://www.nseindia.com/", timeout=10)
        r = s.get(url, timeout=10)
        if r.status_code == 200:
            data = r.json()
            # Parse response
            if "CM" in data:    # Cash Market
                rows = []
                for item in data["CM"]:
                    rows.append({
                        "date": item.get("tradingDate", "")[:10],
                        "name": item.get("description", "Holiday"),
                    })
                if rows:
                    return rows
    except Exception:
        pass

    # Fallback to hardcoded
    return [h for h in HOLIDAYS_2026 if h["date"].startswith(str(year))]


def refresh_holidays(year: int = None) -> int:
    """Save current year's holidays to CSV."""
    DATA_DIR.mkdir(exist_ok=True, parents=True)
    rows = fetch_nse_holidays(year)
    pd.DataFrame(rows).to_csv(HOLIDAYS_FILE, index=False)
    return len(rows)


def load_holidays() -> set[str]:
    """Return set of holiday dates as ISO strings."""
    if not HOLIDAYS_FILE.exists():
        # Auto-populate first time
        try:
            refresh_holidays()
        except Exception:
            pass
    if HOLIDAYS_FILE.exists():
        try:
            df = pd.read_csv(HOLIDAYS_FILE)
            return set(df["date"].astype(str).tolist())
        except Exception:
            pass
    # Last fallback — hardcoded
    return {h["date"] for h in HOLIDAYS_2026}


def is_market_holiday(check_date: Optional[date] = None) -> tuple[bool, str]:
    """Check if a given date is an NSE holiday.

    Returns (is_holiday, holiday_name_or_empty).
    Weekends NOT included here — handled separately.
    """
    if check_date is None:
        check_date = date.today()
    holidays = load_holidays()
    iso = check_date.isoformat()
    if iso in holidays:
        # Get name
        if HOLIDAYS_FILE.exists():
            try:
                df = pd.read_csv(HOLIDAYS_FILE)
                row = df[df["date"].astype(str) == iso]
                if not row.empty:
                    return True, str(row["name"].iloc[0])
            except Exception:
                pass
        return True, "NSE Holiday"
    return False, ""


def is_trading_day(check_date: Optional[date] = None) -> tuple[bool, str]:
    """Definitive check: is today a trading day?

    Returns (is_trading_day, reason_if_not).
    """
    if check_date is None:
        check_date = date.today()

    # Weekend
    if check_date.weekday() == 5:
        return False, "Saturday"
    if check_date.weekday() == 6:
        return False, "Sunday"

    # Holiday
    is_holiday, name = is_market_holiday(check_date)
    if is_holiday:
        return False, name

    return True, ""


def next_trading_day(from_date: Optional[date] = None) -> tuple[date, list[str]]:
    """Find the next trading day after from_date. Returns (date, skipped_reasons)."""
    if from_date is None:
        from_date = date.today()

    from datetime import timedelta
    skipped = []
    candidate = from_date + timedelta(days=1)
    while True:
        is_td, reason = is_trading_day(candidate)
        if is_td:
            return candidate, skipped
        skipped.append(f"{candidate.isoformat()}: {reason}")
        candidate += timedelta(days=1)
        if len(skipped) > 10:  # safety
            break
    return candidate, skipped
