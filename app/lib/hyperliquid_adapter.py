"""Hyperliquid exchange adapter.

This implementation is intentionally light for Phase 4. It provides an
interface that can be patched in tests and extended with real Hyperliquid SDK
integrations in later phases. All methods are asynchronous so that callers can
await on network operations or mocked routines uniformly.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ExchangeCredentials:
    """Represents the decrypted agent credentials needed for signing."""

    address: str
    private_key: str


class HyperliquidExchangeClient:
    """Minimal client facade for Hyperliquid exchange actions."""

    def __init__(self, credentials: ExchangeCredentials, *, base_url: str) -> None:
        self._credentials = credentials
        self._base_url = base_url

    async def set_isolated_leverage(self, market: str, leverage: int) -> dict[str, Any]:
        """Set leverage for the provided market.

        Returns a stubbed acknowledgement. Replace this with real SDK calls when
        wiring Hyperliquid actions in a later phase.
        """

        return {
            "ok": True,
            "action": "set_leverage",
            "market": market,
            "leverage": leverage,
            "base_url": self._base_url,
        }

    async def place_market_order(self, market: str, usd_notional: float) -> dict[str, Any]:
        """Submit a market order sized by USD notional.

        Returns a stubbed acknowledgement mirroring payload inputs.
        """

        return {
            "ok": True,
            "action": "place_market_order",
            "market": market,
            "usd_notional": usd_notional,
            "base_url": self._base_url,
        }

    async def cancel_open_orders(self, market: str) -> dict[str, Any]:
        """Cancel all open orders for the provided market."""

        return {
            "ok": True,
            "action": "cancel_open_orders",
            "market": market,
            "base_url": self._base_url,
        }

    async def close_position(self, market: str) -> dict[str, Any]:
        """Close any open position for the provided market."""

        return {
            "ok": True,
            "action": "close_position",
            "market": market,
            "base_url": self._base_url,
        }

    async def usd_send(self, destination: str, amount: float) -> dict[str, Any]:
        """Internal USD transfer to a destination user/account."""

        return {
            "ok": True,
            "action": "usd_send",
            "destination": destination,
            "amount": amount,
            "base_url": self._base_url,
        }

    async def spot_send(self, coin: str, destination: str, amount: float) -> dict[str, Any]:
        """Transfer spot asset to a destination user/account."""

        return {
            "ok": True,
            "action": "spot_send",
            "coin": coin,
            "destination": destination,
            "amount": amount,
            "base_url": self._base_url,
        }

    async def close(self) -> None:
        """Release any resources held by the client."""

        return None
