"""Monitoring service responsible for transforming market/account events into snapshots."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

from app.monitoring.hub import MonitoringHub
from app.monitoring.schemas import BotPnLSnapshot
from app.trading.schemas import BotRunRecord

_DECIMAL_FIELDS = {
    "position_notional",
    "entry_price",
    "mark_price",
    "realized_pnl",
    "unrealized_pnl",
}


def _to_decimal(value: Any) -> Decimal:
    if isinstance(value, Decimal):
        return value
    return Decimal(str(value))


@dataclass
class _MonitoringContext:
    snapshot: BotPnLSnapshot


class MonitoringService:
    """Manage monitoring contexts for bot runs and publish updates to the hub."""

    def __init__(self, hub: MonitoringHub) -> None:
        self._hub = hub
        self._contexts: dict[str, _MonitoringContext] = {}
        self._lock = asyncio.Lock()

    async def register_run(self, record: BotRunRecord) -> BotPnLSnapshot:
        """Register a new bot run and publish an initial snapshot."""

        snapshot = BotPnLSnapshot(
            run_id=record.run_id,
            market=record.market,
            status=record.status,
            position_notional=_to_decimal(record.usd_notional),
            entry_price=Decimal("0"),
            mark_price=Decimal("0"),
            realized_pnl=Decimal("0"),
            unrealized_pnl=Decimal("0"),
        )
        async with self._lock:
            self._contexts[record.run_id] = _MonitoringContext(snapshot=snapshot)
        await self._hub.publish(snapshot)
        return snapshot

    async def update_snapshot(self, run_id: str, **updates: Any) -> BotPnLSnapshot:
        """Update an existing run snapshot with provided fields and broadcast it."""

        async with self._lock:
            context = self._contexts.get(run_id)
            if context is None:
                raise KeyError(f"Unknown run_id '{run_id}'")

            payload: dict[str, Any] = {}
            for field, value in updates.items():
                if field in _DECIMAL_FIELDS and value is not None:
                    payload[field] = _to_decimal(value)
                elif value is not None:
                    payload[field] = value
            payload["timestamp"] = datetime.now(tz=UTC)
            snapshot = context.snapshot.model_copy(update=payload)
            context.snapshot = snapshot

        await self._hub.publish(snapshot)
        return snapshot

    async def mark_status(self, run_id: str, status: str) -> BotPnLSnapshot:
        """Helper to mark a run status change."""

        return await self.update_snapshot(run_id, status=status)

    async def get_snapshot(self, run_id: str) -> BotPnLSnapshot | None:
        async with self._lock:
            context = self._contexts.get(run_id)
            if context is None:
                return None
            return context.snapshot

    async def list_snapshots(self) -> list[BotPnLSnapshot]:
        """Return a shallow copy list of current snapshots."""

        async with self._lock:
            return [ctx.snapshot for ctx in self._contexts.values()]
