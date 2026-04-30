"""Dhan Historical Data fetcher — uses paid Data API.

Dhan API endpoint:
    POST /v2/charts/historical
    body: { "securityId", "exchangeSegment", "instrument", "fromDate", "toDate" }

Returns OHLCV bars. Caches to data/historical_cache/<symbol>.parquet to avoid
re-fetching.
"""
from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
from dotenv import load_dotenv

DATA_DIR = Path(__file__).parent.parent / "data"
CACHE_DIR = DATA_DIR / "historical_cache"

load_dotenv()


def fetch_history_dhan(
    symbol: str,
    security_id: int,
    from_date: str,
    to_date: str,
    instrument: str = "EQUITY",
    exchange: str = "NSE_EQ",
) -> Optional[pd.DataFrame]:
    """Fetch daily OHLCV via Dhan API. Requires Data API subscription."""
    try:
        from dhanhq import DhanContext, dhanhq as DhanClient
    except ImportError:
        return None

    client_id = os.getenv("DHAN_CLIENT_ID")
    access_token = os.getenv("DHAN_ACCESS_TOKEN")
    if not client_id or not access_token:
        return None

    try:
        ctx = DhanContext(client_id, access_token)
        client = DhanClient(ctx)
        resp = client.historical_daily_data(
            security_id=str(security_id),
            exchange_segment=exchange,
            instrument_type=instrument,
            from_date=from_date,
            to_date=to_date,
        )
        if not isinstance(resp, dict) or resp.get("status") != "success":
            return None
        data = resp.get("data", {})
        df = pd.DataFrame({
            "open": data.get("open", []),
            "high": data.get("high", []),
            "low": data.get("low", []),
            "close": data.get("close", []),
            "volume": data.get("volume", []),
        })
        timestamps = data.get("timestamp", [])
        if len(timestamps) == len(df):
            df.index = pd.to_datetime(timestamps, unit="s")
        return df
    except Exception:
        return None


def get_5year_history(symbol: str, security_id: int, force_refresh: bool = False) -> pd.DataFrame:
    """Get 5-year daily history (cached).

    Falls back to yfinance if Dhan API call fails.
    """
    CACHE_DIR.mkdir(exist_ok=True, parents=True)
    cache_path = CACHE_DIR / f"{symbol}_5y.parquet"

    if cache_path.exists() and not force_refresh:
        cached = pd.read_parquet(cache_path)
        # Refresh if data is more than 1 day stale
        if not cached.empty and (datetime.now() - cached.index[-1]).days < 2:
            return cached

    # Try Dhan first
    end = date.today()
    start = end - timedelta(days=5 * 365)
    df = fetch_history_dhan(
        symbol, security_id,
        from_date=start.isoformat(), to_date=end.isoformat(),
    )

    # Fallback: yfinance
    if df is None or df.empty:
        try:
            import yfinance as yf
            df = yf.download(f"{symbol}.NS", period="5y", interval="1d",
                              progress=False, auto_adjust=True)
            if hasattr(df.columns, "get_level_values"):
                df.columns = df.columns.get_level_values(0)
            df.columns = [c.lower() for c in df.columns]
            df = df[["open", "high", "low", "close", "volume"]]
        except Exception:
            return pd.DataFrame()

    if not df.empty:
        try:
            df.to_parquet(cache_path)
        except Exception:
            pass
    return df
