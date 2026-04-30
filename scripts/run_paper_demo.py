"""End-to-end paper trade demo — one-shot run.

This is your FIRST FULL PIPELINE TEST. It:
    1. Loads the Nifty 500 master file.
    2. Picks the top 10 large-caps (RELIANCE, TCS, INFY, HDFCBANK, ICICIBANK,
       HDFCLIFE, BHARTIARTL, SBIN, LT, ITC).
    3. Downloads last 120 daily bars from yfinance for each.
    4. Computes indicators (RSI, EMA, ATR, MACD).
    5. Runs the momentum_breakout strategy on each.
    6. For each BUY signal:
         - Risk Agent validates the trade (sizing, exposure caps).
         - Execution Agent places a SIMULATED order (PAPER mode).
         - Portfolio Agent records the position.
    7. Sends a Telegram summary.
    8. Prints a clean report.

Run from project root:
    python scripts/run_paper_demo.py

This is read-only on real money — no real orders placed. PAPER mode only.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

# Make project root importable
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv
import pandas as pd
import yfinance as yf
import requests

from utils.config_loader import load_config
from utils.logger import get_logger
from utils import event_bus
from utils import trade_store
from agents.technical import TechnicalAgent
from agents.risk import RiskAgent
from agents.execution import ExecutionAgent, Order
from agents.portfolio import PortfolioAgent, Position
from strategies.momentum_breakout import generate_signal as momentum_signal

log = get_logger("paper_demo")

# Top 10 large-caps for demo (avoid scanning all 500 in first run)
DEMO_SYMBOLS = [
    "RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK",
    "HDFCLIFE", "BHARTIARTL", "SBIN", "LT", "ITC",
]


def fetch_history(symbol: str, days: int = 120) -> pd.DataFrame:
    """Fetch daily OHLCV from yfinance."""
    df = yf.download(
        f"{symbol}.NS",
        period=f"{days}d",
        interval="1d",
        progress=False,
        auto_adjust=True,
    )
    if df is None or df.empty:
        return pd.DataFrame()
    # Flatten multi-index columns if present (yfinance sometimes returns them)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df.columns = [c.lower() for c in df.columns]
    return df[["open", "high", "low", "close", "volume"]]


def send_telegram_summary(messages: list[str]) -> None:
    """Send the demo run summary to Telegram."""
    load_dotenv()
    bot = os.getenv("TELEGRAM_BOT_TOKEN")
    chat = os.getenv("TELEGRAM_CHAT_ID")
    if not bot or not chat:
        return
    text = "\n".join(messages)
    try:
        requests.post(
            f"https://api.telegram.org/bot{bot}/sendMessage",
            json={"chat_id": chat, "text": text},
            timeout=10,
        )
    except Exception as e:
        log.warning("Telegram send failed: {}", e)


def get_sector(symbol: str, master: pd.DataFrame) -> str:
    row = master[master["symbol"] == symbol]
    return row["sector"].iloc[0] if not row.empty else "Unknown"


def main() -> None:
    print("\n" + "#" * 60)
    print("#  RDA STOCK TRADING — PAPER DEMO RUN")
    print(f"#  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("#" * 60 + "\n")

    # 1. Load config + instrument master
    config = load_config()
    master_path = Path(__file__).parent.parent / "data" / "nifty500.csv"
    if not master_path.exists():
        sys.exit("Run scripts/build_instrument_master.py first.")
    master = pd.read_csv(master_path)

    # 2. Init agents
    technical = TechnicalAgent(config)
    risk = RiskAgent(config)
    execution = ExecutionAgent(config)
    portfolio = PortfolioAgent(config)

    log.info("Mode: {} | Capital: Rs.{:,}",
             execution.mode, config["account"]["capital"])
    event_bus.emit("orchestrator", "scan_started",
                   f"Mode={execution.mode}, scanning {len(DEMO_SYMBOLS)} stocks",
                   level="info")

    signals_found = []
    rejected = []

    # 3. Process each symbol
    for symbol in DEMO_SYMBOLS:
        print(f"  -> {symbol:12s}", end=" ")
        try:
            event_bus.emit("research", "scan_symbol",
                           f"Checking F&O ban / earnings / news",
                           symbol=symbol)

            df = fetch_history(symbol)
            if df.empty or len(df) < 50:
                print("[skip] insufficient history")
                event_bus.emit("technical", "skip", "insufficient history",
                               symbol=symbol, level="warning")
                continue

            df = technical.add_indicators(df)
            signal = momentum_signal(df, config, symbol=symbol)

            if signal is None:
                last_close = df["close"].iloc[-1]
                last_rsi = df["rsi"].iloc[-1]
                print(f"[no signal] close=Rs.{last_close:.2f} RSI={last_rsi:.1f}")
                event_bus.emit("technical", "no_signal",
                               f"close={last_close:.2f}, RSI={last_rsi:.1f}",
                               symbol=symbol)
                continue

            print(f"[BUY] entry=Rs.{signal.entry_price:.2f} "
                  f"SL=Rs.{signal.stop_loss:.2f} target=Rs.{signal.target:.2f}")
            event_bus.emit("technical", "signal_generated",
                           f"BUY @ {signal.entry_price:.2f} | SL {signal.stop_loss:.2f} "
                           f"| target {signal.target:.2f} | {signal.reasoning}",
                           symbol=symbol, level="success")

            # 4. Risk validation
            sector = get_sector(symbol, master)
            check = risk.validate_trade(
                symbol=symbol,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss,
                sector=sector,
                open_positions=[
                    {"symbol": s, "quantity": p.quantity,
                     "entry_price": p.entry_price, "sector": p.sector}
                    for s, p in portfolio.open_positions.items()
                ],
            )
            if not check.approved:
                print(f"               [REJECTED] {check.reason}")
                event_bus.emit("risk", "trade_rejected", check.reason,
                               symbol=symbol, level="warning")
                trade_store.append_signal(
                    symbol=symbol, strategy=signal.strategy_name,
                    signal_type=signal.signal_type.value,
                    entry_price=signal.entry_price,
                    stop_loss=signal.stop_loss, target=signal.target,
                    confidence=signal.confidence, reasoning=signal.reasoning,
                    status="rejected", rejection_reason=check.reason,
                )
                rejected.append((symbol, check.reason))
                continue

            event_bus.emit("risk", "trade_approved",
                           f"qty={check.quantity}, sector={sector}",
                           symbol=symbol, level="success")

            # 5. Place paper order
            order = Order(
                symbol=symbol,
                side="BUY",
                quantity=check.quantity,
                order_type="LIMIT",
                product_type="INTRADAY",
                price=signal.entry_price,
            )
            order = execution.place_order(order)
            event_bus.emit("execution", "order_filled",
                           f"qty={order.filled_qty} @ Rs.{order.avg_fill_price:.2f} "
                           f"({execution.mode})",
                           symbol=symbol, level="success")

            # 6. Record position
            portfolio.open_position(Position(
                symbol=symbol,
                quantity=order.filled_qty,
                entry_price=order.avg_fill_price,
                entry_time=datetime.now(),
                stop_loss=signal.stop_loss,
                target=signal.target,
                sector=sector,
                strategy=signal.strategy_name,
            ))
            event_bus.emit("portfolio", "position_opened",
                           f"capital used Rs.{order.filled_qty * signal.entry_price:,.0f}",
                           symbol=symbol)

            trade_store.append_signal(
                symbol=symbol, strategy=signal.strategy_name,
                signal_type=signal.signal_type.value,
                entry_price=signal.entry_price,
                stop_loss=signal.stop_loss, target=signal.target,
                confidence=signal.confidence, reasoning=signal.reasoning,
                status="executed",
            )

            risk_amount = (signal.entry_price - signal.stop_loss) * order.filled_qty
            print(f"               [FILLED] qty={order.filled_qty} "
                  f"capital_used=Rs.{order.filled_qty * signal.entry_price:,.0f} "
                  f"risk=Rs.{risk_amount:,.0f}")
            signals_found.append((symbol, signal, order))

        except Exception as e:
            print(f"[ERROR] {e}")
            log.exception("Failed for {}", symbol)
            event_bus.emit("orchestrator", "error", str(e), symbol=symbol, level="error")

    # 7. Persist data for dashboard
    open_positions_data = [
        {
            "symbol": s,
            "quantity": p.quantity,
            "entry_price": p.entry_price,
            "entry_time": p.entry_time.isoformat(timespec="seconds"),
            "stop_loss": p.stop_loss,
            "target": p.target,
            "current_price": p.current_price or p.entry_price,
            "unrealized_pnl": (p.current_price - p.entry_price) * p.quantity if p.current_price else 0.0,
            "sector": p.sector,
            "strategy": p.strategy,
        }
        for s, p in portfolio.open_positions.items()
    ]
    trade_store.write_positions(open_positions_data)

    # 8. Summary
    print("\n" + "=" * 60)
    print("  SUMMARY")
    print("=" * 60)

    snap = portfolio.daily_snapshot()
    trade_store.append_equity_snapshot({
        "date": datetime.now().date().isoformat(),
        "capital": snap.capital,
        "cash": snap.cash,
        "invested": snap.invested,
        "unrealized_pnl": snap.unrealized_pnl,
        "realized_pnl_today": snap.realized_pnl_today,
        "open_positions": snap.open_positions,
        "trades_today": snap.trades_today,
        "win_rate": snap.win_rate,
    })
    event_bus.emit("orchestrator", "scan_completed",
                   f"signals={len(signals_found)}, rejected={len(rejected)}, "
                   f"open_positions={snap.open_positions}",
                   level="info")
    print(f"  Mode:               {execution.mode}")
    print(f"  Symbols scanned:    {len(DEMO_SYMBOLS)}")
    print(f"  BUY signals:        {len(signals_found)}")
    print(f"  Rejected by risk:   {len(rejected)}")
    print(f"  Open positions:     {snap.open_positions}")
    print(f"  Capital invested:   Rs.{snap.invested:,.0f}")
    print(f"  Cash remaining:     Rs.{snap.cash:,.0f}")

    if signals_found:
        print("\n  Open paper positions:")
        for symbol, sig, order in signals_found:
            print(f"    {symbol:12s} qty={order.filled_qty:4d} @ Rs.{order.avg_fill_price:.2f}  "
                  f"SL=Rs.{sig.stop_loss:.2f}  Target=Rs.{sig.target:.2f}")

    # 8. Telegram alert
    msgs = [
        f"[RDA] Paper Demo Run — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"Mode: {execution.mode}",
        f"Scanned: {len(DEMO_SYMBOLS)} stocks",
        f"BUY signals: {len(signals_found)}",
        f"Rejected: {len(rejected)}",
        f"Capital deployed: Rs.{snap.invested:,.0f}",
    ]
    if signals_found:
        msgs.append("\nOpen positions:")
        for symbol, sig, order in signals_found:
            msgs.append(f"  {symbol} x{order.filled_qty} @ {order.avg_fill_price:.2f} "
                        f"(SL {sig.stop_loss:.2f}, Tgt {sig.target:.2f})")
    send_telegram_summary(msgs)
    print("\n  Telegram summary sent.\n")


if __name__ == "__main__":
    main()
