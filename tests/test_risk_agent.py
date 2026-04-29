"""Tests for RiskAgent — position sizing + validation rules.

Run: pytest tests/test_risk_agent.py -v
"""
import pytest
from agents.risk import RiskAgent


@pytest.fixture
def config():
    return {
        "account": {"capital": 100000, "trading_mode": "PAPER"},
        "risk": {
            "risk_per_trade_pct": 2.0,
            "max_open_positions": 5,
            "max_sector_exposure_pct": 40,
            "max_daily_loss_pct": 5,
            "default_stoploss_pct": 2.0,
        },
    }


def test_position_sizing_respects_risk_budget(config):
    """₹1L capital, 2% risk = ₹2,000. Entry 100, SL 98 → ₹2/share risk → 1000 shares."""
    agent = RiskAgent(config)
    qty = agent.calculate_position_size(entry_price=100, stop_loss=98)
    # But capped at 30% of capital → ₹30,000 / ₹100 = 300 shares
    assert qty == 300


def test_position_sizing_with_wide_stop(config):
    """Entry 500, SL 480 → ₹20/share risk → 100 shares (within capital cap)."""
    agent = RiskAgent(config)
    qty = agent.calculate_position_size(entry_price=500, stop_loss=480)
    assert qty == 60     # capped: 30000/500 = 60


def test_invalid_stoploss_returns_zero(config):
    agent = RiskAgent(config)
    assert agent.calculate_position_size(entry_price=100, stop_loss=100) == 0
    assert agent.calculate_position_size(entry_price=100, stop_loss=110) == 0


def test_max_open_positions_blocks_trade(config):
    agent = RiskAgent(config)
    open_positions = [
        {"symbol": f"STOCK{i}", "quantity": 10, "entry_price": 100, "sector": "IT"}
        for i in range(5)
    ]
    result = agent.validate_trade("NEW", 100, 98, "IT", open_positions)
    assert not result.approved
    assert "Max positions" in result.reason


def test_already_holding_blocks_trade(config):
    agent = RiskAgent(config)
    open_positions = [{"symbol": "RELIANCE", "quantity": 10, "entry_price": 2500, "sector": "Energy"}]
    result = agent.validate_trade("RELIANCE", 2500, 2450, "Energy", open_positions)
    assert not result.approved
    assert "Already holding" in result.reason


def test_daily_loss_kill_switch(config):
    agent = RiskAgent(config)
    # ₹6,000 loss = 6% of ₹1L > 5% threshold
    result = agent.validate_trade("ABC", 100, 98, "IT", [], daily_pnl=-6000)
    assert not result.approved
    assert "Daily loss" in result.reason
    assert agent.daily_loss_halt is True
