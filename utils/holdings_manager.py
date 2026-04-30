"""Holdings Manager — track manually-purchased stocks across all brokers.

Stores user's manual holdings in data/holdings.csv with full CRUD support.

For each holding, computes on-the-fly:
    - Live LTP via yfinance (or Dhan if needed)
    - Unrealized P&L (current value - invested)
    - Technical signal: BUY / HOLD / SELL
    - Recent news count + sentiment
    - Gain type (INTRADAY / STCG / LTCG) for tax filing
"""
from __future__ import annotations

import csv
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import pandas as pd

DATA_DIR = Path(__file__).parent.parent / "data"
HOLDINGS_FILE = DATA_DIR / "holdings.csv"

HEADERS = [
    "symbol", "quantity", "avg_buy_price", "buy_date",
    "broker", "notes", "added_at",
]


def _ensure_csv() -> None:
    DATA_DIR.mkdir(exist_ok=True, parents=True)
    if not HOLDINGS_FILE.exists():
        with open(HOLDINGS_FILE, "w", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(HEADERS)


def load_holdings() -> pd.DataFrame:
    _ensure_csv()
    return pd.read_csv(HOLDINGS_FILE)


def save_holdings(df: pd.DataFrame) -> None:
    _ensure_csv()
    df.to_csv(HOLDINGS_FILE, index=False)


def add_holding(
    symbol: str,
    quantity: int,
    avg_buy_price: float,
    buy_date: str,
    broker: str = "",
    notes: str = "",
) -> None:
    df = load_holdings()
    new_row = {
        "symbol": symbol.upper().strip(),
        "quantity": int(quantity),
        "avg_buy_price": float(avg_buy_price),
        "buy_date": buy_date,
        "broker": broker,
        "notes": notes,
        "added_at": datetime.now().isoformat(timespec="seconds"),
    }
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    save_holdings(df)


def delete_holding(idx: int) -> None:
    df = load_holdings()
    df = df.drop(idx).reset_index(drop=True)
    save_holdings(df)


def update_holding(idx: int, **kwargs) -> None:
    df = load_holdings()
    for k, v in kwargs.items():
        if k in df.columns:
            df.at[idx, k] = v
    save_holdings(df)


def gain_type(buy_date_str: str) -> str:
    """STCG (<1yr) / LTCG (>=1yr) / INTRADAY classification."""
    try:
        bd = pd.to_datetime(buy_date_str).date()
    except Exception:
        return "UNKNOWN"
    days = (date.today() - bd).days
    if days == 0:
        return "INTRADAY"
    if days < 365:
        return "STCG"
    return "LTCG"


def fetch_live_prices(symbols: list[str]) -> dict[str, float]:
    """Bulk-fetch latest LTP via yfinance for a list of symbols."""
    if not symbols:
        return {}
    import yfinance as yf
    yf_syms = [f"{s}.NS" for s in symbols]
    try:
        df = yf.download(yf_syms, period="2d", interval="1d",
                         progress=False, auto_adjust=True, threads=True,
                         group_by="ticker")
    except Exception:
        return {}

    prices = {}
    if hasattr(df.columns, "get_level_values"):
        for s in symbols:
            yf_s = f"{s}.NS"
            try:
                if yf_s in df.columns.get_level_values(0):
                    sub = df[yf_s].dropna()
                    if not sub.empty:
                        prices[s] = float(sub["Close"].iloc[-1])
            except Exception:
                continue
    else:
        if not df.empty and "Close" in df.columns:
            prices[symbols[0]] = float(df["Close"].dropna().iloc[-1])
    return prices


def get_signal_for_holding(symbol: str, current_price: float) -> dict:
    """Compute technical signal for an existing holding.

    BUY: existing strategies fire BUY (momentum/mean reversion)
    SELL: overbought + trend broken + drawdown
    HOLD: otherwise
    """
    import sys
    sys.path.insert(0, str(Path(__file__).parent.parent))
    import yfinance as yf
    from utils.config_loader import load_config
    from agents.technical import TechnicalAgent, score_stock
    from strategies.momentum_breakout import generate_signal as mom
    from strategies.mean_reversion import generate_signal as mr

    config = load_config()

    try:
        df = yf.download(f"{symbol}.NS", period="120d", interval="1d",
                         progress=False, auto_adjust=True)
        if df is None or df.empty:
            return {"signal": "N/A", "reason": "No data", "score": 0}
        if hasattr(df.columns, "get_level_values"):
            df.columns = df.columns.get_level_values(0)
        df.columns = [c.lower() for c in df.columns]
        df = df[["open", "high", "low", "close", "volume"]]

        tech = TechnicalAgent(config)
        df = tech.add_indicators(df)
        score = score_stock(df, symbol=symbol)
        last = df.iloc[-1]
        rsi = float(last.get("rsi", 50))
        ema_slow = float(last.get("ema_slow", current_price))
        ema_trend = float(last.get("ema_trend", current_price))

        # SELL conditions
        if rsi > 75 and current_price < ema_slow:
            return {"signal": "SELL",
                    "reason": f"Overbought (RSI {rsi:.0f}) + trend broken",
                    "score": score.score if score else 0}
        if current_price < ema_trend * 0.9:
            return {"signal": "SELL",
                    "reason": f"Down 10%+ from 50-EMA",
                    "score": score.score if score else 0}

        # BUY conditions (existing strategies)
        for strat_fn, name in [(mom, "Momentum Breakout"),
                               (mr, "Mean Reversion")]:
            try:
                sig = strat_fn(df, config, symbol=symbol)
                if sig is not None:
                    return {"signal": "BUY (add)",
                            "reason": f"{name}: {sig.reasoning[:60]}",
                            "score": score.score if score else 0}
            except Exception:
                continue

        # HOLD default
        return {"signal": "HOLD",
                "reason": f"RSI {rsi:.0f}, score {score.score:.0f}" if score else f"RSI {rsi:.0f}",
                "score": score.score if score else 0}
    except Exception as e:
        return {"signal": "N/A", "reason": str(e)[:50], "score": 0}


def get_news_for(symbol: str, limit: int = 3) -> list[dict]:
    f = DATA_DIR / "news.csv"
    if not f.exists():
        return []
    df = pd.read_csv(f)
    rows = df[df["symbol"] == symbol].head(limit)
    return rows.to_dict("records")


def enrich_holdings(df: pd.DataFrame) -> pd.DataFrame:
    """Add LTP, P&L, signal, gain_type to holdings dataframe.

    Returns enriched df ready for display.
    """
    if df.empty:
        return df

    df = df.copy()
    symbols = df["symbol"].astype(str).tolist()
    prices = fetch_live_prices(symbols)

    df["current_price"] = df["symbol"].map(prices).fillna(0.0)
    df["invested"] = df["quantity"].astype(float) * df["avg_buy_price"].astype(float)
    df["current_value"] = df["quantity"].astype(float) * df["current_price"]
    df["unrealized_pnl"] = df["current_value"] - df["invested"]
    df["pnl_pct"] = (df["unrealized_pnl"] / df["invested"] * 100).round(2)
    df["gain_type"] = df["buy_date"].astype(str).apply(gain_type)
    return df
