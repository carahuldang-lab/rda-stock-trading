"""Run backtests across multiple symbols and strategies — save aggregated results.

Run from project root:
    python backtests/run_backtest.py                    # default 50 stocks
    python backtests/run_backtest.py --limit 100        # 100 stocks
    python backtests/run_backtest.py --years 3          # 3 years history
"""
from __future__ import annotations

import argparse
import csv
import sys
import time
import warnings
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import pandas as pd
import yfinance as yf

from utils.config_loader import load_config
from backtests.backtest_engine import backtest_strategy_on_symbol
from strategies.momentum_breakout import generate_signal as momentum_signal
from strategies.mean_reversion import generate_signal as mean_rev_signal

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)

DATA_DIR = Path(__file__).parent.parent / "data"
RESULTS_FILE = DATA_DIR / "backtest_results.csv"
TRADES_FILE = DATA_DIR / "backtest_trades.csv"

from strategies.fortress import generate_signal as fortress_signal
from strategies.gap_up_momentum import generate_signal as gap_up_signal

STRATEGIES = {
    "fortress": fortress_signal,
    "gap_up_momentum": gap_up_signal,
    "momentum_breakout": momentum_signal,
    "mean_reversion": mean_rev_signal,
}


def parse_args():
    p = argparse.ArgumentParser()
    p.add_argument("--limit", type=int, default=50)
    p.add_argument("--years", type=int, default=2)
    return p.parse_args()


def main():
    args = parse_args()
    config = load_config()
    master = pd.read_csv(DATA_DIR / "nifty500.csv")
    symbols = master["symbol"].dropna().tolist()[:args.limit]

    print(f"\n{'#' * 60}")
    print(f"#  BACKTEST RUN")
    print(f"#  {len(symbols)} symbols x {len(STRATEGIES)} strategies x {args.years}y history")
    print(f"{'#' * 60}\n")

    # Bulk download
    print(f"Downloading {len(symbols)} symbols ({args.years}y)...")
    yf_tickers = [f"{s}.NS" for s in symbols]
    t0 = time.time()
    df_all = yf.download(
        yf_tickers, period=f"{args.years}y", interval="1d",
        progress=False, auto_adjust=True, group_by="ticker", threads=True,
    )
    print(f"Done in {time.time() - t0:.1f}s\n")

    sym_data = {}
    if isinstance(df_all.columns, pd.MultiIndex):
        for s in symbols:
            yf_s = f"{s}.NS"
            if yf_s in df_all.columns.get_level_values(0):
                sub = df_all[yf_s].dropna()
                if not sub.empty and len(sub) >= 100:
                    sub.columns = [c.lower() for c in sub.columns]
                    sym_data[s] = sub[["open", "high", "low", "close", "volume"]]

    print(f"Got data for {len(sym_data)}/{len(symbols)} symbols\n")
    print("Running backtests...")

    all_results = []
    all_trades = []

    for strat_name, strat_fn in STRATEGIES.items():
        wins = 0
        losses = 0
        total_pnl = 0.0
        traded_symbols = 0

        for i, (sym, df) in enumerate(sym_data.items(), 1):
            if i % 25 == 0:
                print(f"  [{strat_name}] progress {i}/{len(sym_data)}")
            res = backtest_strategy_on_symbol(
                df, strat_fn, strat_name, sym, config,
                capital=config["account"]["capital"],
                risk_pct=config["risk"]["risk_per_trade_pct"],
            )
            if res is None or res.n_trades == 0:
                continue
            traded_symbols += 1
            wins += res.n_wins
            losses += (res.n_trades - res.n_wins)
            total_pnl += (res.end_capital - res.start_capital)
            all_results.append({
                "strategy": res.strategy,
                "symbol": res.symbol,
                "trades": res.n_trades,
                "wins": res.n_wins,
                "win_rate_pct": res.win_rate_pct,
                "total_return_pct": res.total_return_pct,
                "avg_win_pct": res.avg_win_pct,
                "avg_loss_pct": res.avg_loss_pct,
                "profit_factor": res.profit_factor,
                "max_drawdown_pct": res.max_drawdown_pct,
                "sharpe": res.sharpe,
            })
            for t in res.trades:
                all_trades.append({
                    "strategy": t.strategy, "symbol": t.symbol,
                    "entry_date": t.entry_date, "exit_date": t.exit_date,
                    "entry_price": t.entry_price, "exit_price": t.exit_price,
                    "quantity": t.quantity, "pnl": round(t.pnl, 2),
                    "pnl_pct": round(t.pnl_pct, 2),
                    "exit_reason": t.exit_reason,
                })

        total_trades = wins + losses
        wr = (wins / total_trades * 100) if total_trades else 0
        print(f"\n  [{strat_name}] symbols traded: {traded_symbols} | "
              f"total trades: {total_trades} | win rate: {wr:.1f}% | "
              f"net P&L: Rs.{total_pnl:,.0f}")

    # Save results
    DATA_DIR.mkdir(exist_ok=True)
    if all_results:
        with open(RESULTS_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(all_results[0].keys()))
            w.writeheader(); w.writerows(all_results)
        print(f"\n  Saved {len(all_results)} per-symbol summaries to {RESULTS_FILE}")
    if all_trades:
        with open(TRADES_FILE, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=list(all_trades[0].keys()))
            w.writeheader(); w.writerows(all_trades)
        print(f"  Saved {len(all_trades)} individual trades to {TRADES_FILE}")

    print("\n[DONE]\n")


if __name__ == "__main__":
    main()
