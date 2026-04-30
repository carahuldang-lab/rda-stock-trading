"""Event Bus — agents publish their activity here.

The dashboard reads this file to show a live feed of what each agent is doing.

Each event is a JSON line in data/events.jsonl. New events are appended.
Format:
    {
      "timestamp": "2026-04-29T10:23:45",
      "agent": "technical",
      "level": "info",  // info | success | warning | error
      "symbol": "RELIANCE",
      "action": "signal_generated",
      "details": "BUY @ 1425.40, RSI=62.3, vol 2.1x avg"
    }
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Optional

EVENTS_FILE = Path(__file__).parent.parent / "data" / "events.jsonl"
_lock = Lock()


def emit(
    agent: str,
    action: str,
    details: str = "",
    symbol: str = "",
    level: str = "info",
) -> None:
    """Publish an event from an agent.

    agent:   research | fundamental | technical | risk | execution | portfolio | orchestrator
    action:  short snake_case identifier (e.g., "signal_generated")
    details: human-readable explanation
    symbol:  stock symbol if relevant
    level:   info | success | warning | error
    """
    EVENTS_FILE.parent.mkdir(exist_ok=True, parents=True)
    event = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "agent": agent,
        "level": level,
        "symbol": symbol,
        "action": action,
        "details": details,
    }
    with _lock:
        with open(EVENTS_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(event) + "\n")


def read_recent(n: int = 100) -> list[dict]:
    """Read the last N events. Used by the dashboard."""
    if not EVENTS_FILE.exists():
        return []
    with open(EVENTS_FILE, "r", encoding="utf-8") as f:
        lines = f.readlines()
    events = []
    for line in lines[-n:]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(events))    # newest first


def clear() -> None:
    """Clear the event log (useful for fresh runs)."""
    if EVENTS_FILE.exists():
        EVENTS_FILE.unlink()
