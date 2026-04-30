"""Bulk Universe Scanner — scans all (or top N) stocks from Nifty 500.

Differences vs run_paper_demo.py:
    1. Uses BULK yfinance download (one network call, ~30 sec for 500 stocks).
    2. Scores EVERY stock — not just buy-or-pass.
    3. Saves top candidates to data/candidates.csv for the dashboard.
    4. Optionally executes paper trades for any A/A+ grade stocks.

Run from project root:
    python scripts/scan_universe.py                 # scan all 500 stocks
    python scripts/scan_universe.py --limit 100     # scan first 100
    python scripts/scan_universe.py --paper-trade   # also execute trades
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path
from typing import Optional

# Make project root importable
sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import yfinance as yf

from utils.config_loader import load_config
from utils.logger import get_logger
from utils import event_bus, trade_store
from agents.technical import TechnicalAgent, score_stock, CandidateScore
from agents.risk import RiskAgent
from agents.execution import ExecutionAgent, Order
from agents.portfolio import PortfolioAgent, Position
from strategies.momentum_breakout import generate_signal as momentum_signal
from strategies.mean_reversion import generate_signal as mean_rev_signal

STRATEGIES = {
    "momentum_breakout": momentum_signal,
    "mean_reversion": mean_rev_signal,
}

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)
log = get_logger("scan_universe")

DATA_DIR = Path(__file__).parent.parent / "data"
CANDIDATES_FILE = DATA_DIR / "candidates.csv"


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=0,
                   help="Scan only first N stocks (0 = all)")
    p.add_argument("--paper-trade", action="store_true",
                   help="Execute paper trades for A/A+ grade signals")
    p.add_argument("--days", type=int, default=120,
                   help="Days of history to download")
    return p.parse_args()


def bulk_download(symbols: list[str], days: int) -> dict[str, pd.DataFrame]:
    """Download all symbols at once. Returns dict {symbol: df}."""
    yf_tickers = [s + ".NS" for s in symbols]
    print(f"  Downloading {len(yf_tickers)} symbols in bulk...")
    t0 = time.time()
    df = yf.download(
        yf_tickers,
        period=f"{days}d",
        interval="1d",
        progress=False,
        auto_adjust=True,
        group_by="ticker",
        threads=True,
    )
    print(f"  Done in {time.time() - t0:.1f}s")

    result = {}
    if isinstance(df.columns, pd.MultiIndex):
        # Multi-symbol response: columns = (symbol, field)
        for sym in symbols:
            yf_sym = sym + ".NS"
            if yf_sym in df.columns.get_level_values(0):
                sub = df[yf_sym].dropna()
                if not sub.empty and len(sub) >= 25:
                    sub.columns = [c.lower() for c in sub.columns]
                    sub = sub[["open", "high", "low", "close", "volume"]]
                    result[sym] = sub
    else:
        # Single symbol fallback
        if not df.empty:
            df.columns = [c.lower() for c in df.columns]
            result[symbols[0]] = df[["open", "high", "low", "close", "volume"]]
    return result


def main():
    args = parse_args()

    print("\n" + "#" * 60)
    print(f"#  RDA STOCK TRADING — BULK UNIVERSE SCAN")
    print(f"#  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("#" * 60 + "\n")

    config = load_config()
    master_path = DATA_DIR / "nifty500.csv"
    if not master_path.exists():
        sys.exit("Run scripts/build_instrument_master.py first.")
    master = pd.read_csv(master_path)

    # Build symbol list
    symbols = master["symbol"].dropna().tolist()
    if args.limit > 0:
        symbols = symbols[:args.limit]
    print(f"Universe: {len(symbols)} symbols\n")

    event_bus.emit("orchestrator", "bulk_scan_started",
                   f"Scanning {len(symbols)} stocks", level="info")

    # Init agents
    technical = TechnicalAgent(config)
    risk = RiskAgent(config)
    execution = ExecutionAgent(config)
    portfolio = PortfolioAgent(config)

    # Bulk download
    data = bulk_download(symbols, args.days)
    print(f"  Got data for {len(data)}/{len(symbols)} symbols\n")

    # Score and signal each
    candidates: list[CandidateScore] = []
    signals_executed = 0
    signals_rejected = 0
    skipped = 0

    sector_map = dict(zip(master["symbol"], master.get("sector", "")))

    print("Scoring & signaling...")
    for i, symbol in enumerate(symbols, 1):
        if i % 50 == 0:
            print(f"  Progress: {i}/{len(symbols)}")

        df = data.get(symbol)
        if df is None or df.empty:
            skipped += 1
            continue

        try:
            df = technical.add_indicators(df)
            sector = sector_map.get(symbol, "Unknown")

            # Score every stock
            score = score_stock(df, symbol=symbol, sector=sector)
            if score is None:
                skipped += 1
                continue
            candidates.append(score)

            # Run BOTH strategies — first one to fire wins
            signal = None
            for strat_name, strat_fn in STRATEGIES.items():
                try:
                    sig = strat_fn(df, config, symbol=symbol)
                    if sig is not None:
                        signal = sig
                        break
                except Exception:
                    continue
            if signal is None:
                continue

            # Got a BUY signal — log it
            event_bus.emit("technical", "signal_generated",
                           f"BUY @ {signal.entry_price:.2f} | grade={score.grade} | score={score.score}",
                           symbol=symbol, level="success")

            if not args.paper_trade:
                trade_store.append_signal(
                    symbol=symbol, strategy=signal.strategy_name,
                    signal_type=signal.signal_type.value,
                    entry_price=signal.entry_price,
                    stop_loss=signal.stop_loss, target=signal.target,
                    confidence=signal.confidence, reasoning=signal.reasoning,
                    status="not_traded", rejection_reason="paper-trade flag off",
                )
                signals_executed += 1
                continue

            # Paper-trade flag is on — validate + execute
            check = risk.validate_trade(
                symbol=symbol, entry_price=signal.entry_price,
                stop_loss=signal.stop_loss, sector=sector,
                open_positions=[
                    {"symbol": s, "quantity": p.quantity,
                     "entry_price": p.entry_price, "sector": p.sector}
                    for s, p in portfolio.open_positions.items()
                ],
            )
            if not check.approved:
                event_bus.emit("risk", "trade_rejected", check.reason,
                               symbol=symbol, level="warning")
                trade_store.append_signal(
                    symbol=symbol, strategy=signal.strategy_name,
                    signal_type=signal.signal_type.value,
                    entry_price=signal.entry_price, stop_loss=signal.stop_loss,
                    target=signal.target, confidence=signal.confidence,
                    reasoning=signal.reasoning,
                    status="rejected", rejection_reason=check.reason,
                )
                signals_rejected += 1
                continue

            order = execution.place_order(Order(
                symbol=symbol, side="BUY", quantity=check.quantity,
                order_type="LIMIT", product_type="INTRADAY",
                price=signal.entry_price,
            ))
            portfolio.open_position(Position(
                symbol=symbol, quantity=order.filled_qty,
                entry_price=order.avg_fill_price, entry_time=datetime.now(),
                stop_loss=signal.stop_loss, target=signal.target,
                sector=sector, strategy=signal.strategy_name,
            ))
            event_bus.emit("execution", "order_filled",
                           f"qty={order.filled_qty} @ {order.avg_fill_price:.2f}",
                           symbol=symbol, level="success")
            trade_store.append_signal(
                symbol=symbol, strategy=signal.strategy_name,
                signal_type=signal.signal_type.value,
                entry_price=signal.entry_price, stop_loss=signal.stop_loss,
                target=signal.target, confidence=signal.confidence,
                reasoning=signal.reasoning, status="executed",
            )
            signals_executed += 1

        except Exception as e:
            log.warning("Failed for {}: {}", symbol, e)
            skipped += 1
            continue

    # Save candidates ranked by score
    candidates_sorted = sorted(candidates, key=lambda c: c.score, reverse=True)
    DATA_DIR.mkdir(exist_ok=True)
    with open(CANDIDATES_FILE, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow([
            "rank", "symbol", "sector", "grade", "score", "last_close",
            "rsi", "distance_from_high_pct", "volume_ratio", "ema_aligned",
            "trend_pct",
        ])
        for rank, c in enumerate(candidates_sorted, 1):
            w.writerow([
                rank, c.symbol, c.sector, c.grade, c.score, c.last_close,
                c.rsi, c.distance_from_high_pct, c.volume_ratio,
                "Y" if c.ema_aligned else "N", c.trend_pct,
            ])
    print(f"\n  Saved {len(candidates_sorted)} candidates to {CANDIDATES_FILE}")

    # Update positions snapshot for dashboard
    open_positions_data = [
        {
            "symbol": s, "quantity": p.quantity, "entry_price": p.entry_price,
            "entry_time": p.entry_time.isoformat(timespec="seconds"),
            "stop_loss": p.stop_loss, "target": p.target,
            "current_price": p.current_price or p.entry_price,
            "unrealized_pnl": (p.current_price - p.entry_price) * p.quantity if p.current_price else 0.0,
            "sector": p.sector, "strategy": p.strategy,
        }
        for s, p in portfolio.open_positions.items()
    ]
    if open_positions_data or args.paper_trade:
        trade_store.write_positions(open_positions_data)

    # Equity snapshot
    snap = portfolio.daily_snapshot()
    trade_store.append_equity_snapshot({
        "date": datetime.now().date().isoformat(),
        "capital": snap.capital, "cash": snap.cash, "invested": snap.invested,
        "unrealized_pnl": snap.unrealized_pnl,
        "realized_pnl_today": snap.realized_pnl_today,
        "open_positions": snap.open_positions,
        "trades_today": snap.trades_today, "win_rate": snap.win_rate,
    })

    # Summary
    print("\n" + "=" * 60)
    print("  SCAN SUMMARY")
    print("=" * 60)
    print(f"  Universe scanned:     {len(symbols)}")
    print(f"  Successfully scored:  {len(candidates)}")
    print(f"  Skipped:              {skipped}")
    print(f"  BUY signals:          {signals_executed}")
    print(f"  Rejected by risk:     {signals_rejected}")
    print(f"  Open paper positions: {snap.open_positions}")

    print("\n  TOP 10 CANDIDATES (closest to breakout):")
    print(f"  {'Rank':<5}{'Symbol':<14}{'Grade':<7}{'Score':<8}{'RSI':<7}{'Dist%':<8}{'Sector':<25}")
    for c in candidates_sorted[:10]:
        print(f"  {candidates_sorted.index(c)+1:<5}{c.symbol:<14}{c.grade:<7}"
              f"{c.score:<8.1f}{c.rsi:<7.1f}{c.distance_from_high_pct:<8.2f}{c.sector[:24]:<25}")

    event_bus.emit("orchestrator", "bulk_scan_completed",
                   f"scored={len(candidates)}, signals={signals_executed}, "
                   f"rejected={signals_rejected}, skipped={skipped}",
                   level="info")
    print("\n[DONE]\n")


if __name__ == "__main__":
    main()
