"""Trading service orchestrating bot lifecycle actions."""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import BackgroundTasks, HTTPException

from app.authz import storage as auth_storage
from app.config import get_settings
from app.lib.hyperliquid_adapter import ExchangeCredentials, HyperliquidExchangeClient
from app.trading import storage as trading_storage
from app.trading.schemas import BotRunRecord, BotStartRequest
from app.trading.schemas import BotStopRequest
from app.monitoring.service import MonitoringService
from app.lib.logger import get_logger
from app.lib.metrics import METRICS


logger = get_logger(__name__)


def _select_agent() -> dict[str, Any]:
    agents = auth_storage.load_agents()
    if not agents:
        raise HTTPException(status_code=400, detail="No agent wallet registered")
    # For Phase 4 we use the first agent entry. Later phases can support multiple agents.
    return agents[0]


def _decrypt_private_key(agent_entry: dict[str, Any]) -> str:
    cipher = agent_entry.get("key_cipher")
    if not cipher:
        raise HTTPException(status_code=400, detail="Agent entry missing key cipher")
    fernet = auth_storage.get_fernet()
    try:
        return fernet.decrypt(cipher.encode("utf-8")).decode("utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail="Unable to decrypt agent key") from exc


def _serialize_record(record: BotRunRecord) -> dict[str, Any]:
    data = record.model_dump()
    data["started_at"] = record.started_at.isoformat()
    data["usd_notional"] = str(record.usd_notional)
    return data


async def start_bot_run(
    payload: BotStartRequest,
    wallet_address: str,
    background_tasks: BackgroundTasks,
) -> BotRunRecord:
    if not wallet_address:
        raise HTTPException(status_code=400, detail="Wallet not connected")

    agent_entry = _select_agent()
    _assert_agent_available(agent_entry["agent_address"])
    private_key = _decrypt_private_key(agent_entry)

    settings = get_settings()
    client = HyperliquidExchangeClient(
        ExchangeCredentials(agent_entry["agent_address"], private_key),
        base_url=settings.hl_rest_base,
    )

    METRICS.increment("bot.start.attempt")
    logger.info(
        "bot_start_attempt",
        extra={
            "market": payload.market,
            "leverage": payload.leverage,
            "wallet_address": wallet_address,
            "agent_address": agent_entry["agent_address"],
        },
    )
    try:
        await client.set_isolated_leverage(payload.market, payload.leverage)
        await client.place_market_order(payload.market, float(payload.usd_notional))
    finally:
        await client.close()

    run_id = secrets.token_hex(16)
    started_at = datetime.now(tz=UTC)
    record = BotRunRecord(
        run_id=run_id,
        market=payload.market,
        usd_notional=payload.usd_notional,
        leverage=payload.leverage,
        wallet_address=wallet_address,
        agent_address=agent_entry["agent_address"],
        status="running",
        started_at=started_at,
        duration_minutes=payload.duration_minutes,
    )

    trading_storage.append_run(_serialize_record(record))

    audit_entry = {
        "id": secrets.token_hex(16),
        "ts": started_at.timestamp(),
        "action": "bot_started",
        "run_id": run_id,
        "market": payload.market,
        "wallet_address": wallet_address,
        "agent_address": agent_entry["agent_address"],
    }
    auth_storage.append_audit(audit_entry)

    background_tasks.add_task(_complete_run, run_id)

    METRICS.increment("bot.start.success")
    logger.info(
        "bot_start_success",
        extra={
            "run_id": run_id,
            "market": payload.market,
            "wallet_address": wallet_address,
            "agent_address": agent_entry["agent_address"],
        },
    )

    return record


async def _complete_run(run_id: str) -> None:
    # Placeholder background worker. Later phases will perform actual monitoring until
    # the desired duration or stop signal is reached. For now we simply yield control
    # and mark the run as completed.
    await asyncio.sleep(0)
    trading_storage.update_run(run_id, {"status": "completed", "completed_at": datetime.now(tz=UTC).isoformat()})


async def stop_bot_run(
    payload: BotStopRequest,
    monitoring: MonitoringService,
) -> dict[str, Any]:
    run_entry = trading_storage.get_run(payload.run_id)
    if not run_entry:
        raise HTTPException(status_code=404, detail="Run not found")

    if run_entry.get("status") not in {"running", "completed"}:
        raise HTTPException(status_code=400, detail="Run is not active")

    agents = auth_storage.load_agents()
    agent_address = run_entry.get("agent_address")
    agent_entry = next((a for a in agents if a.get("agent_address") == agent_address), None)
    if not agent_entry:
        raise HTTPException(status_code=400, detail="Agent wallet unavailable")

    private_key = _decrypt_private_key(agent_entry)

    settings = get_settings()
    client = HyperliquidExchangeClient(
        ExchangeCredentials(agent_entry["agent_address"], private_key),
        base_url=settings.hl_rest_base,
    )

    market = run_entry.get("market")
    if not isinstance(market, str):
        raise HTTPException(status_code=500, detail="Run entry missing market")

    try:
        await client.cancel_open_orders(market)
        await client.close_position(market)
    finally:
        await client.close()

    closed_at = datetime.now(tz=UTC)
    trading_storage.update_run(
        payload.run_id,
        {"status": "closed", "closed_at": closed_at.isoformat()},
    )

    await monitoring.mark_status(payload.run_id, "closed")

    audit_entry = {
        "id": secrets.token_hex(16),
        "ts": closed_at.timestamp(),
        "action": "bot_stopped",
        "run_id": payload.run_id,
        "market": market,
        "agent_address": agent_entry["agent_address"],
    }
    auth_storage.append_audit(audit_entry)

    METRICS.increment("bot.stop.success")
    logger.info(
        "bot_stop_success",
        extra={
            "run_id": payload.run_id,
            "market": market,
            "agent_address": agent_entry["agent_address"],
        },
    )

    return {
        "run_id": payload.run_id,
        "market": market,
        "status": "closed",
        "closed_at": closed_at,
    }


def _assert_agent_available(agent_address: str) -> None:
    runs = trading_storage.load_runs()
    for entry in runs:
        if entry.get("agent_address") == agent_address and entry.get("status") in {"running", "starting"}:
            logger.warning(
                "agent_nonce_guard_triggered",
                extra={"agent_address": agent_address, "run_id": entry.get("run_id")},
            )
            METRICS.increment("bot.start.nonce_guard")
            raise HTTPException(status_code=409, detail="Agent already assigned to an active run")
