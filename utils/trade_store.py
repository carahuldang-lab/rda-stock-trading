"""Trade Store — persistent CSV-based storage for trades, positions, signals.

Files written to data/:
    positions.csv  — currently open positions (live snapshot)
    trades.csv     — closed trades (audit trail, append-only)
    signals.csv    — every signal generated (executed or rejected)
    equity.csv     — daily equity curve snapshots

Phase 2 — migrate to SQLite for concurrent access.
"""
from __future__ import annotations

import csv
from datetime import datetime
from pathlib import Path
from typing import Optional

DATA_DIR = Path(__file__).parent.parent / "data"

POSITIONS_FILE = DATA_DIR / "positions.csv"
TRADES_FILE = DATA_DIR / "trades.csv"
SIGNALS_FILE = DATA_DIR / "signals.csv"
EQUITY_FILE = DATA_DIR / "equity.csv"

POSITION_HEADERS = [
    "symbol", "quantity", "entry_price", "entry_time", "stop_loss", "target",
    "current_price", "unrealized_pnl", "sector", "strategy",
    "initial_sl", "initial_qty", "peak_price", "partial_booked",
]
TRADE_HEADERS = [
    "symbol", "quantity", "entry_price", "exit_price", "entry_time", "exit_time",
    "pnl_gross", "pnl_net", "holding_days", "gain_type", "sector", "strategy",
    "exit_reason",
]
SIGNAL_HEADERS = [
    "timestamp", "symbol", "strategy", "signal_type", "entry_price",
    "stop_loss", "target", "confidence", "reasoning", "status", "rejection_reason",
]
EQUITY_HEADERS = [
    "date", "capital", "cash", "invested", "unrealized_pnl",
    "realized_pnl_today", "open_positions", "trades_today", "win_rate",
]


def _ensure_csv(path: Path, headers: list[str]) -> None:
    DATA_DIR.mkdir(exist_ok=True, parents=True)
    if not path.exists():
        with open(path, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(headers)


def append_signal(
    symbol: str,
    strategy: str,
    signal_type: str,
    entry_price: float,
    stop_loss: float,
    target: float,
    confidence: float,
    reasoning: str,
    status: str = "executed",        # executed | rejected | filtered
    rejection_reason: str = "",
) -> None:
    _ensure_csv(SIGNALS_FILE, SIGNAL_HEADERS)
    with open(SIGNALS_FILE, "a", newline="", encoding="utf-8") as f:
        csv.writer(f).writerow([
            datetime.now().isoformat(timespec="seconds"),
            symbol, strategy, signal_type,
            f"{entry_price:.2f}", f"{stop_loss:.2f}", f"{target:.2f}",
            f"{confidence:.2f}", reasoning, status, rejection_reason,
        ])


def write_positions(positions: list[dict]) -> None:
    """Overwrite positions.csv with current open positions snapshot."""
    _ensure_csv(POSITIONS_FILE, POSITION_HEADERS)
    with open(POSITIONS_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=POSITION_HEADERS, extrasaction="ignore")
        w.writeheader()
        for p in positions:
            w.writerow(p)


def append_trade(trade: dict) -> None:
    _ensure_csv(TRADES_FILE, TRADE_HEADERS)
    with open(TRADES_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=TRADE_HEADERS, extrasaction="ignore")
        w.writerow(trade)


def append_equity_snapshot(snap: dict) -> None:
    _ensure_csv(EQUITY_FILE, EQUITY_HEADERS)
    with open(EQUITY_FILE, "a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=EQUITY_HEADERS, extrasaction="ignore")
        w.writerow(snap)
