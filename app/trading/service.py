"""Trading service orchestrating bot lifecycle actions."""

from __future__ import annotations

import asyncio
import secrets
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from fastapi import BackgroundTasks, HTTPException

from app.authz import storage as auth_storage
from app.config import get_settings
from app.lib.hyperliquid_adapter import (
    ExchangeCredentials,
    HyperliquidAPIError,
    HyperliquidExchangeClient,
)
from app.trading import storage as trading_storage
from app.trading.schemas import BotRunRecord, BotStartRequest
from app.trading.schemas import BotStopRequest
from app.monitoring.service import MonitoringService
from app.lib.logger import get_logger
from app.lib.metrics import METRICS


logger = get_logger(__name__)

_RUN_MONITOR_TASKS: dict[str, asyncio.Task[None]] = {}
_MONITOR_POLL_SECONDS = 5.0


def _select_agent(wallet_address: str | None, preferred_agent: str | None) -> dict[str, Any]:
    normalized_preferred = auth_storage.normalize_address(preferred_agent)

    candidates = auth_storage.agents_for_wallet(wallet_address) if wallet_address else []
    if not candidates:
        candidates = auth_storage.load_agents()

    if not candidates:
        raise HTTPException(status_code=400, detail="No agent wallet registered")

    if normalized_preferred:
        for entry in candidates:
            if auth_storage.normalize_address(entry.get("agent_address")) == normalized_preferred:
                return entry

    return candidates[0]


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
    monitoring: MonitoringService,
    active_agent_address: str | None = None,
) -> BotRunRecord:
    if not wallet_address:
        raise HTTPException(status_code=400, detail="Wallet not connected")

    agent_entry = _select_agent(wallet_address, active_agent_address)
    _assert_agent_available(agent_entry["agent_address"])
    private_key = _decrypt_private_key(agent_entry)

    settings = get_settings()
    client = HyperliquidExchangeClient(
        ExchangeCredentials(
            agent_entry["agent_address"],
            private_key,
            account_address=agent_entry.get("owner_wallet"),
        ),
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
    except HyperliquidAPIError as exc:
        METRICS.increment("bot.start.error")
        logger.warning(
            "bot_start_rejected",
            extra={
                "market": payload.market,
                "agent_address": agent_entry["agent_address"],
                "action": exc.action,
                "response": exc.response,
            },
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
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

    run_payload = _serialize_record(record)
    run_payload["end_at"] = (started_at + timedelta(minutes=payload.duration_minutes)).isoformat()
    trading_storage.append_run(run_payload)

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

    credentials = ExchangeCredentials(
        agent_entry["agent_address"],
        private_key,
        account_address=agent_entry.get("owner_wallet"),
    )
    await monitoring.register_run(record)

    _schedule_monitor_task(
        run_id,
        _monitor_run(
            run_id=run_id,
            market=payload.market,
            duration_minutes=payload.duration_minutes,
            credentials=credentials,
            base_url=settings.hl_rest_base,
            monitoring=monitoring,
        ),
    )

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
        ExchangeCredentials(
            agent_entry["agent_address"],
            private_key,
            account_address=agent_entry.get("owner_wallet"),
        ),
        base_url=settings.hl_rest_base,
    )

    market = run_entry.get("market")
    if not isinstance(market, str):
        raise HTTPException(status_code=500, detail="Run entry missing market")

    monitor_task = _RUN_MONITOR_TASKS.pop(payload.run_id, None)
    if monitor_task is not None:
        monitor_task.cancel()

    try:
        await client.cancel_open_orders(market)
        await client.close_position(market)
    except HyperliquidAPIError as exc:
        METRICS.increment("bot.stop.error")
        logger.warning(
            "bot_stop_rejected",
            extra={"market": market, "action": exc.action, "response": exc.response},
        )
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    finally:
        await client.close()

    closed_at = datetime.now(tz=UTC)
    trading_storage.update_run(
        payload.run_id,
        {"status": "closed", "closed_at": closed_at.isoformat()},
    )

    try:
        await monitoring.mark_status(payload.run_id, "closed")
    except KeyError:
        logger.warning(
            "monitoring_status_skip",
            extra={"run_id": payload.run_id, "reason": "snapshot_missing"},
        )

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


def _format_notional(value: Any) -> str:
    try:
        amount = Decimal(str(value))
        return f"{amount.quantize(Decimal('0.01')):,.2f}"
    except (InvalidOperation, ValueError, TypeError):
        return str(value)


def get_start_overview(limit: int = 5) -> dict[str, Any]:
    """Collect recent runs, metrics, and status for the overview panel."""

    runs = trading_storage.load_runs()
    sorted_runs = sorted(runs, key=lambda entry: entry.get("started_at", ""), reverse=True)

    recent_runs: list[dict[str, Any]] = []
    for entry in sorted_runs[:limit]:
        run_id = entry.get("run_id", "")
        run_id_short = run_id
        if isinstance(run_id, str) and len(run_id) >= 10:
            run_id_short = f"{run_id[:6]}…{run_id[-4:]}"

        started_iso = entry.get("started_at")
        started_display = started_iso
        if isinstance(started_iso, str):
            try:
                started_dt = datetime.fromisoformat(started_iso)
                started_display = started_dt.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
            except ValueError:
                started_display = started_iso

        recent_runs.append(
            {
                "run_id": run_id,
                "run_id_short": run_id_short,
                "market": entry.get("market", ""),
                "usd_notional": _format_notional(entry.get("usd_notional")),
                "leverage": entry.get("leverage"),
                "status": entry.get("status", ""),
                "duration_minutes": entry.get("duration_minutes"),
                "end_at": entry.get("end_at"),
                "wallet_address": entry.get("wallet_address"),
                "started_at": started_iso,
                "started_at_display": started_display,
            }
        )

    metrics_snapshot = METRICS.snapshot()
    agent_count = len(auth_storage.load_agents())
    active_runs = sum(1 for entry in runs if entry.get("status") in {"running", "starting"})

    return {
        "recent_runs": recent_runs,
        "metrics": metrics_snapshot,
        "agent_count": agent_count,
        "active_runs": active_runs,
        "total_runs": len(runs),
    }


def _schedule_monitor_task(run_id: str, coro: asyncio.coroutine) -> None:
    existing = _RUN_MONITOR_TASKS.pop(run_id, None)
    if existing is not None:
        existing.cancel()

    task = asyncio.create_task(coro)
    _RUN_MONITOR_TASKS[run_id] = task

    def _cleanup(_task: asyncio.Task) -> None:
        _RUN_MONITOR_TASKS.pop(run_id, None)
        try:
            _task.result()
        except asyncio.CancelledError:
            pass
        except Exception:  # pragma: no cover - logged for observability
            logger.exception("monitor_task_failed", extra={"run_id": run_id})

    task.add_done_callback(_cleanup)


async def _monitor_run(
    *,
    run_id: str,
    market: str,
    duration_minutes: float,
    credentials: ExchangeCredentials,
    base_url: str,
    monitoring: MonitoringService,
) -> None:
    end_at = datetime.now(tz=UTC) + timedelta(minutes=duration_minutes)
    settings = get_settings()
    poll_seconds = getattr(settings, "monitor_poll_seconds", _MONITOR_POLL_SECONDS)

    async with HyperliquidExchangeClient(credentials, base_url=base_url) as client:
        while True:
            # Check current run status
            run_entry = trading_storage.get_run(run_id)
            if not run_entry:
                break

            status = run_entry.get("status")
            if status in {"closed", "cancelled", "failed"}:
                break

            snapshot = await _fetch_position_snapshot(client, market)
            if snapshot is not None:
                await monitoring.update_snapshot(run_id, status="running", **snapshot)
            else:
                await monitoring.update_snapshot(run_id, status="running")

            now = datetime.now(tz=UTC)
            if now >= end_at:
                try:
                    await client.cancel_open_orders(market)
                    await client.close_position(market)
                except HyperliquidAPIError as exc:  # pragma: no cover - network failure path
                    logger.warning(
                        "auto_close_failed",
                        extra={"run_id": run_id, "market": market, "error": str(exc.response)},
                    )
                else:
                    closed_at = datetime.now(tz=UTC)
                    trading_storage.update_run(
                        run_id,
                        {
                            "status": "closed",
                            "closed_at": closed_at.isoformat(),
                            "auto_closed": True,
                        },
                    )
                    await monitoring.mark_status(run_id, "closed")
                    break

            try:
                await asyncio.sleep(poll_seconds)
            except asyncio.CancelledError:
                break


async def _fetch_position_snapshot(
    client: HyperliquidExchangeClient,
    market: str,
) -> dict[str, Any] | None:
    try:
        position = await client.get_perp_position(market)
    except Exception:  # pragma: no cover - defensive logging
        logger.exception("position_poll_failed", extra={"market": market})
        return None

    if position is None:
        return {
            "position_notional": Decimal("0"),
            "entry_price": Decimal("0"),
            "mark_price": Decimal(str(position["mark_price"])) if position else Decimal("0"),
            "realized_pnl": Decimal("0"),
            "unrealized_pnl": Decimal("0"),
        }

    return {
        "position_notional": position["position_notional"],
        "entry_price": position["entry_price"],
        "mark_price": position["mark_price"],
        "realized_pnl": position["realized_pnl"],
        "unrealized_pnl": position["unrealized_pnl"],
    }
