"""Tests for internal transfer endpoint."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient

from app.authz import storage as auth_storage
from app.trading import storage as trading_storage


async def _register_agent(async_client: AsyncClient, wallet: str, agent_address: str, private_key: str) -> None:
    await async_client.post("/authz/session", json={"address": wallet})
    response = await async_client.post(
        "/authz/agent",
        json={
            "label": "Primary",
            "agent_address": agent_address,
            "private_key": private_key,
        },
    )
    assert response.status_code == 200


def _seed_run(run_id: str, wallet: str) -> str:
    agent_entry = auth_storage.load_agents()[0]
    trading_storage.append_run(
        {
            "run_id": run_id,
            "market": "ETH-PERP",
            "usd_notional": "100",
            "leverage": 3,
            "wallet_address": wallet,
            "agent_address": agent_entry["agent_address"],
            "status": "running",
            "started_at": datetime.now(tz=UTC).isoformat(),
            "duration_minutes": 30,
        }
    )
    return agent_entry["agent_address"]


@pytest.mark.asyncio
async def test_internal_transfer_usd_send(monkeypatch, async_client: AsyncClient) -> None:
    wallet = "0x" + "feed" * 10
    agent_address = "0x" + "beef" * 10
    private_key = "0x" + "abcd" * 16

    await _register_agent(async_client, wallet, agent_address, private_key)
    run_id = "run" + datetime.now(tz=UTC).strftime("%H%M%S")
    _seed_run(run_id, wallet)

    calls: dict[str, tuple] = {}

    async def fake_usd_send(self, destination: str, amount: float):  # type: ignore[unused-argument]
        calls["usd_send"] = (destination, amount)
        return {"ok": True, "transfer": "mock"}

    async def fake_close(self):
        calls["close"] = True

    monkeypatch.setattr(
        "app.transfers.service.HyperliquidExchangeClient.usd_send",
        fake_usd_send,
    )
    monkeypatch.setattr(
        "app.transfers.service.HyperliquidExchangeClient.close",
        fake_close,
    )

    payload = {
        "kind": "usdSend",
        "amount": "42.5",
        "destination": "0x" + "cafe" * 10,
        "run_id": run_id,
    }

    response = await async_client.post("/api/internal/transfer", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["kind"] == "usdSend"
    assert data["run_id"] == run_id
    assert calls["usd_send"] == (payload["destination"].lower(), 42.5)
    assert calls["close"] is True

    audit_path = auth_storage.audit_log_path()
    audit_entries = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line]
    assert any(entry.get("action") == "internal_transfer" and entry.get("run_id") == run_id for entry in audit_entries)


@pytest.mark.asyncio
async def test_internal_transfer_spot_send(monkeypatch, async_client: AsyncClient) -> None:
    wallet = "0x" + "f0f0" * 10
    agent_address = "0x" + "dead" * 10
    private_key = "0x" + "face" * 16

    await _register_agent(async_client, wallet, agent_address, private_key)
    run_id = "run" + datetime.now(tz=UTC).strftime("%M%S%f")
    _seed_run(run_id, wallet)

    calls: dict[str, tuple] = {}

    async def fake_spot_send(self, coin: str, destination: str, amount: float):  # type: ignore[unused-argument]
        calls["spot_send"] = (coin, destination, amount)
        return {"ok": True}

    async def fake_close(self):
        calls["close"] = True

    monkeypatch.setattr(
        "app.transfers.service.HyperliquidExchangeClient.spot_send",
        fake_spot_send,
    )
    monkeypatch.setattr(
        "app.transfers.service.HyperliquidExchangeClient.close",
        fake_close,
    )

    payload = {
        "kind": "spotSend",
        "amount": "3.75",
        "asset": "arb",
        "destination": "0x" + "1234" * 10,
        "run_id": run_id,
    }

    response = await async_client.post("/api/internal/transfer", json=payload)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["asset"] == "ARB"
    assert data["run_id"] == run_id
    assert "transfer_id" in data

    assert calls["spot_send"] == ("ARB", payload["destination"].lower(), 3.75)
    assert calls["close"] is True

    audit_entries = [json.loads(line) for line in auth_storage.audit_log_path().read_text(encoding="utf-8").splitlines() if line]
    transfer_entry = next(entry for entry in audit_entries if entry.get("run_id") == run_id and entry.get("action") == "internal_transfer")
    assert transfer_entry["asset"] == "ARB"
    assert transfer_entry["kind"] == "spotSend"
