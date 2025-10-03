"""Tests for realtime monitoring SSE and UI partial updates."""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.monitoring.schemas import BotPnLSnapshot
from app.monitoring.routes import stream_all


@pytest.mark.asyncio
async def test_monitoring_sse_emits_events(app) -> None:
    """Streaming response should emit SSE payloads with snapshot JSON."""

    hub = app.state.monitoring_hub
    await hub.reset()

    run_id = secrets.token_hex(8)
    snapshot = BotPnLSnapshot(
        run_id=run_id,
        market="BTC-PERP",
        status="running",
    )

    async def fake_listen():
        yield snapshot

    # Substitute hub.listen with deterministic async generator
    original_listen = hub.listen
    hub.listen = lambda *args, **kwargs: fake_listen()  # type: ignore[assignment]

    try:
        response = await stream_all(hub)  # type: ignore[arg-type]
        body_iter = response.body_iterator
        chunk = await asyncio.wait_for(body_iter.__anext__(), timeout=1.0)
    finally:
        hub.listen = original_listen  # type: ignore[assignment]

    payload = chunk.decode("utf-8")
    assert "data:" in payload
    assert f'"ok": true' in payload.lower()
    assert run_id in payload


@pytest.mark.asyncio
async def test_monitoring_table_partial_renders_snapshot(app, async_client: AsyncClient) -> None:
    """Partial table should render rows for snapshots currently in hub."""

    hub = app.state.monitoring_hub
    await hub.reset()

    run_id = secrets.token_hex(8)
    now = datetime.now(tz=UTC)
    snapshot = BotPnLSnapshot(
        run_id=run_id,
        market="ETH-PERP",
        status="running",
        position_notional="123.45",
        entry_price="2500.12",
        mark_price="2501.00",
        realized_pnl="0",
        unrealized_pnl="1.23",
        timestamp=now,
    )
    await hub.publish(snapshot)

    resp = await async_client.get("/monitoring/partial/table")
    assert resp.status_code == 200
    html = resp.text

    # Validate that run id short code and selected numeric values appear in the HTML
    assert run_id[:6] in html and run_id[-4:] in html
    assert "ETH-PERP" in html
    assert "123.45" in html
    assert "1.23" in html
