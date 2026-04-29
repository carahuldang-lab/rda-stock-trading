"""End-to-end connectivity test — Dhan API, Yahoo Finance, Telegram.

Run this BEFORE starting any actual trading workflow.
It confirms:
    1. .env credentials are loaded correctly.
    2. Dhan SDK can authenticate and fetch your fund limits.
    3. Dhan can fetch live LTP for a sample stock (RELIANCE).
    4. yfinance can pull historical data (backup data source).
    5. Telegram bot can send messages.

Run from project root:
    python tests/test_connectivity.py
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta
from pathlib import Path

# Add project root to path so we can import utils/, agents/
sys.path.insert(0, str(Path(__file__).parent.parent))

from dotenv import load_dotenv


def print_header(text: str) -> None:
    print("\n" + "=" * 60)
    print(f"  {text}")
    print("=" * 60)


def print_success(msg: str) -> None:
    print(f"  [PASS] {msg}")


def print_fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def print_info(msg: str) -> None:
    print(f"  [INFO] {msg}")


# ============================================================
# TEST 1 — .env loaded correctly
# ============================================================
def test_env() -> dict:
    print_header("TEST 1 — Environment variables (.env)")
    load_dotenv()

    required = {
        "DHAN_CLIENT_ID": os.getenv("DHAN_CLIENT_ID"),
        "DHAN_ACCESS_TOKEN": os.getenv("DHAN_ACCESS_TOKEN"),
        "TRADING_MODE": os.getenv("TRADING_MODE"),
    }
    optional = {
        "TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN"),
        "TELEGRAM_CHAT_ID": os.getenv("TELEGRAM_CHAT_ID"),
    }

    all_ok = True
    for k, v in required.items():
        if v:
            masked = v[:6] + "..." + v[-4:] if len(v) > 12 else v
            print_success(f"{k} = {masked}")
        else:
            print_fail(f"{k} is missing in .env")
            all_ok = False

    for k, v in optional.items():
        if v:
            masked = v[:6] + "..." + v[-4:] if len(v) > 12 else v
            print_success(f"{k} = {masked}")
        else:
            print_info(f"{k} not set (optional)")

    if not all_ok:
        sys.exit("\nFix .env file before continuing.")

    return {**required, **optional}


# ============================================================
# TEST 2 — Dhan authentication + funds
# ============================================================
def test_dhan_auth(env: dict) -> object:
    print_header("TEST 2 — Dhan API authentication")

    client = None

    # Try v2 SDK pattern first (current)
    try:
        from dhanhq import DhanContext, dhanhq as DhanClient
        ctx = DhanContext(env["DHAN_CLIENT_ID"], env["DHAN_ACCESS_TOKEN"])
        client = DhanClient(ctx)
        print_success(f"Connected (v2 SDK) as Client ID {env['DHAN_CLIENT_ID']}")
    except ImportError:
        pass
    except Exception as e:
        print_info(f"v2 init failed: {e} — trying v1 fallback")

    # Fallback to v1 pattern
    if client is None:
        try:
            from dhanhq import dhanhq
            client = dhanhq(env["DHAN_CLIENT_ID"], env["DHAN_ACCESS_TOKEN"])
            print_success(f"Connected (v1 SDK) as Client ID {env['DHAN_CLIENT_ID']}")
        except Exception as e:
            print_fail(f"Auth failed: {e}")
            sys.exit(1)

    # Fetch fund limits
    try:
        funds = client.get_fund_limits()
        if isinstance(funds, dict) and funds.get("status") == "success":
            data = funds.get("data", {})
            available = data.get("availabelBalance", "N/A")
            utilized = data.get("utilizedAmount", "N/A")
            print_success(f"Available balance: Rs.{available}")
            print_success(f"Utilized amount:   Rs.{utilized}")
        else:
            print_info(f"Fund response: {funds}")
    except Exception as e:
        print_fail(f"Fund fetch failed: {e}")

    return client


# ============================================================
# TEST 3 — Dhan live quote for RELIANCE
# ============================================================
def test_dhan_quote(client) -> None:
    print_header("TEST 3 — Dhan live quote (RELIANCE)")

    # Dhan uses a "security_id" for each stock. RELIANCE NSE = 2885
    RELIANCE_SECURITY_ID = "2885"
    EXCHANGE_NSE_EQ = "NSE_EQ"

    try:
        # quote_data is the modern method; some SDK versions use ticker_data
        quote = client.quote_data({
            EXCHANGE_NSE_EQ: [int(RELIANCE_SECURITY_ID)],
        })
        if isinstance(quote, dict) and quote.get("status") == "success":
            data = quote.get("data", {}).get("data", {}).get(EXCHANGE_NSE_EQ, {}).get(RELIANCE_SECURITY_ID, {})
            ltp = data.get("last_price", "N/A")
            print_success(f"RELIANCE LTP: Rs.{ltp}")
        else:
            print_info(f"Quote response: {quote}")
    except Exception as e:
        print_fail(f"Quote fetch failed: {e}")
        print_info("This may need Dhan Data API subscription. Skipping for now.")


# ============================================================
# TEST 4 — yfinance historical data (backup source)
# ============================================================
def test_yfinance() -> None:
    print_header("TEST 4 — Yahoo Finance historical data")

    try:
        import yfinance as yf
    except ImportError:
        print_fail("yfinance not installed")
        return

    try:
        # NSE symbols use .NS suffix
        end = datetime.now()
        start = end - timedelta(days=10)
        df = yf.download(
            "RELIANCE.NS",
            start=start.strftime("%Y-%m-%d"),
            end=end.strftime("%Y-%m-%d"),
            interval="1d",
            progress=False,
            auto_adjust=True,
        )
        if df is not None and not df.empty:
            print_success(f"Fetched {len(df)} bars of RELIANCE.NS daily data")
            print_info(f"Latest close: Rs.{df['Close'].iloc[-1].item():.2f} on {df.index[-1].date()}")
        else:
            print_fail("No data returned from yfinance")
    except Exception as e:
        print_fail(f"yfinance failed: {e}")


# ============================================================
# TEST 5 — Telegram alert
# ============================================================
def test_telegram(env: dict) -> None:
    print_header("TEST 5 — Telegram bot alert")

    bot_token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not bot_token or not chat_id:
        print_info("Telegram not configured — skipping.")
        return

    try:
        import requests
        msg = (
            "[OK] RDA Stock Trading - Connectivity Test\n\n"
            f"Time: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
            "Dhan API: connected\n"
            "Status: System ready for paper trading"
        )
        url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
        r = requests.post(url, json={"chat_id": chat_id, "text": msg}, timeout=10)
        if r.status_code == 200:
            print_success("Telegram message sent. Check your Telegram.")
        else:
            print_fail(f"Telegram returned {r.status_code}: {r.text}")
    except Exception as e:
        print_fail(f"Telegram failed: {e}")


# ============================================================
# RUN ALL TESTS
# ============================================================
if __name__ == "__main__":
    print("\n" + "#" * 60)
    print("#  RDA STOCK TRADING - CONNECTIVITY TEST")
    print("#" * 60)

    env = test_env()
    client = test_dhan_auth(env)
    test_dhan_quote(client)
    test_yfinance()
    test_telegram(env)

    print_header("ALL TESTS COMPLETE")
    print("  If any test failed, fix it before proceeding.\n")
