"""Intraday Scalp Scanner — runs every 5 minutes during market hours.

Scans top liquid Nifty 500 stocks on 5-minute bars for ORB (Opening Range Breakout)
and other intraday setups.

Run from project root:
    python scripts/scan_scalp.py --limit 30
"""
from __future__ import annotations

import argparse
import sys
import warnings
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd

from utils.config_loader import load_config
from utils import event_bus, trade_store
from agents.technical import TechnicalAgent
from agents.technical.timeframes import fetch_bars
from agents.risk import RiskAgent
from agents.execution import ExecutionAgent, Order
from agents.portfolio import PortfolioAgent, Position
from agents.research.market_regime import detect_regime, save_regime
from strategies.scalp_orb import generate_signal as orb_signal

warnings.filterwarnings("ignore")


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=30)
    p.add_argument("--paper-trade", action="store_true", default=True)
    return p.parse_args()


def main():
    args = parse_args()
    config = load_config()

    print(f"\n{'#' * 60}")
    print(f"#  RDA SCALP SCAN  -  {datetime.now().strftime('%H:%M:%S')}")
    print(f"{'#' * 60}\n")

    # Regime check FIRST — block scalps in CRASH/BEARISH
    print("Detecting market regime...")
    regime = detect_regime()
    save_regime(regime)
    print(f"  Regime: {regime.regime} (size mult: {regime.position_size_multiplier}x)")
    print(f"  {regime.reasoning}\n")

    if regime.regime in ("BEARISH", "CRASH"):
        print(f"  [BLOCKED] Regime={regime.regime} — no new scalps today")
        event_bus.emit("orchestrator", "scalp_blocked",
                       f"Regime={regime.regime}", level="warning")
        return

    DATA_DIR = Path(__file__).parent.parent / "data"
    master = pd.read_csv(DATA_DIR / "nifty500.csv")
    symbols = master["symbol"].dropna().head(args.limit).tolist()

    technical = TechnicalAgent(config)
    risk = RiskAgent(config)
    risk.regime_size_multiplier = regime.position_size_multiplier
    execution = ExecutionAgent(config)
    portfolio = PortfolioAgent(config)

    sector_map = dict(zip(master["symbol"], master.get("sector", "")))
    signals_fired = 0

    for sym in symbols:
        try:
            df = fetch_bars(sym, "5m", period="2d")
            if df.empty or len(df) < 6:
                continue

            sig = orb_signal(df, config, symbol=sym)
            if sig is None:
                continue

            event_bus.emit("technical", "scalp_signal",
                           f"{sig.signal_type.value} @ {sig.entry_price:.2f}",
                           symbol=sym, level="success")

            check = risk.validate_trade(
                symbol=sym, entry_price=sig.entry_price,
                stop_loss=sig.stop_loss, sector=sector_map.get(sym, "Unknown"),
                open_positions=[
                    {"symbol": s, "quantity": p.quantity,
                     "entry_price": p.entry_price, "sector": p.sector}
                    for s, p in portfolio.open_positions.items()
                ],
            )
            if not check.approved:
                continue

            order = execution.place_order(Order(
                symbol=sym, side=sig.signal_type.value,
                quantity=check.quantity, order_type="LIMIT",
                product_type="INTRADAY", price=sig.entry_price,
            ))
            portfolio.open_position(Position(
                symbol=sym, quantity=order.filled_qty,
                entry_price=order.avg_fill_price, entry_time=datetime.now(),
                stop_loss=sig.stop_loss, target=sig.target,
                sector=sector_map.get(sym, "Unknown"),
                strategy="scalp_orb",
            ))
            trade_store.append_signal(
                symbol=sym, strategy="scalp_orb",
                signal_type=sig.signal_type.value,
                entry_price=sig.entry_price, stop_loss=sig.stop_loss,
                target=sig.target, confidence=sig.confidence,
                reasoning=sig.reasoning, status="executed",
            )
            signals_fired += 1
            print(f"  [SCALP] {sym}: {sig.signal_type.value} @ {sig.entry_price:.2f}, "
                  f"SL {sig.stop_loss:.2f}, TGT {sig.target:.2f}")
        except Exception as e:
            continue

    print(f"\nScalp scan done: {signals_fired} signals fired across {len(symbols)} stocks\n")


if __name__ == "__main__":
    main()
