"""Orchestrator — coordinates all agents through the daily trading cycle.

Cycle (Phase 1):
    1. Pre-market (09:00 IST)   — load config, init agents, load instrument list.
    2. Market open (09:15)       — start scan loop.
    3. Scan loop (every 60s)     — for each symbol in universe:
            a. ResearchAgent.should_avoid_today()
            b. FundamentalAgent.passes_filters()  [Phase 2]
            c. TechnicalAgent.generate_signal()
            d. RiskAgent.validate_trade()
            e. ExecutionAgent.place_order()
            f. PortfolioAgent.open_position()
    4. Square-off (15:15)        — close all open intraday positions.
    5. EOD (15:45)               — daily snapshot, send Telegram summary.

Run:
    python main.py
"""
from __future__ import annotations

import time
from datetime import datetime, time as dt_time

from utils import load_config, get_logger
from agents.research import ResearchAgent
from agents.fundamental import FundamentalAgent
from agents.technical import TechnicalAgent
from agents.risk import RiskAgent
from agents.execution import ExecutionAgent
from agents.portfolio import PortfolioAgent

log = get_logger(__name__)


class Orchestrator:
    """Coordinates the full trading cycle."""

    def __init__(self, config_path: str = "config/config.yaml"):
        self.config = load_config(config_path)
        self.research = ResearchAgent(self.config)
        self.fundamental = FundamentalAgent(self.config)
        self.technical = TechnicalAgent(self.config)
        self.risk = RiskAgent(self.config)
        self.execution = ExecutionAgent(self.config)
        self.portfolio = PortfolioAgent(self.config)

        self.universe = self._load_universe()
        log.info("Orchestrator initialized — mode={}, universe={} symbols",
                 self.execution.mode, len(self.universe))

    def _load_universe(self) -> list:
        """Load symbol list. Phase 1: hardcoded sample. Phase 2: from data/."""
        # TODO: load from data/instruments/nifty500.csv
        return ["RELIANCE", "TCS", "INFY", "HDFCBANK", "ICICIBANK"]

    # ------------------------------------------------------------------
    # Daily cycle
    # ------------------------------------------------------------------
    def is_market_open(self) -> bool:
        now = datetime.now().time()
        return dt_time(9, 15) <= now <= dt_time(15, 30)

    def is_square_off_time(self) -> bool:
        sq = self.config["strategy"]["square_off_time"]
        h, m = map(int, sq.split(":"))
        return datetime.now().time() >= dt_time(h, m)

    def run_scan_cycle(self) -> None:
        """One pass through the universe — generate + execute signals."""
        # Apply fundamental filter (no-op if disabled)
        tradeable = self.fundamental.get_tradeable_universe(self.universe)

        for symbol in tradeable:
            avoid, reason = self.research.should_avoid_today(symbol)
            if avoid:
                log.debug("Skipping {} — {}", symbol, reason)
                continue

            # TODO: fetch OHLCV data, generate signal, validate, execute
            # signal = self.technical.generate_signal(symbol, df, strategy_name)
            # if signal: ...

    def run(self) -> None:
        """Main daily loop. Run from 09:15 to 15:30 IST."""
        log.info("Starting daily trading cycle")
        self.risk.reset_daily_halt()

        scan_interval = self.config["strategy"]["scan_interval_seconds"]

        while self.is_market_open():
            try:
                if self.is_square_off_time():
                    log.info("Square-off time — closing all positions")
                    self._square_off_all()
                    break

                self.run_scan_cycle()
                time.sleep(scan_interval)

            except KeyboardInterrupt:
                log.warning("Interrupted by user")
                break
            except Exception as e:
                log.exception("Error in scan cycle: {}", e)
                time.sleep(scan_interval)

        # End-of-day
        snap = self.portfolio.daily_snapshot()
        log.info("EOD snapshot: {}", snap)

    def _square_off_all(self) -> None:
        """Close all intraday positions before market close."""
        for symbol in list(self.portfolio.open_positions.keys()):
            # TODO: fetch current LTP and close
            pass


if __name__ == "__main__":
    Orchestrator().run()
