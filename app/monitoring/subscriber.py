"""Hyperliquid WebSocket subscriber that publishes normalized snapshots.

This integrates with the official `hyperliquid` Python package's websocket manager.
It can subscribe to market channels (e.g., bbo for best bid/offer) and optionally
user/account channels when authenticated flows are added in later phases.

For Phase 5, this module provides a minimal, opt-in subscriber that can be started
and stopped explicitly (not auto-started) to avoid unexpected side effects.
"""

from __future__ import annotations

import logging
import threading
from typing import Callable

try:
    from hyperliquid.websocket_manager import WebsocketManager
except Exception:  # pragma: no cover - environment guard
    WebsocketManager = None  # type: ignore

from app.monitoring.service import MonitoringService


class HLSubscriber:
    """Manage Hyperliquid WS subscriptions and publish updates to MonitoringService."""

    def __init__(self, base_url: str, service: MonitoringService) -> None:
        if WebsocketManager is None:  # pragma: no cover - defensive
            raise RuntimeError("hyperliquid package not available")
        self._ws = WebsocketManager(base_url)
        self._service = service
        self._thread: threading.Thread | None = None
        self._running = False

    def start(self) -> None:
        if self._running:
            return
        self._running = True
        self._thread = self._ws
        self._thread.daemon = True
        self._thread.start()

    def stop(self) -> None:
        if not self._running:
            return
        self._running = False
        try:
            self._ws.stop()
        except Exception:
            logging.exception("Error while stopping WebsocketManager")

    # ---- Subscriptions ----

    def subscribe_bbo(self, coin: str) -> int:
        """Subscribe to Best Bid/Offer for a coin; updates mark_price for matching runs.

        This uses the `bbo` channel, which yields messages like:
        {"channel":"bbo","data":{"coin":"BTC","bid":...,"ask":...}}
        """

        def on_bbo(msg: dict) -> None:
            try:
                data = msg.get("data") or {}
                coin_name = str(data.get("coin", "")).upper()
                bid = float(data.get("bid") or 0)
                ask = float(data.get("ask") or 0)
                mark = (bid + ask) / 2 if (bid and ask) else (bid or ask or 0)
                # Update all snapshots whose market starts with this coin name
                # (e.g., BTC-PERP -> BTC)
                prefix = f"{coin_name}-"
                # We iterate via service snapshots to avoid requiring run ids here
                # Note: list_snapshots is a light copy
                # mypy: async loop required
                import anyio  # type: ignore

                async def _update():
                    for snap in await self._service.list_snapshots():
                        if str(snap.market).upper().startswith(prefix):
                            await self._service.update_snapshot(snap.run_id, mark_price=mark)

                anyio.from_thread.run(_update)
            except Exception:
                logging.exception("bbo callback failed")

        sub = {"type": "bbo", "coin": coin.upper()}
        return self._ws.subscribe(sub, on_bbo)

    # Future: subscribe to user/account channels (requires auth routing)
