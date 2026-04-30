"""Exit Manager runner — applies trailing SL, partial booking, reversal exits.

Run every 15 min via scheduler.

Reads:    data/positions.csv
Writes:   data/positions.csv (updated SL/qty), data/trades.csv (closed legs)
Emits:    event_bus events for dashboard
Sends:    Telegram alert on each exit / partial book / SL move
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import requests
from dotenv import load_dotenv

from utils.config_loader import load_config
from utils import event_bus, trade_store
from agents.exit_manager import (
    evaluate_position, _fetch_recent_bars, is_active,
)

DATA_DIR = Path(__file__).parent.parent / "data"
POS_FILE = DATA_DIR / "positions.csv"

load_dotenv()


def send_telegram(text: str) -> None:
    bot = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not bot or not chat:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot}/sendMessage",
            json={"chat_id": chat, "text": text}, timeout=10,
        )
    except Exception:
        pass


def main():
    config = load_config()

    if not is_active(config):
        print("Exit manager not active yet (per config gate). Skipping.")
        return

    if not POS_FILE.exists():
        print("No positions file - nothing to manage.")
        return

    df = pd.read_csv(POS_FILE)
    if df.empty:
        print("No open positions.")
        return

    print(f"\n{'#' * 60}")
    print(f"#  EXIT MANAGER  -  {datetime.now().strftime('%H:%M:%S')}")
    print(f"#  Managing {len(df)} open positions")
    print(f"{'#' * 60}\n")

    # Ensure tracking columns exist (backfill for legacy positions)
    if "initial_sl" not in df.columns:
        df["initial_sl"] = df["stop_loss"]
    if "initial_qty" not in df.columns:
        df["initial_qty"] = df["quantity"]
    if "peak_price" not in df.columns:
        df["peak_price"] = df["entry_price"]
    if "partial_booked" not in df.columns:
        df["partial_booked"] = "N"

    actions = 0
    rows_to_keep = []
    closed_trades = []

    for idx, pos in df.iterrows():
        sym = pos["symbol"]
        bars = _fetch_recent_bars(str(sym), days=30)
        decision = evaluate_position(pos.to_dict(), bars, config)

        if decision.action == "hold":
            # Update peak price
            if not bars.empty:
                cur_price = float(bars.iloc[-1]["close"])
                pos["peak_price"] = max(float(pos.get("peak_price", 0)), cur_price)
                pos["current_price"] = cur_price
                pos["unrealized_pnl"] = (cur_price - float(pos["entry_price"])) * float(pos["quantity"])
            rows_to_keep.append(pos)
            continue

        if decision.action == "trail_sl":
            print(f"  [TRAIL]  {sym}  SL: {pos['stop_loss']:.2f} → {decision.new_sl:.2f}")
            event_bus.emit("exit_manager", "trail_sl", decision.reason,
                           symbol=str(sym), level="info")
            pos["stop_loss"] = decision.new_sl
            if not bars.empty:
                pos["current_price"] = float(bars.iloc[-1]["close"])
                pos["peak_price"] = max(float(pos.get("peak_price", 0)),
                                          float(bars.iloc[-1]["close"]))
            rows_to_keep.append(pos)
            actions += 1
            send_telegram(f"[RDA] {sym} SL trailed to Rs.{decision.new_sl:.2f}")

        elif decision.action == "partial_book":
            sell_qty = decision.sell_qty
            remaining_qty = int(pos["quantity"]) - sell_qty
            print(f"  [BOOK]   {sym}  Sold {sell_qty} @ {decision.exit_price:.2f}, "
                  f"keeping {remaining_qty}, SL → breakeven {decision.new_sl:.2f}")
            pnl_partial = (decision.exit_price - float(pos["entry_price"])) * sell_qty

            # Log the partial close as a trade
            closed_trades.append({
                "symbol": sym, "quantity": sell_qty,
                "entry_price": float(pos["entry_price"]),
                "exit_price": decision.exit_price,
                "entry_time": pos.get("entry_time", ""),
                "exit_time": datetime.now().isoformat(timespec="seconds"),
                "pnl_gross": round(pnl_partial, 2),
                "pnl_net": round(pnl_partial - 40, 2),  # rough charges
                "holding_days": 0,
                "gain_type": "INTRADAY", "sector": pos.get("sector", ""),
                "strategy": pos.get("strategy", ""),
                "exit_reason": "partial_1R",
            })

            event_bus.emit("exit_manager", "partial_book",
                           f"Sold {sell_qty}, kept {remaining_qty}, P&L Rs.{pnl_partial:+.0f}",
                           symbol=str(sym), level="success")
            send_telegram(
                f"[RDA] {sym}: BOOKED 50%\n"
                f"Sold {sell_qty} @ Rs.{decision.exit_price:.2f}\n"
                f"P&L: Rs.{pnl_partial:+,.0f}\n"
                f"Holding {remaining_qty} with SL at breakeven Rs.{decision.new_sl:.2f}"
            )

            pos["quantity"] = remaining_qty
            pos["stop_loss"] = decision.new_sl
            pos["partial_booked"] = "Y"
            rows_to_keep.append(pos)
            actions += 1

        elif decision.action == "exit":
            print(f"  [EXIT]   {sym}  Full exit @ {decision.exit_price:.2f} - {decision.reason}")
            qty = int(pos["quantity"])
            pnl = (decision.exit_price - float(pos["entry_price"])) * qty
            closed_trades.append({
                "symbol": sym, "quantity": qty,
                "entry_price": float(pos["entry_price"]),
                "exit_price": decision.exit_price,
                "entry_time": pos.get("entry_time", ""),
                "exit_time": datetime.now().isoformat(timespec="seconds"),
                "pnl_gross": round(pnl, 2),
                "pnl_net": round(pnl - 40, 2),
                "holding_days": 0, "gain_type": "INTRADAY",
                "sector": pos.get("sector", ""),
                "strategy": pos.get("strategy", ""),
                "exit_reason": decision.reason[:40],
            })
            event_bus.emit("exit_manager", "full_exit",
                           f"Exit @ {decision.exit_price:.2f}, P&L Rs.{pnl:+.0f}",
                           symbol=str(sym), level="warning")
            send_telegram(
                f"[RDA] {sym}: FULL EXIT\n"
                f"Sold {qty} @ Rs.{decision.exit_price:.2f}\n"
                f"P&L: Rs.{pnl:+,.0f}\n"
                f"Reason: {decision.reason}"
            )
            actions += 1
            # Don't add to rows_to_keep — position closed

    # Save updated positions
    if rows_to_keep:
        new_df = pd.DataFrame(rows_to_keep)
        new_df.to_csv(POS_FILE, index=False)
    else:
        # All closed
        new_df = pd.DataFrame(columns=df.columns)
        new_df.to_csv(POS_FILE, index=False)

    # Append closed trades
    for trade in closed_trades:
        trade_store.append_trade(trade)

    print(f"\n  Actions: {actions} | Positions remaining: {len(rows_to_keep)} | "
          f"Closed legs: {len(closed_trades)}\n")


if __name__ == "__main__":
    main()
