"""API routes for trading bot lifecycle."""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse

from app.trading.schemas import BotStartRequest, BotStartResponse, BotStopRequest, BotStopResponse
from app.trading.service import start_bot_run, stop_bot_run
from app.lib.rate_limiter import enforce_rate_limit
from app.monitoring.routes import get_monitoring_service
from app.monitoring import MonitoringService

router = APIRouter()


@router.post("/start")
async def start_bot_endpoint(
    request: Request,
    payload: BotStartRequest,
    background_tasks: BackgroundTasks,
    monitoring: MonitoringService = Depends(get_monitoring_service),
) -> JSONResponse:
    """Start a trading bot by placing initial orders and recording the run."""

    enforce_rate_limit(request, "bot.start")
    wallet_address: str | None = request.session.get("wallet_address")
    active_agent_address: str | None = request.session.get("active_agent_address")
    record = await start_bot_run(
        payload,
        wallet_address or "",
        background_tasks,
        monitoring,
        active_agent_address=active_agent_address,
    )
    await monitoring.register_run(record)
    response_payload = BotStartResponse.from_record(record).model_dump(mode="json")
    return JSONResponse({"ok": True, "data": response_payload})


@router.post("/stop")
async def stop_bot_endpoint(
    request: Request,
    payload: BotStopRequest,
    monitoring: MonitoringService = Depends(get_monitoring_service),
) -> JSONResponse:
    """Stop a trading bot run by cancelling orders and closing position."""

    enforce_rate_limit(request, "bot.stop")
    result = await stop_bot_run(payload, monitoring)
    response_payload = BotStopResponse.from_record(**result).model_dump(mode="json")
    return JSONResponse({"ok": True, "data": response_payload})
