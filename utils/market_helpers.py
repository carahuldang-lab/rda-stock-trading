"""Market helpers — tick-size rounding, lot adjustments, position reconciliation."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import pandas as pd

DATA_DIR = Path(__file__).parent.parent / "data"


def get_tick_size(symbol: str) -> float:
    """Look up exchange tick size for a symbol from Dhan instrument master.

    Most Nifty 500 stocks have tick = 0.05 paise (5 paise).
    Some high-value stocks have tick = 0.10 or 0.50.
    """
    f = DATA_DIR / "nifty500.csv"
    if not f.exists():
        return 0.05
    try:
        df = pd.read_csv(f)
        row = df[df["symbol"] == symbol]
        if row.empty or "tick_size" not in row.columns:
            return 0.05
        ts = float(row["tick_size"].iloc[0])
        # tick_size in instrument master is in paise (e.g., 5 = 0.05 INR)
        return ts / 100 if ts >= 1 else ts
    except Exception:
        return 0.05


def round_to_tick(price: float, tick_size: float) -> float:
    """Round price DOWN to nearest tick (so limit orders are achievable on BUY)."""
    if tick_size <= 0:
        return round(price, 2)
    return round(math.floor(price / tick_size) * tick_size, 2)


def round_up_to_tick(price: float, tick_size: float) -> float:
    """Round price UP — use for SELL limits and stop-losses on long positions."""
    if tick_size <= 0:
        return round(price, 2)
    return round(math.ceil(price / tick_size) * tick_size, 2)


def reconcile_positions(local_positions: list[dict], broker_positions: list[dict]) -> dict:
    """Compare local DB positions vs broker reality.

    Returns dict of mismatches:
        {
          "missing_in_broker": [...]   — local thinks we hold, broker doesn't,
          "missing_in_local":  [...]   — broker has positions we don't track,
          "qty_mismatch":      [...]   — different quantities,
        }
    """
    local_map = {p["symbol"]: p for p in local_positions}
    broker_map = {p["symbol"]: p for p in broker_positions}

    missing_in_broker = []
    missing_in_local = []
    qty_mismatch = []

    for sym, lp in local_map.items():
        if sym not in broker_map:
            missing_in_broker.append(sym)
            continue
        bp = broker_map[sym]
        if int(lp.get("quantity", 0)) != int(bp.get("quantity", 0)):
            qty_mismatch.append({
                "symbol": sym,
                "local_qty": int(lp.get("quantity", 0)),
                "broker_qty": int(bp.get("quantity", 0)),
            })

    for sym in broker_map:
        if sym not in local_map:
            missing_in_local.append(sym)

    return {
        "missing_in_broker": missing_in_broker,
        "missing_in_local": missing_in_local,
        "qty_mismatch": qty_mismatch,
        "is_clean": (
            len(missing_in_broker) == 0
            and len(missing_in_local) == 0
            and len(qty_mismatch) == 0
        ),
    }
