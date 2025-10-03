"""Tests for history and audit trail endpoints."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import json

import pytest
from httpx import AsyncClient

from app.authz import storage as auth_storage
from app.trading import storage as trading_storage


def _append_run(run_id: str, status: str) -> None:
    trading_storage.append_run(
        {
            "run_id": run_id,
            "market": "ETH-PERP",
            "usd_notional": "50",
            "leverage": 2,
            "wallet_address": "0x" + "feed" * 10,
            "agent_address": "0x" + "abba" * 10,
            "status": status,
            "started_at": datetime.now(tz=UTC).isoformat(),
            "duration_minutes": 30,
        }
    )


def _append_audit(entry: dict) -> None:
    auth_storage.append_audit(entry)


@pytest.mark.asyncio
async def test_history_api_paginates_and_filters(async_client: AsyncClient) -> None:
    now = datetime.now(tz=UTC)
    run_a = "runA1234"
    run_b = "runB5678"

    _append_run(run_a, "closed")
    _append_run(run_b, "running")

    _append_audit(
        {
            "id": "evt1",
            "ts": (now - timedelta(minutes=5)).timestamp(),
            "action": "bot_started",
            "run_id": run_a,
        }
    )
    _append_audit(
        {
            "id": "evt2",
            "ts": (now - timedelta(minutes=3)).timestamp(),
            "action": "internal_transfer",
            "run_id": run_b,
        }
    )
    _append_audit(
        {
            "id": "evt3",
            "ts": (now - timedelta(minutes=1)).timestamp(),
            "action": "withdraw_prepare",
            "run_id": run_a,
            "tx_hash": "0x" + "dead" * 16,
        }
    )

    response = await async_client.get("/history/api", params={"offset": 0, "limit": 2})
    assert response.status_code == 200
    payload = response.json()
    assert payload["ok"] is True
    data = payload["data"]
    assert data["total"] == 3
    assert data["limit"] == 2
    assert data["offset"] == 0
    items = data["items"]
    assert len(items) == 2

    # Entries should be sorted descending by timestamp
    assert items[0]["id"] == "evt3"
    assert items[0]["run_status"] == "closed"
    assert items[0]["explorer_url"].startswith("https://arbiscan.io/tx/")
    assert items[1]["id"] == "evt2"

    # Pagination: fetch the remaining item and filter by run
    response_filter = await async_client.get("/history/api", params={"run_id": run_a})
    assert response_filter.status_code == 200
    filter_data = response_filter.json()["data"]
    assert filter_data["total"] == 2
    assert all(item["run_id"] == run_a for item in filter_data["items"])


@pytest.mark.asyncio
async def test_history_page_renders(async_client: AsyncClient) -> None:
    auth_storage.append_audit(
        {
            "id": "evt-page",
            "ts": datetime.now(tz=UTC).timestamp(),
            "action": "bot_started",
        }
    )
    response = await async_client.get("/history/")
    assert response.status_code == 200
    assert "History & Audit Trail" in response.text
