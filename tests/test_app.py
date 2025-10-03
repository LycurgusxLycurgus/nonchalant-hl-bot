"""Application tests spanning Phases 0-4."""

import asyncio
import json
from decimal import Decimal

import pytest
from httpx import AsyncClient

from app.deposit.routes import _extract_usd_balance
from app.lib.info_client import InfoClientError
from app.trading import storage as trading_storage


@pytest.mark.asyncio
async def test_health_endpoint(async_client: AsyncClient) -> None:
    """Health route should return healthy status envelope."""
    response = await async_client.get("/health")

    assert response.status_code == 200
    body = response.json()
    assert body == {"ok": True, "data": {"status": "healthy"}}


@pytest.mark.asyncio
async def test_root_template_renders_wallet_state(async_client: AsyncClient) -> None:
    """Root view should render hero copy and wallet panel placeholders."""
    response = await async_client.get("/")

    assert response.status_code == 200
    html = response.text
    assert "Phase 0 / Skeleton" in html
    assert "Hyperliquid trading agent platform" in html
    assert "Connect wallet" in html
    assert "Not connected" in html


@pytest.mark.asyncio
async def test_wallet_session_roundtrip(async_client: AsyncClient) -> None:
    """Wallet session endpoints should persist and clear address."""
    address = "0x" + "abcd" * 10

    post_response = await async_client.post("/authz/session", json={"address": address})
    assert post_response.status_code == 200
    assert post_response.json() == {"ok": True, "data": {"address": address.lower()}}

    get_response = await async_client.get("/authz/session")
    assert get_response.status_code == 200
    assert get_response.json() == {"ok": True, "data": {"address": address.lower()}}

    delete_response = await async_client.delete("/authz/session")
    assert delete_response.status_code == 200
    assert delete_response.json() == {"ok": True, "data": {"address": None}}

    empty_get = await async_client.get("/authz/session")
    assert empty_get.json() == {"ok": True, "data": {"address": None}}


@pytest.mark.asyncio
async def test_wallet_session_rejects_invalid_address(async_client: AsyncClient) -> None:
    """Invalid wallet addresses should trigger validation errors."""
    response = await async_client.post("/authz/session", json={"address": "0x123"})

    assert response.status_code == 422


@pytest.mark.asyncio
async def test_agent_registration_encrypts_and_logs(async_client: AsyncClient, storage_dir) -> None:
    """Agent registration endpoint should store encrypted key and log audit metadata."""

    payload = {
        "label": "Primary",
        "agent_address": "0x" + "abcd" * 10,
        "private_key": "0x" + "1234" * 16,
    }

    response = await async_client.post("/authz/agent", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["agent_address"] == payload["agent_address"].lower()
    assert "stored_at" in body["data"]

    registry_path = storage_dir / "agents.json"
    assert registry_path.exists()
    registry = json.loads(registry_path.read_text(encoding="utf-8"))
    assert registry[0]["agent_address"] == payload["agent_address"].lower()
    assert registry[0]["label"] == payload["label"]
    assert registry[0]["key_cipher"].startswith("gAAAA")  # Fernet token prefix
    assert payload["private_key"] not in registry_path.read_text(encoding="utf-8")

    audit_path = storage_dir / "audit_log.jsonl"
    assert audit_path.exists()
    lines = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines() if line]
    assert lines[0]["action"] == "agent_registered"
    assert lines[0]["agent_address"] == payload["agent_address"].lower()


@pytest.mark.asyncio
async def test_extract_usd_balance_handles_various_shapes() -> None:
    """Balance extraction should support minimal payload shapes."""

    payload = {
        "user": {
            "balances": [
                {"coin": "USDC", "total": "123.456"},
                {"coin": "ETH", "total": "1.2"},
            ]
        }
    }
    assert _extract_usd_balance(payload) == Decimal("123.456")

    payload_alt = {
        "user": {
            "spotBalances": [
                {"symbol": "BTC", "balance": 0.01},
                {"symbol": "USDC", "available": 200},
            ]
        }
    }
    assert _extract_usd_balance(payload_alt) == Decimal("200")

    payload_missing = {"user": {}}
    assert _extract_usd_balance(payload_missing) == Decimal("0")


@pytest.mark.asyncio
async def test_balance_api_returns_formatted_usdc(monkeypatch, async_client: AsyncClient) -> None:
    """Balance API should return formatted USDC string when wallet is connected."""

    wallet_address = "0x" + "beef" * 10
    await async_client.post("/authz/session", json={"address": wallet_address})

    async def fake_fetch(address: str):
        assert address == wallet_address.lower()
        return {"user": {"balances": [{"coin": "USDC", "total": "1234.567"}]}}

    monkeypatch.setattr("app.deposit.routes.InfoClient.fetch_balances", lambda self, addr: fake_fetch(addr))

    response = await async_client.get("/deposit/api/balance")
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["data"]["wallet_address"] == wallet_address.lower()
    assert body["data"]["balance"] == "1,234.57"


@pytest.mark.asyncio
async def test_balance_partial_renders_error_when_unavailable(monkeypatch, async_client: AsyncClient) -> None:
    """Balance partial should display error messaging when Info endpoint fails."""

    wallet_address = "0x" + "cafe" * 10
    await async_client.post("/authz/session", json={"address": wallet_address})

    async def failing_fetch(_addr: str):
        raise InfoClientError("boom")

    monkeypatch.setattr(
        "app.deposit.routes.InfoClient.fetch_balances",
        lambda self, addr: failing_fetch(addr),
    )

    response = await async_client.get("/deposit/partial/balance")
    assert response.status_code == 200
    html = response.text
    assert "Unable to reach Hyperliquid Info endpoint" in html


@pytest.mark.asyncio
async def test_start_bot_endpoint_persists_run(monkeypatch, async_client: AsyncClient, storage_dir) -> None:
    """POST /api/bot/start should trigger exchange calls and persist run metadata."""

    wallet_address = "0x" + "f00d" * 10
    agent_address = "0x" + "beef" * 10
    private_key = "0x" + "1234" * 16

    await async_client.post("/authz/session", json={"address": wallet_address})
    register_response = await async_client.post(
        "/authz/agent",
        json={
            "label": "Primary",
            "agent_address": agent_address,
            "private_key": private_key,
        },
    )
    assert register_response.status_code == 200

    calls: dict[str, tuple] = {}

    async def fake_set(self, market: str, leverage: int):
        calls["set_leverage"] = (market, leverage)

    async def fake_order(self, market: str, notional: float):
        calls["market_order"] = (market, notional)

    async def fake_close(self):
        calls["close"] = True

    monkeypatch.setattr(
        "app.trading.service.HyperliquidExchangeClient.set_isolated_leverage",
        fake_set,
    )
    monkeypatch.setattr(
        "app.trading.service.HyperliquidExchangeClient.place_market_order",
        fake_order,
    )
    monkeypatch.setattr(
        "app.trading.service.HyperliquidExchangeClient.close",
        fake_close,
    )

    payload = {
        "market": "btc-perp",
        "usd_notional": "250.5",
        "leverage": 3,
        "duration_minutes": 5,
    }

    response = await async_client.post("/api/bot/start", json=payload)
    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    data = body["data"]
    assert data["status"] == "running"
    assert data["market"] == "BTC-PERP"
    assert "run_id" in data

    # Background tasks execute after response; yield control to allow completion
    await asyncio.sleep(0)

    runs_json = trading_storage.runs_path().read_text(encoding="utf-8")
    runs = json.loads(runs_json)
    assert runs
    latest = runs[-1]
    assert latest["run_id"] == data["run_id"]
    assert latest["status"] == "completed"
    assert latest["wallet_address"] == wallet_address.lower()
    assert "completed_at" in latest

    assert calls["set_leverage"] == ("BTC-PERP", 3)
    assert calls["market_order"] == ("BTC-PERP", 250.5)
    assert calls["close"] is True


@pytest.mark.asyncio
async def test_start_bot_requires_wallet(async_client: AsyncClient) -> None:
    """POST /api/bot/start should fail when wallet is not connected."""

    agent_address = "0x" + "face" * 10
    private_key = "0x" + "9999" * 16

    await async_client.post(
        "/authz/agent",
        json={
            "label": "Secondary",
            "agent_address": agent_address,
            "private_key": private_key,
        },
    )

    response = await async_client.post(
        "/api/bot/start",
        json={
            "market": "eth-perp",
            "usd_notional": "100",
            "leverage": 2,
        },
    )
    assert response.status_code == 400
    body = response.json()
    assert body["detail"] == "Wallet not connected"


@pytest.mark.asyncio
async def test_stop_bot_closes_run(monkeypatch, async_client: AsyncClient, app) -> None:
    """Stop endpoint should cancel orders, close position, and update run state."""

    hub = app.state.monitoring_hub
    await hub.reset()

    wallet_address = "0x" + "feed" * 10
    agent_address = "0x" + "babe" * 10
    private_key = "0x" + "abcd" * 16

    await async_client.post("/authz/session", json={"address": wallet_address})
    register_response = await async_client.post(
        "/authz/agent",
        json={
            "label": "Runner",
            "agent_address": agent_address,
            "private_key": private_key,
        },
    )
    assert register_response.status_code == 200

    calls: dict[str, tuple] = {}

    async def fake_set(self, market: str, leverage: int):  # type: ignore[unused-argument]
        return None

    async def fake_order(self, market: str, notional: float):
        return None

    async def fake_cancel(self, market: str):
        calls["cancel_open_orders"] = (market,)
        return None

    async def fake_close_pos(self, market: str):
        calls["close_position"] = (market,)
        return None

    async def fake_close(self):
        calls["close"] = True

    monkeypatch.setattr(
        "app.trading.service.HyperliquidExchangeClient.set_isolated_leverage",
        fake_set,
    )
    monkeypatch.setattr(
        "app.trading.service.HyperliquidExchangeClient.place_market_order",
        fake_order,
    )
    monkeypatch.setattr(
        "app.trading.service.HyperliquidExchangeClient.cancel_open_orders",
        fake_cancel,
    )
    monkeypatch.setattr(
        "app.trading.service.HyperliquidExchangeClient.close_position",
        fake_close_pos,
    )
    monkeypatch.setattr(
        "app.trading.service.HyperliquidExchangeClient.close",
        fake_close,
    )

    payload = {
        "market": "eth-perp",
        "usd_notional": "150",
        "leverage": 2,
        "duration_minutes": 10,
    }

    start_response = await async_client.post("/api/bot/start", json=payload)
    assert start_response.status_code == 200
    start_data = start_response.json()["data"]
    run_id = start_data["run_id"]

    # Allow background completion task to run
    await asyncio.sleep(0)

    stop_response = await async_client.post("/api/bot/stop", json={"run_id": run_id})
    assert stop_response.status_code == 200
    stop_data = stop_response.json()["data"]
    assert stop_data["status"] == "closed"
    assert stop_data["run_id"] == run_id
    assert "closed_at" in stop_data

    runs_json = trading_storage.runs_path().read_text(encoding="utf-8")
    runs = json.loads(runs_json)
    latest = next(entry for entry in runs if entry["run_id"] == run_id)
    assert latest["status"] == "closed"
    assert "closed_at" in latest

    snapshots = await hub.list_snapshots()
    snapshot = next((s for s in snapshots if s.run_id == run_id), None)
    assert snapshot is not None
    assert snapshot.status == "closed"

    assert calls["cancel_open_orders"] == ("ETH-PERP",)
    assert calls["close_position"] == ("ETH-PERP",)
    assert calls["close"] is True
