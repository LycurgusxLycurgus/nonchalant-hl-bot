"""In-memory monitoring hub fan-out for realtime bot snapshots."""

from __future__ import annotations

import asyncio
from typing import AsyncIterator

from app.monitoring.schemas import BotPnLSnapshot


class MonitoringHub:
    """Broadcast snapshots to interested subscribers via async queues."""

    def __init__(self) -> None:
        self._subscribers: list[_Subscriber] = []
        self._snapshots: dict[str, BotPnLSnapshot] = {}
        self._lock = asyncio.Lock()

    async def publish(self, snapshot: BotPnLSnapshot) -> None:
        """Publish a new snapshot to all matching subscribers."""

        async with self._lock:
            self._snapshots[snapshot.run_id] = snapshot
            subscribers = list(self._subscribers)
        for subscriber in subscribers:
            if subscriber.run_id is None or subscriber.run_id == snapshot.run_id:
                await subscriber.queue.put(snapshot)

    async def listen(self, run_id: str | None = None) -> AsyncIterator[BotPnLSnapshot]:
        """Yield snapshots for the provided run identifier (or all runs if None)."""

        subscriber = _Subscriber(asyncio.Queue(), run_id)
        await self._register(subscriber)
        try:
            while True:
                snapshot = await subscriber.queue.get()
                yield snapshot
        finally:
            await self._unregister(subscriber)

    async def latest(self, run_id: str) -> BotPnLSnapshot | None:
        """Return the last known snapshot for a run."""

        async with self._lock:
            return self._snapshots.get(run_id)

    async def list_snapshots(self) -> list[BotPnLSnapshot]:
        """Return last known snapshots for all runs (unordered)."""

        async with self._lock:
            return list(self._snapshots.values())

    async def _register(self, subscriber: "_Subscriber") -> None:
        async with self._lock:
            self._subscribers.append(subscriber)
            snapshots: list[BotPnLSnapshot] = []
            if subscriber.run_id is None:
                snapshots = list(self._snapshots.values())
            else:
                snapshot = self._snapshots.get(subscriber.run_id)
                if snapshot is not None:
                    snapshots = [snapshot]
        for snapshot in snapshots:
            await subscriber.queue.put(snapshot)

    async def _unregister(self, subscriber: "_Subscriber") -> None:
        async with self._lock:
            if subscriber in self._subscribers:
                self._subscribers.remove(subscriber)

    async def reset(self) -> None:
        """Clear all stored snapshots and subscribers (testing utility)."""

        async with self._lock:
            self._snapshots.clear()
            self._subscribers.clear()


class _Subscriber:
    def __init__(self, queue: "asyncio.Queue[BotPnLSnapshot]", run_id: str | None) -> None:
        self.queue: "asyncio.Queue[BotPnLSnapshot]" = queue
        self.run_id = run_id
