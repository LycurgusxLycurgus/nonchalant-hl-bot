"""Observability and hardening tests."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from httpx import AsyncClient

from app.authz import storage as auth_storage
from app.trading import storage as trading_storage


async def _register_agent(async_client: AsyncClient, wallet: str, agent_address: str, private_key: str) -> None:
    await async_client.post("/authz/session", json={"address": wallet})
    response = await async_client.post(
        "/authz/agent",
        json={
            "label": "Obs",
            "agent_address": agent_address,
            "private_key": private_key,
        },
    )
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_start_rate_limit_enforced(monkeypatch, async_client: AsyncClient, app: FastAPI) -> None:
    wallet = "0x" + "feed" * 10
    agent_address = "0x" + "face" * 10
    private_key = "0x" + "abcd" * 16

    await _register_agent(async_client, wallet, agent_address, private_key)
    app.state.rate_limit_per_minute = 1

    payload = {
        "market": "eth-perp",
        "usd_notional": "25",
        "leverage": 2,
        "duration_minutes": 5,
    }

    first = await async_client.post("/api/bot/start", json=payload)
    assert first.status_code == 200
    await asyncio.sleep(0)

    second = await async_client.post("/api/bot/start", json=payload)
    assert second.status_code == 429
    assert second.json()["detail"] == "Too many requests"

    metrics_response = await async_client.get("/metrics")
    data = metrics_response.json()["data"]
    assert data["bot.start.attempt"] >= 1
    assert data["bot.start.success"] >= 1


@pytest.mark.asyncio
async def test_nonce_guard_blocks_parallel_runs(async_client: AsyncClient) -> None:
    wallet = "0x" + "dead" * 10
    agent_address = "0x" + "beef" * 10
    private_key = "0x" + "f00d" * 16

    await _register_agent(async_client, wallet, agent_address, private_key)

    run_id = "run" + datetime.now(tz=UTC).strftime("%H%M%S")
    trading_storage.append_run(
        {
            "run_id": run_id,
            "market": "ETH-PERP",
            "usd_notional": "100",
            "leverage": 2,
            "wallet_address": wallet,
            "agent_address": agent_address,
            "status": "running",
            "started_at": datetime.now(tz=UTC).isoformat(),
            "duration_minutes": 15,
        }
    )

    payload = {
        "market": "eth-perp",
        "usd_notional": "50",
        "leverage": 2,
        "duration_minutes": 5,
    }

    response = await async_client.post("/api/bot/start", json=payload)
    assert response.status_code == 409
    assert response.json()["detail"] == "Agent already assigned to an active run"

    # Guard should not alter audit log
    audit_entries = list(auth_storage.load_agents())
    assert audit_entries
