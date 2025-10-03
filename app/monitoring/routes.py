"""Monitoring routes providing SSE streams, REST access, and UI dashboard."""

from __future__ import annotations

from typing import AsyncIterator, Any
import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.templating import Jinja2Templates

from app.monitoring.hub import MonitoringHub
from app.monitoring.service import MonitoringService
from app.monitoring.schemas import MonitoringEnvelope

router = APIRouter()


async def get_monitoring_hub(request: Request) -> MonitoringHub:
    hub: MonitoringHub | None = getattr(request.app.state, "monitoring_hub", None)
    if hub is None:
        raise RuntimeError("Monitoring hub not configured on application state")
    return hub


def get_monitoring_service(request: Request, hub: MonitoringHub = Depends(get_monitoring_hub)) -> MonitoringService:
    service: MonitoringService | None = getattr(request.app.state, "monitoring_service", None)
    if service is None:
        service = MonitoringService(hub)
        request.app.state.monitoring_service = service
    return service


@router.get("/runs/{run_id}/snapshot")
async def get_run_snapshot(run_id: str, service: MonitoringService = Depends(get_monitoring_service)) -> JSONResponse:
    snapshot = await service.get_snapshot(run_id)
    if snapshot is None:
        raise HTTPException(status_code=404, detail="Run snapshot not found")
    # Ensure JSON-serializable payload
    return JSONResponse({"ok": True, "data": snapshot.json_payload()})


@router.get("/runs/{run_id}/stream")
async def stream_run(run_id: str, hub: MonitoringHub = Depends(get_monitoring_hub)) -> StreamingResponse:
    async def event_source() -> AsyncIterator[bytes]:
        async for snapshot in hub.listen(run_id):
            payload = {"ok": True, "data": snapshot.json_payload()}
            yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")

    return StreamingResponse(event_source(), media_type="text/event-stream")


@router.get("/stream")
async def stream_all(hub: MonitoringHub = Depends(get_monitoring_hub)) -> StreamingResponse:
    async def event_source() -> AsyncIterator[bytes]:
        async for snapshot in hub.listen():
            payload = {"ok": True, "data": snapshot.json_payload()}
            yield f"data: {json.dumps(payload)}\n\n".encode("utf-8")

    return StreamingResponse(event_source(), media_type="text/event-stream")


# -------- UI routes ---------

def _templates(request: Request) -> Jinja2Templates:
    templates: Jinja2Templates = request.app.state.templates  # type: ignore[attr-defined]
    return templates


@router.get("/", response_class=HTMLResponse)
async def monitoring_dashboard(request: Request, hub: MonitoringHub = Depends(get_monitoring_hub)) -> HTMLResponse:
    """Render realtime monitoring dashboard with SSE-powered updates."""

    snapshots = await hub.list_snapshots()
    # Wallet context (optional)
    builder = getattr(request.app.state, "wallet_context_builder", None)  # type: ignore[attr-defined]
    wallet_ctx = builder(request.session) if callable(builder) else {"wallet_address": None, "wallet_address_short": None}

    context: dict[str, Any] = {
        "request": request,
        "page_title": "Realtime Monitor",
        "nav_active": "monitoring",
        "current_year": datetime.now(tz=UTC).year,
        "snapshots": snapshots,
        **wallet_ctx,
    }
    return _templates(request).TemplateResponse("monitoring/dashboard.html", context)


@router.get("/partial/table", response_class=HTMLResponse)
async def monitoring_table_partial(request: Request, hub: MonitoringHub = Depends(get_monitoring_hub)) -> HTMLResponse:
    """Return the monitoring table rows partial (htmx friendly)."""

    snapshots = await hub.list_snapshots()
    context = {
        "request": request,
        "snapshots": snapshots,
    }
    return _templates(request).TemplateResponse("monitoring/_rows.html", context)
