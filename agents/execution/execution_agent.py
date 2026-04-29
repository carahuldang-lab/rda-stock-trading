"""Execution Agent — Dhan API integration.

Responsibilities:
    1. Authenticate with Dhan using DHAN_CLIENT_ID + DHAN_ACCESS_TOKEN.
    2. Place / modify / cancel orders.
    3. Fetch order status and fills.
    4. Stream live ticks (Phase 2 — websocket).
    5. Switch between PAPER and LIVE mode based on config.

Dhan API docs: https://dhanhq.co/docs/v2/
Python SDK: https://github.com/dhan-oss/DhanHQ-py
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional


class OrderStatus(str, Enum):
    PENDING = "PENDING"
    OPEN = "OPEN"
    FILLED = "FILLED"
    PARTIAL = "PARTIAL"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


@dataclass
class Order:
    """Represents an order placed (or to be placed)."""
    symbol: str
    side: str                           # BUY | SELL
    quantity: int
    order_type: str                     # MARKET | LIMIT | SL | SL-M
    product_type: str                   # INTRADAY | CNC
    price: float = 0.0                  # 0 for MARKET
    trigger_price: float = 0.0          # for SL orders
    order_id: Optional[str] = None
    status: OrderStatus = OrderStatus.PENDING
    filled_qty: int = 0
    avg_fill_price: float = 0.0
    placed_at: Optional[datetime] = None


class ExecutionAgent:
    """Wrapper around Dhan API. Supports PAPER and LIVE modes."""

    def __init__(self, config: dict):
        self.config = config
        self.exec_cfg = config["execution"]
        self.mode = os.getenv("TRADING_MODE", config["account"]["trading_mode"])
        self.client = None
        self._connect()

    def _connect(self) -> None:
        """Initialize Dhan SDK client."""
        if self.mode == "PAPER":
            # Paper mode — use Dhan's paper trading endpoint or simulate locally
            self.client = None              # TODO: paper sim or Dhan paper API
            return

        try:
            from dhanhq import dhanhq
        except ImportError:
            raise ImportError("dhanhq not installed — pip install dhanhq")

        client_id = os.getenv("DHAN_CLIENT_ID")
        access_token = os.getenv("DHAN_ACCESS_TOKEN")
        if not client_id or not access_token:
            raise RuntimeError("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN missing in .env")

        self.client = dhanhq(client_id, access_token)

    # ------------------------------------------------------------------
    # Order placement
    # ------------------------------------------------------------------
    def place_order(self, order: Order) -> Order:
        """Place an order (live or paper)."""
        if self.mode == "PAPER":
            return self._place_paper_order(order)
        return self._place_live_order(order)

    def _place_paper_order(self, order: Order) -> Order:
        """Simulate order locally — used in paper trading."""
        order.order_id = f"PAPER-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        order.status = OrderStatus.FILLED       # Assume instant fill at limit price
        order.filled_qty = order.quantity
        order.avg_fill_price = order.price
        order.placed_at = datetime.now()
        return order

    def _place_live_order(self, order: Order) -> Order:
        """Place real order via Dhan API.

        TODO: implement using self.client.place_order(...)
              See: https://dhanhq.co/docs/v2/orders/
        """
        raise NotImplementedError("Live trading — implement after Phase 1 paper validation")

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------
    def get_order_status(self, order_id: str) -> OrderStatus:
        """Fetch current status of an order."""
        # TODO: self.client.get_order_by_id(order_id)
        return OrderStatus.PENDING

    def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        # TODO: self.client.cancel_order(order_id)
        return False

    def get_positions(self) -> list:
        """Fetch all open positions from broker."""
        # TODO: self.client.get_positions()
        return []

    def get_funds(self) -> dict:
        """Fetch account funds / margin available."""
        # TODO: self.client.get_fund_limits()
        return {"available": 0.0, "used": 0.0}
