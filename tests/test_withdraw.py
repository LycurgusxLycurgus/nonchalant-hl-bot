"""Tests for withdrawal preparation endpoint."""

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
            "label": "Withdraw",
            "agent_address": agent_address,
            "private_key": private_key,
        },
    )
    assert response.status_code == 200


def _seed_run(run_id: str, wallet: str, agent_address: str) -> None:
    trading_storage.append_run(
        {
            "run_id": run_id,
            "market": "ETH-PERP",
            "usd_notional": "250",
            "leverage": 3,
            "wallet_address": wallet,
            "agent_address": agent_address,
            "status": "running",
            "started_at": datetime.now(tz=UTC).isoformat(),
            "duration_minutes": 30,
        }
    )


@pytest.mark.asyncio
async def test_withdraw_prepare_returns_typed_data(monkeypatch, async_client: AsyncClient) -> None:
    wallet = "0x" + "c0de" * 10
    agent_address = "0x" + "face" * 10
    private_key = "0x" + "f00d" * 16

    await _register_agent(async_client, wallet, agent_address, private_key)
    run_id = "run" + datetime.now(tz=UTC).strftime("%H%M%S%f")
    _seed_run(run_id, wallet, agent_address)

    payload = {
        "run_id": run_id,
        "amount_usd": "150.75",
        "l1_destination": "0x" + "aaaa" * 10,
        "chain": "arbitrum",
    }

    response = await async_client.post("/api/withdraw/prepare", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    data = body["data"]

    assert data["run_id"] == run_id
    assert data["l1_destination"] == payload["l1_destination"].lower()
    assert data["amount_usd"] == "150.75"
    assert "transfer_id" in data

    typed = data["typed_data"]
    assert typed["primaryType"] == "HLWithdraw"
    assert typed["domain"]["verifyingContract"].lower() == agent_address.lower()
    assert typed["domain"]["chainId"] == 42161
    assert {field["name"] for field in typed["types"]["HLWithdraw"]} == {"destination", "amount", "nonce"}
    message = typed["message"]
    assert message["destination"] == payload["l1_destination"].lower()
    assert message["amount"] == "150.75"
    assert isinstance(message["nonce"], int)

    audit_entries = [json.loads(line) for line in auth_storage.audit_log_path().read_text(encoding="utf-8").splitlines() if line]
    entry = next(e for e in audit_entries if e.get("action") == "withdraw_prepare" and e.get("run_id") == run_id)
    assert entry["typed_data"]["message"]["amount"] == "150.75"
    assert entry["payload"]["amount_usd"] == "150.75"
