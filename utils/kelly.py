"""Kelly Criterion — optimal position sizing based on edge.

Formula: f* = (b*p - q) / b
    where:
        f* = fraction of capital to bet
        b  = odds (win amount / loss amount)
        p  = probability of winning
        q  = probability of losing (1 - p)

We use FRACTIONAL Kelly (typically 25%) for safety:
    Full Kelly is mathematically optimal but assumes perfect knowledge of p and b.
    Real-world p, b are estimates — fractional Kelly survives estimation errors.
"""
from __future__ import annotations


def kelly_fraction(win_rate: float, avg_win_pct: float, avg_loss_pct: float,
                   fractional: float = 0.25) -> float:
    """Compute fractional Kelly position size as % of capital.

    Args:
        win_rate: probability of winning (0-1)
        avg_win_pct: average win size (e.g., 4.0 = 4%)
        avg_loss_pct: average loss size (positive number, e.g., 2.0 = 2%)
        fractional: fraction of full Kelly to use (0.25 = quarter Kelly)

    Returns:
        Fraction of capital to allocate (0 to 1).
        Returns 0 if edge is negative (don't trade).
    """
    if avg_loss_pct <= 0 or avg_win_pct <= 0:
        return 0.0
    if not 0 < win_rate < 1:
        return 0.0
    b = avg_win_pct / avg_loss_pct
    p = win_rate
    q = 1 - p
    full_kelly = (b * p - q) / b
    if full_kelly <= 0:
        return 0.0
    f = full_kelly * fractional
    # Cap at 25% to avoid over-concentration even if formula says more
    return min(f, 0.25)


def position_size_from_kelly(capital: float, kelly_frac: float, entry_price: float) -> int:
    """Convert Kelly fraction into share quantity."""
    if entry_price <= 0:
        return 0
    capital_to_use = capital * kelly_frac
    return int(capital_to_use / entry_price)
