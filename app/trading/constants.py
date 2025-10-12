"""Shared constants for trading UI defaults."""

from __future__ import annotations

DEFAULT_MARKETS: tuple[str, ...] = (
    "BTC-PERP",
    "ETH-PERP",
    "SOL-PERP",
    "ARB-PERP",
)

DEFAULT_DURATION_MINUTES: int = 15
DEFAULT_LEVERAGE: int = 3
DEFAULT_NOTIONAL: str = "500"
