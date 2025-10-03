"""Routes for history and audit trail."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.history.service import load_history

router = APIRouter()


def _get_templates(request: Request) -> Jinja2Templates:
    return request.app.state.templates  # type: ignore[attr-defined]


def _wallet_context(request: Request) -> dict[str, object]:
    builder = getattr(request.app.state, "wallet_context_builder", None)  # type: ignore[attr-defined]
    if callable(builder):
        return builder(request.session)
    return {"wallet_address": None, "wallet_address_short": None}


@router.get("/", response_class=HTMLResponse, name="history_page")
async def history_page(
    request: Request,
    run_id: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> HTMLResponse:
    history = load_history(offset=offset, limit=limit, run_id=run_id)
    context = {
        "request": request,
        "page_title": "History & Audit Trail",
        "nav_active": "history",
        "history": history.items,
        "total": history.total,
        "limit": history.limit,
        "offset": history.offset,
        "run_id": run_id,
        "current_year": datetime.now(tz=UTC).year,
        **_wallet_context(request),
    }
    return _get_templates(request).TemplateResponse("history/index.html", context)


@router.get("/api", response_class=JSONResponse, name="history_api")
async def history_api(
    run_id: str | None = Query(default=None),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=100),
) -> JSONResponse:
    history = load_history(offset=offset, limit=limit, run_id=run_id)
    return JSONResponse({"ok": True, "data": history.model_dump(mode="json")})
