"""UI routes for trading start flow."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import ValidationError

from app.lib.rate_limiter import enforce_rate_limit
from app.monitoring import MonitoringService
from app.monitoring.routes import get_monitoring_service
from app.trading.constants import (
    DEFAULT_DURATION_MINUTES,
    DEFAULT_LEVERAGE,
    DEFAULT_MARKETS,
    DEFAULT_NOTIONAL,
)
from app.trading.schemas import BotStartRequest, BotStopRequest
from app.trading.service import get_start_overview, start_bot_run, stop_bot_run

router = APIRouter()


def _templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[attr-defined]


def _wallet_context(request: Request) -> dict[str, Any]:
    builder = getattr(request.app.state, "wallet_context_builder", None)  # type: ignore[attr-defined]
    if callable(builder):
        return builder(request.session)
    return {"wallet_address": None, "wallet_address_short": None}


def _default_form_values() -> dict[str, str]:
    return {
        "market": DEFAULT_MARKETS[0],
        "usd_notional": str(DEFAULT_NOTIONAL),
        "leverage": str(DEFAULT_LEVERAGE),
        "duration_minutes": str(DEFAULT_DURATION_MINUTES),
    }


def _base_context(
    request: Request,
    *,
    form_values: dict[str, str],
    form_errors: dict[str, str],
    form_success: str | None,
    stop_target_run_id: str | None,
) -> dict[str, Any]:
    overview = get_start_overview()
    context: dict[str, Any] = {
        "request": request,
        "markets": DEFAULT_MARKETS,
        "default_notional": DEFAULT_NOTIONAL,
        "default_leverage": DEFAULT_LEVERAGE,
        "form_values": form_values,
        "form_errors": form_errors,
        "form_success": form_success,
        "start_overview": overview,
        "stop_target_run_id": stop_target_run_id,
        **_wallet_context(request),
    }
    return context


@router.get("/start-panel", response_class=HTMLResponse)
async def start_panel(request: Request) -> HTMLResponse:
    context = _base_context(
        request,
        form_values=_default_form_values(),
        form_errors={},
        form_success=None,
        stop_target_run_id=None,
    )
    return _templates(request).TemplateResponse("trading/_start_panel.html", context)


@router.post("/start", response_class=HTMLResponse)
async def start_bot_form(
    request: Request,
    background_tasks: BackgroundTasks,
    monitoring: MonitoringService = Depends(get_monitoring_service),
) -> HTMLResponse:
    enforce_rate_limit(request, "bot.start")

    form = await request.form()
    raw_values = {
        "market": (form.get("market") or "").strip(),
        "usd_notional": (form.get("usd_notional") or "").strip(),
        "leverage": (form.get("leverage") or "").strip(),
        "duration_minutes": (form.get("duration_minutes") or "").strip(),
    }

    wallet_address: str | None = request.session.get("wallet_address")
    form_errors: dict[str, str] = {}
    form_success: str | None = None

    if not wallet_address:
        form_errors["wallet"] = "Connect your wallet to start a bot run."

    payload: BotStartRequest | None = None
    if not form_errors:
        try:
            payload = BotStartRequest(**raw_values)
        except ValidationError as exc:
            for error in exc.errors():
                field = error.get("loc", ["_"])[-1]
                message = error.get("msg", "Invalid value")
                form_errors[str(field)] = message

    if payload and wallet_address:
        try:
            active_agent_address = request.session.get("active_agent_address")
            record = await start_bot_run(
                payload,
                wallet_address,
                background_tasks,
                monitoring,
                active_agent_address=active_agent_address,
            )
            short_id = f"{record.run_id[:6]}…{record.run_id[-4:]}"
            form_success = f"Bot run {short_id} on {record.market} is now live."
            # Reset form to defaults after successful launch
            raw_values = _default_form_values()
        except HTTPException as exc:
            if exc.status_code >= 500:
                form_errors["__all__"] = "Failed to start bot. Please retry shortly."
            else:
                form_errors["__all__"] = exc.detail if isinstance(exc.detail, str) else "Unable to start bot."

    context = _base_context(
        request,
        form_values=raw_values,
        form_errors=form_errors,
        form_success=form_success,
        stop_target_run_id=None,
    )

    template_name = "trading/start_panel_response.html"
    return _templates(request).TemplateResponse(template_name, context)


@router.post("/stop", response_class=HTMLResponse)
async def stop_bot_form(
    request: Request,
    monitoring: MonitoringService = Depends(get_monitoring_service),
) -> HTMLResponse:
    enforce_rate_limit(request, "bot.stop")

    form = await request.form()
    run_id_raw = (form.get("run_id") or "").strip()

    form_errors: dict[str, str] = {}
    form_success: str | None = None
    stop_target_run_id: str | None = run_id_raw or None

    payload: BotStopRequest | None = None
    if not run_id_raw:
        form_errors["run_id"] = "Select a run to stop."
    else:
        try:
            payload = BotStopRequest(run_id=run_id_raw)
        except ValidationError as exc:
            for error in exc.errors():
                message = error.get("msg", "Invalid run identifier")
                form_errors["run_id"] = message

    if payload is not None:
        try:
            result = await stop_bot_run(payload, monitoring)
            stop_target_run_id = result["run_id"]
            short_id = f"{result['run_id'][:6]}…{result['run_id'][-4:]}"
            form_success = f"Run {short_id} is closing. Monitoring will update momentarily."
        except HTTPException as exc:
            if exc.status_code >= 500:
                form_errors["__all__"] = "Failed to stop run. Please retry shortly."
            else:
                detail = exc.detail if isinstance(exc.detail, str) else "Unable to stop run."
                form_errors["__all__"] = detail

    context = _base_context(
        request,
        form_values=_default_form_values(),
        form_errors=form_errors,
        form_success=form_success,
        stop_target_run_id=stop_target_run_id,
    )

    template_name = "trading/start_panel_response.html"
    return _templates(request).TemplateResponse(template_name, context)
