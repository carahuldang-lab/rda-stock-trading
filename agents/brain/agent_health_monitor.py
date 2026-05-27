"""Agent Self-Heal — detects stale agents and triggers refresh automatically.

When the brain reports stale data (e.g., news.csv is 8h old), this runs the
corresponding refresh command to fix it before the next brain cycle.

Maps each stale file → refresh script.

Run:
    python -m agents.brain.agent_health_monitor          # check + heal all
    python -m agents.brain.agent_health_monitor --check  # report only
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
load_dotenv()

PROJECT_ROOT = Path(__file__).parent.parent.parent
DATA_DIR = PROJECT_ROOT / "data"
PYTHON_EXE = sys.executable

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT = os.getenv("TELEGRAM_CHAT_ID", "")

# (data file) -> (refresh command list)
REFRESH_MAP: Dict[str, List[str]] = {
    "market_regime.csv": [PYTHON_EXE, "-m", "agents.research.market_regime"],
    "news.csv": [PYTHON_EXE, "-m", "agents.research.indian_news"],
    "fundamentals.csv": [PYTHON_EXE, "-m", "agents.fundamental.screener"],
    "analyst_reports.csv": [PYTHON_EXE, "-m", "agents.research.analyst_reports"],
    "sector_strength.csv": [PYTHON_EXE, "-m", "agents.research.market_regime", "--sector"],
}

# Max age (hours) before triggering refresh
MAX_AGE_HOURS = {
    "market_regime.csv": 2,
    "news.csv": 6,
    "fundamentals.csv": 168,
    "analyst_reports.csv": 168,
    "sector_strength.csv": 24,
}


def _age_hours(filename: str) -> float | None:
    p = DATA_DIR / filename
    if not p.exists():
        return None
    return (datetime.now().timestamp() - p.stat().st_mtime) / 3600


def detect_stale() -> List[str]:
    stale = []
    for fname, max_h in MAX_AGE_HOURS.items():
        age = _age_hours(fname)
        if age is None or age > max_h:
            stale.append(fname)
    return stale


def heal(fname: str, timeout_sec: int = 180) -> bool:
    cmd = REFRESH_MAP.get(fname)
    if not cmd:
        print(f"[heal] No refresh command mapped for {fname}")
        return False
    print(f"[heal] Refreshing {fname} with: {' '.join(cmd)}")
    try:
        proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), timeout=timeout_sec, capture_output=True, text=True)
        if proc.returncode == 0:
            print(f"[heal] ✅ {fname} refreshed")
            return True
        print(f"[heal] ❌ {fname} returncode={proc.returncode}\nSTDERR: {proc.stderr[:500]}")
        return False
    except subprocess.TimeoutExpired:
        print(f"[heal] ❌ {fname} timed out")
        return False
    except Exception as e:
        print(f"[heal] ❌ {fname} exception: {e}")
        return False


def send_telegram(text: str) -> None:
    if not TG_TOKEN or not TG_CHAT:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage",
            json={"chat_id": TG_CHAT, "text": text, "parse_mode": "Markdown"},
            timeout=10,
        )
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="Detect only, don't heal")
    args = parser.parse_args()

    stale = detect_stale()
    if not stale:
        print(f"[heal] All agents healthy at {datetime.now().isoformat()}")
        return

    if args.check:
        print(f"[heal] Stale files: {', '.join(stale)}")
        return

    healed = []
    failed = []
    for fname in stale:
        if heal(fname):
            healed.append(fname)
        else:
            failed.append(fname)

    summary = f"🩺 *Agent Health Heal* — {datetime.now().strftime('%H:%M')}\n"
    if healed:
        summary += f"✅ Refreshed: {', '.join(healed)}\n"
    if failed:
        summary += f"❌ Failed: {', '.join(failed)}"
    print(summary)
    if failed:
        send_telegram(summary)


if __name__ == "__main__":
    main()
