"""Auto-Scheduler — runs the trading bot during market hours.

Schedule (IST):
    09:00  pre-market: refresh fundamentals, news (skip on weekends)
    09:15  market open — start scan loop
    every 60 min during market: run scan_universe.py
    15:15  square-off: close all intraday positions (when LIVE)
    15:45  EOD: send Telegram daily summary

Run this from a SECOND terminal during market hours. It blocks until you Ctrl+C.

    python scheduler.py
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, time as dt_time, timedelta
from pathlib import Path

import pandas as pd
import requests
import schedule
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent))

from utils.logger import get_logger
from utils import event_bus
from utils.config_loader import load_config

log = get_logger("scheduler")
load_dotenv()
config = load_config()

PROJECT_ROOT = Path(__file__).parent
DATA_DIR = PROJECT_ROOT / "data"
PYTHON_EXE = sys.executable     # current venv's python


# ---------------------------------------------------------------
# Telegram helper
# ---------------------------------------------------------------
def send_telegram(text: str) -> bool:
    bot = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not bot or not chat:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{bot}/sendMessage",
            json={"chat_id": chat, "text": text}, timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        log.warning("Telegram failed: {}", e)
        return False


def is_market_open() -> bool:
    now = datetime.now()
    if now.weekday() >= 5:        # Sat/Sun
        return False
    return dt_time(9, 15) <= now.time() <= dt_time(15, 30)


def is_market_holiday() -> bool:
    """Stub — extend with NSE holiday calendar."""
    return False


# ---------------------------------------------------------------
# Scheduled tasks
# ---------------------------------------------------------------
def task_premarket():
    log.info("Pre-market: refreshing news + fundamentals")
    event_bus.emit("scheduler", "premarket_started", "", level="info")
    # News refresh (top 100 stocks for speed)
    subprocess.run(
        [PYTHON_EXE, "-m", "agents.research.news_scraper"],
        cwd=PROJECT_ROOT,
    )
    send_telegram(
        f"[{datetime.now().strftime('%H:%M')}] Pre-market refresh done. "
        f"Bot ready for market open."
    )


def task_scan():
    """Daily/swing universe scan — runs hourly."""
    if not is_market_open():
        log.info("Market closed - skipping scan")
        return
    log.info("Running universe scan (swing strategies)")
    event_bus.emit("scheduler", "scan_triggered", "", level="info")
    subprocess.run(
        [PYTHON_EXE, "scripts/scan_universe.py", "--limit", "200", "--paper-trade"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )


def task_scalp_scan():
    """Intraday scalp scan — runs every 5 minutes during market."""
    if not is_market_open():
        return
    log.info("Running scalp scan (5min ORB)")
    subprocess.run(
        [PYTHON_EXE, "scripts/scan_scalp.py", "--limit", "30"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )


def task_exit_manager():
    """Exit Manager — trailing SL, partial booking, reversal exits. Every 15 min."""
    if not is_market_open():
        return
    log.info("Running exit manager")
    subprocess.run(
        [PYTHON_EXE, "scripts/manage_exits.py"],
        cwd=PROJECT_ROOT, capture_output=True, text=True,
    )


def task_weekly_rebalance():
    """Monday morning — close stale positions, refresh top fortress candidates."""
    if datetime.now().weekday() != 0:    # Monday only
        return
    log.info("Running weekly rebalance")
    event_bus.emit("scheduler", "weekly_rebalance", "", level="info")
    # Refresh fundamentals + analyst reports (top 100 stocks)
    subprocess.run(
        [PYTHON_EXE, "-m", "agents.fundamental.screener", "100"],
        cwd=PROJECT_ROOT,
    )
    subprocess.run(
        [PYTHON_EXE, "-c",
         "import sys; sys.path.insert(0, '.'); "
         "from agents.research.analyst_reports import refresh_reports; "
         "import pandas as pd; "
         "refresh_reports(pd.read_csv('data/nifty500.csv')['symbol'].head(100).tolist())"],
        cwd=PROJECT_ROOT,
    )
    send_telegram(f"[RDA] Weekly rebalance done at {datetime.now().strftime('%H:%M')}.")


def task_eod_report():
    log.info("Sending EOD report")
    event_bus.emit("scheduler", "eod_report", "", level="info")
    try:
        eq_path = DATA_DIR / "equity.csv"
        pos_path = DATA_DIR / "positions.csv"
        sig_path = DATA_DIR / "signals.csv"

        eq = pd.read_csv(eq_path) if eq_path.exists() else pd.DataFrame()
        pos = pd.read_csv(pos_path) if pos_path.exists() else pd.DataFrame()
        sig = pd.read_csv(sig_path) if sig_path.exists() else pd.DataFrame()

        today = datetime.now().date().isoformat()
        eq_today = eq[eq["date"].astype(str) == today] if not eq.empty else eq
        sig_today = sig[sig["timestamp"].astype(str).str.startswith(today)] if not sig.empty else sig

        msg = [
            f"[RDA] Daily Summary - {datetime.now().strftime('%d %b %Y')}",
            f"Mode: {os.getenv('TRADING_MODE', 'PAPER')}",
            "",
        ]
        if not eq_today.empty:
            row = eq_today.iloc[-1]
            msg.append(f"Capital: Rs.{row.get('capital', 0):,.0f}")
            msg.append(f"Realized today: Rs.{row.get('realized_pnl_today', 0):+,.0f}")
            msg.append(f"Unrealized: Rs.{row.get('unrealized_pnl', 0):+,.0f}")
            msg.append(f"Open positions: {int(row.get('open_positions', 0))}")
        msg.append(f"Signals today: {len(sig_today)}")
        if not sig_today.empty and "status" in sig_today.columns:
            executed = (sig_today["status"] == "executed").sum()
            rejected = (sig_today["status"] == "rejected").sum()
            msg.append(f"  Executed: {executed} | Rejected: {rejected}")
        if not pos.empty:
            msg.append("")
            msg.append("Open positions:")
            for _, r in pos.head(5).iterrows():
                msg.append(f"  {r['symbol']} x{int(r['quantity'])} @ {float(r['entry_price']):.2f}")
        send_telegram("\n".join(msg))
    except Exception as e:
        log.exception("EOD report failed: {}", e)


# ---------------------------------------------------------------
# Schedule registration
# ---------------------------------------------------------------
# Pre-market
schedule.every().day.at("08:55").do(task_weekly_rebalance)   # only fires on Monday
schedule.every().day.at("09:00").do(task_premarket)

# Hourly swing scans (Fortress, Momentum, Mean-Reversion)
schedule.every().day.at("09:30").do(task_scan)
schedule.every().day.at("10:30").do(task_scan)
schedule.every().day.at("11:30").do(task_scan)
schedule.every().day.at("12:30").do(task_scan)
schedule.every().day.at("13:30").do(task_scan)
schedule.every().day.at("14:30").do(task_scan)

# Intraday scalps every 5 min from 09:30 - 14:00
for hh in range(9, 14):
    for mm in (35, 40, 45, 50, 55, 0, 5, 10, 15, 20, 25, 30):
        try:
            schedule.every().day.at(f"{hh:02d}:{mm:02d}").do(task_scalp_scan)
        except Exception:
            pass

# Exit manager every 15 min during market hours (09:30 - 15:15)
for hh in range(9, 16):
    for mm in (0, 15, 30, 45):
        try:
            schedule.every().day.at(f"{hh:02d}:{mm:02d}").do(task_exit_manager)
        except Exception:
            pass

# EOD
schedule.every().day.at("15:45").do(task_eod_report)


# ---------------------------------------------------------------
# Main loop
# ---------------------------------------------------------------
def main():
    log.info("Scheduler started. Press Ctrl+C to stop.")
    log.info("Next run: {}", schedule.next_run())
    event_bus.emit("scheduler", "started",
                   f"Mode={os.getenv('TRADING_MODE', 'PAPER')}",
                   level="success")
    send_telegram(
        f"[RDA] Scheduler started at {datetime.now().strftime('%H:%M')} "
        f"({os.getenv('TRADING_MODE', 'PAPER')} mode)."
    )

    try:
        while True:
            schedule.run_pending()
            time.sleep(30)
    except KeyboardInterrupt:
        log.info("Scheduler stopped by user")
        event_bus.emit("scheduler", "stopped", "User Ctrl+C", level="warning")
        send_telegram(f"[RDA] Scheduler stopped at {datetime.now().strftime('%H:%M')}.")


if __name__ == "__main__":
    main()
