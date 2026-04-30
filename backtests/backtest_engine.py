"""Backtest Engine — simulate strategies on historical data.

For each (strategy × symbol):
    1. Fetch 2 years of daily bars.
    2. Walk forward bar-by-bar — compute indicators using only past data.
    3. Generate signals; simulate paper orders with real SL/Target.
    4. Track equity curve, win rate, max drawdown, profit factor.

Output: data/backtest_results.csv  + per-trade log per strategy.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).parent.parent))

from agents.technical import TechnicalAgent


@dataclass
class BacktestTrade:
    symbol: str
    strategy: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    pnl: float
    pnl_pct: float
    holding_days: int
    exit_reason: str


@dataclass
class BacktestResult:
    strategy: str
    symbol: str
    start_capital: float
    end_capital: float
    total_return_pct: float
    n_trades: int
    n_wins: int
    win_rate_pct: float
    avg_win_pct: float
    avg_loss_pct: float
    profit_factor: float
    max_drawdown_pct: float
    sharpe: float
    trades: list[BacktestTrade] = field(default_factory=list)


def walk_forward_backtest(
    df: pd.DataFrame, strategy_fn, strategy_name: str, symbol: str,
    config: dict, train_window_days: int = 365, test_window_days: int = 90,
    step_days: int = 90,
) -> list:
    """Walk-forward validation — reduces overfitting risk.

    Splits history into rolling train/test windows. Backtests on each test
    window separately, returns list of BacktestResult per window.

    A strategy that's profitable in 3 of 4 walk-forward windows is genuinely robust;
    one that's profitable in 1 of 4 is overfit to a single market regime.
    """
    if df is None or len(df) < (train_window_days + test_window_days):
        return []

    results = []
    start_idx = train_window_days
    while start_idx + test_window_days <= len(df):
        end_idx = start_idx + test_window_days
        test_df = df.iloc[start_idx - 50: end_idx]   # 50-bar warmup
        res = backtest_strategy_on_symbol(
            test_df, strategy_fn, strategy_name, symbol, config, warmup_bars=50,
        )
        if res is not None:
            results.append({
                "window_start": str(df.index[start_idx]),
                "window_end": str(df.index[end_idx - 1]),
                "n_trades": res.n_trades,
                "win_rate_pct": res.win_rate_pct,
                "total_return_pct": res.total_return_pct,
                "max_drawdown_pct": res.max_drawdown_pct,
                "profit_factor": res.profit_factor,
            })
        start_idx += step_days

    return results


def _equity_drawdown(equity: list[float]) -> float:
    if not equity:
        return 0.0
    peak = equity[0]
    max_dd = 0.0
    for v in equity:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100 if peak > 0 else 0
        if dd > max_dd:
            max_dd = dd
    return max_dd


def _sharpe(returns: list[float]) -> float:
    if not returns:
        return 0.0
    arr = np.array(returns)
    if arr.std() == 0:
        return 0.0
    return float((arr.mean() / arr.std()) * np.sqrt(252))


def backtest_strategy_on_symbol(
    df: pd.DataFrame,
    strategy_fn: Callable,
    strategy_name: str,
    symbol: str,
    config: dict,
    capital: float = 100_000.0,
    risk_pct: float = 2.0,
    warmup_bars: int = 50,
    slippage_pct: float = 0.05,        # 5 bps per fill — realistic for liquid Nifty 500 stocks
    brokerage_per_trade: float = 20.0,
    stt_pct: float = 0.025,            # STT on sell side
) -> Optional[BacktestResult]:
    """Walk-forward backtest of a strategy on a single symbol."""
    if df is None or len(df) < warmup_bars + 30:
        return None

    technical = TechnicalAgent(config)
    df = technical.add_indicators(df)

    cash = capital
    position_qty = 0
    position_entry = 0.0
    position_sl = 0.0
    position_target = 0.0
    position_entry_date = None
    trades: list[BacktestTrade] = []
    equity_curve: list[float] = [capital]
    daily_returns: list[float] = []
    last_equity = capital

    for i in range(warmup_bars, len(df) - 1):
        bar = df.iloc[i]
        next_bar = df.iloc[i + 1]
        bar_date = df.index[i].strftime("%Y-%m-%d") if hasattr(df.index[i], "strftime") else str(df.index[i])
        next_date = df.index[i + 1].strftime("%Y-%m-%d") if hasattr(df.index[i + 1], "strftime") else str(df.index[i + 1])

        # If holding, check exits using NEXT bar OHLC (no look-ahead)
        if position_qty > 0:
            high, low = float(next_bar["high"]), float(next_bar["low"])
            exit_price = None
            exit_reason = ""
            if low <= position_sl:
                exit_price = position_sl
                exit_reason = "stoploss"
            elif high >= position_target:
                exit_price = position_target
                exit_reason = "target"
            if exit_price is not None:
                # Apply slippage on exit (worst case)
                exit_price *= (1 - slippage_pct / 100)
                # Charges: brokerage on entry+exit, STT on sell, GST 18% on brokerage
                charges = (brokerage_per_trade * 2
                            + (exit_price * position_qty) * stt_pct / 100
                            + brokerage_per_trade * 2 * 0.18)
                pnl = (exit_price - position_entry) * position_qty - charges
                pnl_pct = ((exit_price - position_entry) / position_entry * 100
                           - charges / (position_entry * position_qty) * 100)
                cash += position_qty * exit_price - charges
                trades.append(BacktestTrade(
                    symbol=symbol, strategy=strategy_name,
                    entry_date=str(position_entry_date),
                    exit_date=next_date,
                    entry_price=position_entry,
                    exit_price=exit_price,
                    quantity=position_qty,
                    pnl=pnl, pnl_pct=pnl_pct,
                    holding_days=(i + 1 - df.index.get_loc(pd.to_datetime(position_entry_date))
                                  if pd.to_datetime(position_entry_date) in df.index else 0),
                    exit_reason=exit_reason,
                ))
                position_qty = 0
                position_entry = 0.0
            continue       # one trade at a time per symbol

        # Look for entry on closed bar
        sub = df.iloc[: i + 1]
        try:
            signal = strategy_fn(sub, config, symbol=symbol)
        except Exception:
            signal = None
        if signal is None:
            continue

        # Position size by risk budget
        risk_amount = capital * risk_pct / 100
        risk_per_share = signal.entry_price - signal.stop_loss
        if risk_per_share <= 0:
            continue
        qty = int(risk_amount / risk_per_share)
        if qty <= 0:
            continue
        max_qty_by_capital = int((cash * 0.30) / signal.entry_price)
        qty = min(qty, max_qty_by_capital)
        if qty <= 0:
            continue

        # Apply slippage on entry (worst case - pay slightly more)
        actual_entry = signal.entry_price * (1 + slippage_pct / 100)
        cash -= qty * actual_entry
        position_qty = qty
        position_entry = actual_entry
        position_sl = signal.stop_loss
        position_target = signal.target
        position_entry_date = bar_date

        # Track daily equity
        current_equity = cash + position_qty * float(bar["close"])
        equity_curve.append(current_equity)
        if last_equity > 0:
            daily_returns.append((current_equity - last_equity) / last_equity)
        last_equity = current_equity

    # Close any open position at last bar
    if position_qty > 0:
        last_close = float(df.iloc[-1]["close"])
        cash += position_qty * last_close
        pnl = (last_close - position_entry) * position_qty
        trades.append(BacktestTrade(
            symbol=symbol, strategy=strategy_name,
            entry_date=str(position_entry_date),
            exit_date=df.index[-1].strftime("%Y-%m-%d") if hasattr(df.index[-1], "strftime") else str(df.index[-1]),
            entry_price=position_entry, exit_price=last_close,
            quantity=position_qty, pnl=pnl,
            pnl_pct=(last_close - position_entry) / position_entry * 100,
            holding_days=0, exit_reason="end_of_data",
        ))

    n = len(trades)
    wins = [t for t in trades if t.pnl > 0]
    losses = [t for t in trades if t.pnl <= 0]
    avg_win = np.mean([t.pnl_pct for t in wins]) if wins else 0
    avg_loss = np.mean([t.pnl_pct for t in losses]) if losses else 0
    sum_wins = sum(t.pnl for t in wins)
    sum_losses = abs(sum(t.pnl for t in losses))
    profit_factor = (sum_wins / sum_losses) if sum_losses > 0 else 0

    return BacktestResult(
        strategy=strategy_name,
        symbol=symbol,
        start_capital=capital,
        end_capital=round(cash, 2),
        total_return_pct=round((cash - capital) / capital * 100, 2),
        n_trades=n,
        n_wins=len(wins),
        win_rate_pct=round(len(wins) / n * 100, 2) if n else 0,
        avg_win_pct=round(avg_win, 2),
        avg_loss_pct=round(avg_loss, 2),
        profit_factor=round(profit_factor, 2),
        max_drawdown_pct=round(_equity_drawdown(equity_curve), 2),
        sharpe=round(_sharpe(daily_returns), 2),
        trades=trades,
    )
