"""Data loader for the dashboard.

Reads from CSV files in data/ folder. Cached by Streamlit for performance.
"""
from __future__ import annotations

from datetime import datetime, date
from pathlib import Path
from typing import Optional

import pandas as pd
import streamlit as st

DATA_DIR = Path(__file__).parent.parent / "data"


@st.cache_data(ttl=10)
def load_positions() -> pd.DataFrame:
    f = DATA_DIR / "positions.csv"
    if not f.exists():
        return pd.DataFrame(columns=[
            "symbol", "quantity", "entry_price", "entry_time", "stop_loss",
            "target", "current_price", "unrealized_pnl", "sector", "strategy",
        ])
    return pd.read_csv(f)


@st.cache_data(ttl=10)
def load_trades() -> pd.DataFrame:
    f = DATA_DIR / "trades.csv"
    if not f.exists():
        return pd.DataFrame()
    return pd.read_csv(f)


@st.cache_data(ttl=10)
def load_signals(limit: int = 50) -> pd.DataFrame:
    f = DATA_DIR / "signals.csv"
    if not f.exists():
        return pd.DataFrame()
    df = pd.read_csv(f)
    return df.tail(limit).iloc[::-1]   # newest first


@st.cache_data(ttl=10)
def load_equity_curve() -> pd.DataFrame:
    f = DATA_DIR / "equity.csv"
    if not f.exists():
        return pd.DataFrame()
    df = pd.read_csv(f)
    if "date" in df.columns:
        df["date"] = pd.to_datetime(df["date"])
    return df


@st.cache_data(ttl=5)
def load_events(limit: int = 100) -> list[dict]:
    """Load recent agent activity events."""
    import json
    f = DATA_DIR / "events.jsonl"
    if not f.exists():
        return []
    with open(f, "r", encoding="utf-8") as fp:
        lines = fp.readlines()
    events = []
    for line in lines[-limit:]:
        try:
            events.append(json.loads(line))
        except Exception:
            continue
    return list(reversed(events))


@st.cache_data(ttl=300)   # 5 min cache
def load_universe() -> pd.DataFrame:
    """Load Nifty 500 master."""
    f = DATA_DIR / "nifty500.csv"
    if not f.exists():
        return pd.DataFrame()
    return pd.read_csv(f)


@st.cache_data(ttl=30)
def load_candidates() -> pd.DataFrame:
    """Load ranked candidates from latest universe scan."""
    f = DATA_DIR / "candidates.csv"
    if not f.exists():
        return pd.DataFrame()
    return pd.read_csv(f)


@st.cache_data(ttl=300)
def load_fundamentals() -> pd.DataFrame:
    f = DATA_DIR / "fundamentals.csv"
    if not f.exists():
        return pd.DataFrame()
    return pd.read_csv(f)


@st.cache_data(ttl=300)
def load_news() -> pd.DataFrame:
    f = DATA_DIR / "news.csv"
    if not f.exists():
        return pd.DataFrame()
    return pd.read_csv(f)


@st.cache_data(ttl=60)
def load_backtest_results() -> pd.DataFrame:
    f = DATA_DIR / "backtest_results.csv"
    if not f.exists():
        return pd.DataFrame()
    return pd.read_csv(f)


@st.cache_data(ttl=60)
def load_backtest_trades() -> pd.DataFrame:
    f = DATA_DIR / "backtest_trades.csv"
    if not f.exists():
        return pd.DataFrame()
    return pd.read_csv(f)


def get_kpis(config: dict, positions: pd.DataFrame, trades: pd.DataFrame) -> dict:
    """Compute top-level KPIs."""
    capital = config["account"]["capital"]
    invested = float(positions["quantity"].astype(float).mul(
        positions["entry_price"].astype(float)).sum()) if not positions.empty else 0.0
    unrealized_pnl = float(positions["unrealized_pnl"].astype(float).sum()) \
        if not positions.empty and "unrealized_pnl" in positions.columns else 0.0

    realized_today = 0.0
    win_rate = 0.0
    if not trades.empty and "pnl_net" in trades.columns:
        today_str = date.today().isoformat()
        if "exit_time" in trades.columns:
            today_trades = trades[trades["exit_time"].astype(str).str.startswith(today_str)]
            realized_today = float(today_trades["pnl_net"].astype(float).sum())
        wins = (trades["pnl_net"].astype(float) > 0).sum()
        win_rate = (wins / len(trades) * 100) if len(trades) > 0 else 0.0

    return {
        "capital": capital,
        "cash": capital - invested,
        "invested": invested,
        "unrealized_pnl": unrealized_pnl,
        "realized_today": realized_today,
        "open_positions": len(positions),
        "total_trades": len(trades),
        "win_rate": win_rate,
    }
