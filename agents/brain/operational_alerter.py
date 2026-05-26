"""Operational alerter — replaces laptop n8n. Runs on Oracle systemd timer.

Checks file mtimes for critical bot tasks. Sends Telegram alert if any task
is stale beyond its minute-threshold. Dedups so it doesn't spam (re-alert
only on threshold breach or every 30 min)."""
from __future__ import annotations
import os, json
from datetime import datetime, time as dtime
from pathlib import Path
from zoneinfo import ZoneInfo
import requests
from dotenv import load_dotenv
load_dotenv()

ROOT = Path(__file__).parent.parent.parent
DATA = ROOT / "data"
STATE = DATA / ".alert_state.json"

IST = ZoneInfo("Asia/Kolkata")
MKT_OPEN = dtime(9, 15)
MKT_CLOSE = dtime(15, 30)

# task_name -> (filename, max_age_minutes)
WATCH = {
    "groww_sync":        ("groww_holdings.csv",    15),
    "live_breakout_scan":("live_movers.csv",        8),
    "universe_scan":     ("candidates.csv",        20),
    "technical_agent":   ("signals.csv",           20),
    "market_regime":     ("market_regime.csv",     30),
}

# Re-alert cooldown
RENOTIFY_AFTER_MIN = 30

TG = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT = os.getenv("TELEGRAM_CHAT_ID", "")


def now_ist() -> datetime:
    return datetime.now(IST)


def in_market_hours() -> bool:
    n = now_ist()
    if n.weekday() >= 5:  # Sat/Sun
        return False
    return MKT_OPEN <= n.time() <= MKT_CLOSE


def file_age_min(fname: str) -> float | None:
    p = DATA / fname
    if not p.exists():
        return None
    return (datetime.now().timestamp() - p.stat().st_mtime) / 60


def load_state() -> dict:
    if STATE.exists():
        try:
            return json.loads(STATE.read_text())
        except Exception:
            pass
    return {}


def save_state(s: dict) -> None:
    STATE.write_text(json.dumps(s, indent=2))


def send_tg(text: str) -> None:
    if not TG or not CHAT:
        print("[alerter] no Telegram creds; skipping send"); return
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{TG}/sendMessage",
            json={"chat_id": CHAT, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
        if not r.ok:
            print(f"[alerter] tg send failed: {r.status_code} {r.text[:200]}")
    except Exception as e:
        print(f"[alerter] tg exception: {e}")


def main():
    if not in_market_hours():
        print(f"[alerter] {now_ist().isoformat()} outside market hours; skip")
        return
    state = load_state()
    stale = []
    for name, (fname, limit) in WATCH.items():
        age = file_age_min(fname)
        if age is None:
            stale.append((name, fname, "missing", limit))
        elif age > limit:
            stale.append((name, fname, round(age, 1), limit))

    if not stale:
        # Clear stale state if everything healthy now
        if state.get("stale_keys"):
            send_tg(f"[RDA] All bot tasks healthy at {now_ist().strftime('%H:%M IST')}.")
        save_state({"last_check": now_ist().isoformat(), "stale_keys": []})
        print(f"[alerter] {now_ist().isoformat()} all healthy")
        return

    keys = sorted(t[0] for t in stale)
    prev_keys = state.get("stale_keys", [])
    last_alert_ts = state.get("last_alert_ts", "1970-01-01T00:00:00+05:30")
    last_alert = datetime.fromisoformat(last_alert_ts)
    mins_since = (now_ist() - last_alert).total_seconds() / 60

    same_set = (set(keys) == set(prev_keys))
    must_renotify = mins_since >= RENOTIFY_AFTER_MIN

    if not same_set or must_renotify:
        lines = [f"Bot Health Alert — STALE DATA"]
        for name, fname, age, limit in stale:
            if age == "missing":
                lines.append(f"❌ `{name}` file missing ({fname})")
            else:
                lines.append(f"⚠️ `{name}` is {age}min stale (>{limit}min limit)")
        lines.append(f"\nTime: {now_ist().strftime('%H:%M IST')}")
        send_tg("\n".join(lines))
        save_state({"last_check": now_ist().isoformat(),
                    "stale_keys": keys,
                    "last_alert_ts": now_ist().isoformat()})
        print(f"[alerter] alert sent for: {keys}")
    else:
        save_state({"last_check": now_ist().isoformat(),
                    "stale_keys": keys,
                    "last_alert_ts": last_alert_ts})
        print(f"[alerter] {len(stale)} still stale, dedup'd")


if __name__ == "__main__":
    main()
