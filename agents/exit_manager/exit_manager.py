"""Exit Manager — actively manages open positions with 3 exit rules.

Runs every 15 min during market hours. For each open position:

    1. TRAILING STOP
       Once price >= entry + 1.5*ATR → trail SL at (price - 1.5*ATR)
       SL only moves UP, never down.
       Locks in 70-80% of peak profit.

    2. PARTIAL PROFIT BOOKING
       Once price >= entry + 1R (1R = entry_price - initial_SL),
       sell 50% of position, move remaining SL to breakeven (entry).
       Result: zero risk on remaining position, full upside intact.

    3. REVERSAL EXIT
       If RSI > 75 AND today's close < today's open AND volume > 1.5x avg,
       EXIT NOW (don't wait for SL hit).
       Catches "blowoff top" reversals.

Config flag: `exit_management.active_from_date` controls activation date.
Today's positions can be excluded by setting this to tomorrow.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import yfinance as yf

DATA_DIR = Path(__file__).parent.parent.parent / "data"


@dataclass
class ExitDecision:
    symbol: str
    action: str                  # "hold" | "trail_sl" | "partial_book" | "exit"
    new_sl: Optional[float] = None
    sell_qty: Optional[int] = None
    exit_price: Optional[float] = None
    reason: str = ""


def _fetch_recent_bars(symbol: str, days: int = 30) -> pd.DataFrame:
    """Fetch recent daily bars + add basic indicators."""
    try:
        df = yf.download(f"{symbol}.NS", period=f"{days}d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return pd.DataFrame()
        if hasattr(df.columns, "get_level_values"):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]]

        # Quick indicators we need
        try:
            import pandas_ta as ta
            df["rsi"] = ta.rsi(df["close"], length=14)
            df["atr"] = ta.atr(df["high"], df["low"], df["close"], length=14)
        except Exception:
            df["rsi"] = 50.0
            df["atr"] = (df["high"] - df["low"]).rolling(14).mean()
        return df
    except Exception:
        return pd.DataFrame()


def evaluate_position(position: dict, df: pd.DataFrame, config: dict) -> ExitDecision:
    """Apply all 3 exit rules to a single position. Returns ONE decision."""
    symbol = str(position["symbol"])
    qty = int(position["quantity"])
    entry = float(position["entry_price"])
    cur_sl = float(position["stop_loss"])
    target = float(position["target"])
    initial_sl = float(position.get("initial_sl", cur_sl))
    initial_qty = int(position.get("initial_qty", qty))
    peak = float(position.get("peak_price", entry))
    partial_booked = str(position.get("partial_booked", "N")).upper() == "Y"

    if df is None or df.empty:
        return ExitDecision(symbol, "hold", reason="no data")

    last = df.iloc[-1]
    cur_price = float(last["close"])
    rsi = float(last.get("rsi", 50))
    atr = float(last.get("atr", cur_price * 0.02)) if not pd.isna(last.get("atr")) else cur_price * 0.02
    today_open = float(last["open"])

    avg_vol = float(df["volume"].iloc[-21:-1].mean()) if len(df) >= 22 else float(df["volume"].mean())
    today_vol = float(last["volume"])

    # Track peak
    new_peak = max(peak, cur_price)

    # ---- RULE 3: REVERSAL EXIT (highest priority) ----
    if rsi > 75 and cur_price < today_open and avg_vol > 0 and today_vol > 1.5 * avg_vol:
        return ExitDecision(
            symbol=symbol, action="exit",
            sell_qty=qty, exit_price=cur_price,
            reason=f"Reversal: RSI {rsi:.0f}, close < open, vol {today_vol/avg_vol:.1f}x",
        )

    # ---- RULE 2: PARTIAL BOOKING at 1R ----
    one_r = entry - initial_sl       # risk distance
    if not partial_booked and one_r > 0 and cur_price >= entry + one_r:
        sell_qty = max(1, initial_qty // 2)
        return ExitDecision(
            symbol=symbol, action="partial_book",
            sell_qty=sell_qty, exit_price=cur_price,
            new_sl=entry,            # move remaining SL to breakeven
            reason=f"1R booked at {cur_price:.2f}; SL → breakeven",
        )

    # ---- RULE 1: TRAILING STOP ----
    if cur_price >= entry + 1.5 * atr:
        proposed_sl = cur_price - 1.5 * atr
        if proposed_sl > cur_sl:        # only move SL UP
            return ExitDecision(
                symbol=symbol, action="trail_sl",
                new_sl=round(proposed_sl, 2),
                reason=f"Trail SL: {cur_sl:.2f} → {proposed_sl:.2f} (price {cur_price:.2f})",
            )

    return ExitDecision(symbol, "hold",
                         reason=f"price {cur_price:.2f}, SL {cur_sl:.2f}, peak {new_peak:.2f}")


def is_active(config: dict) -> bool:
    """Check if exit manager is active today (per config gate)."""
    em_cfg = config.get("exit_management", {})
    if not em_cfg.get("enabled", True):
        return False
    active_from = em_cfg.get("active_from_date", "")
    if active_from:
        try:
            from_date = pd.to_datetime(active_from).date()
            if date.today() < from_date:
                return False
        except Exception:
            pass
    return True
