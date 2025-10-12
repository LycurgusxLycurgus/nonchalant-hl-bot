"""FastAPI application entrypoint for the Hyperliquid bot skeleton (Phases 0-1)."""

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.paths import STATIC_DIR, TEMPLATES_DIR
from app.authz.routes import router as authz_router
from app.config import get_settings
from app.deposit.routes import router as deposit_router
from app.trading.routes import router as trading_router
from app.trading.service import get_start_overview
from app.trading.ui import router as trading_ui_router
from app.trading.constants import (
    DEFAULT_MARKETS,
    DEFAULT_DURATION_MINUTES,
    DEFAULT_LEVERAGE,
    DEFAULT_NOTIONAL,
)
from app.monitoring import MonitoringHub, router as monitoring_router, MonitoringService
from app.transfers.routes import router as transfers_router
from app.withdraw.routes import router as withdraw_router
from app.history.routes import router as history_router
from app.lib.logger import configure_logging
from app.lib.metrics import METRICS
from app.lib.rate_limiter import RateLimiter

settings = get_settings()

configure_logging()
app = FastAPI(title="Hyperliquid Bot", version="0.3.0")
app.add_middleware(
    SessionMiddleware,
    secret_key=settings.secret_key_salt,
    same_site="lax",
    https_only=settings.hl_env == "prod",
)

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

app.state.templates = templates
app.state.monitoring_hub = MonitoringHub()
app.state.monitoring_service = MonitoringService(app.state.monitoring_hub)
app.state.metrics = METRICS
app.state.rate_limiter = RateLimiter()
app.state.rate_limit_per_minute = settings.request_rate_limit_per_minute

app.include_router(authz_router, prefix="/authz", tags=["authz"])
app.include_router(deposit_router, prefix="/deposit", tags=["deposit"])
app.include_router(trading_router, prefix="/api/bot", tags=["bot"])
app.include_router(trading_ui_router, prefix="/bot", tags=["bot-ui"])
app.include_router(monitoring_router, prefix="/monitoring", tags=["monitoring"])
app.include_router(transfers_router, prefix="/api/internal", tags=["internal"])
app.include_router(withdraw_router, prefix="/api/withdraw", tags=["withdraw"])
app.include_router(history_router, prefix="/history", tags=["history"])


@app.get("/health", tags=["system"], summary="Health check")
async def health_check() -> JSONResponse:
    """Return liveness response for uptime monitoring."""
    payload = {"ok": True, "data": {"status": "healthy"}}
    return JSONResponse(content=payload)


@app.get("/metrics", tags=["system"], summary="Metrics endpoint")
async def metrics_endpoint() -> JSONResponse:
    snapshot = METRICS.snapshot()
    return JSONResponse({"ok": True, "data": snapshot})


@app.get(
    "/.well-known/appspecific/com.chrome.devtools.json",
    include_in_schema=False,
)
async def chrome_devtools_probe() -> Response:
    """Return empty response for Chrome DevTools probe to avoid noisy 404 logs."""

    return Response(status_code=204)


@app.get("/favicon.ico", include_in_schema=False)
async def favicon() -> FileResponse:
    """Serve the Hyperliquid Bot favicon."""

    return FileResponse(STATIC_DIR / "favicon.svg", media_type="image/svg+xml")


def _wallet_context(session: dict[str, Any]) -> dict[str, Any]:
    """Assemble wallet-related context for templates."""

    wallet_address: str | None = session.get("wallet_address")
    if not wallet_address:
        return {"wallet_address": None, "wallet_address_short": None}

    short = f"{wallet_address[:6]}…{wallet_address[-4:]}"
    return {"wallet_address": wallet_address, "wallet_address_short": short}


app.state.wallet_context_builder = _wallet_context

@app.get("/", tags=["ui"], summary="Root landing page")
async def root(request: Request):
    """Render landing page including wallet connect state."""

    start_overview = get_start_overview()
    form_defaults = {
        "market": DEFAULT_MARKETS[0],
        "usd_notional": str(DEFAULT_NOTIONAL),
        "leverage": str(DEFAULT_LEVERAGE),
        "duration_minutes": str(DEFAULT_DURATION_MINUTES),
    }

    context = {
        "request": request,
        "page_title": "Hyperliquid Bot",
        "current_year": datetime.now(tz=UTC).year,
        "walletconnect_project_id": settings.walletconnect_project_id,
        "hl_env": settings.hl_env,
        "nav_active": "overview",
        "markets": DEFAULT_MARKETS,
        "default_notional": DEFAULT_NOTIONAL,
        "default_leverage": DEFAULT_LEVERAGE,
        "default_duration_minutes": DEFAULT_DURATION_MINUTES,
        "start_overview": start_overview,
        "form_values": form_defaults,
        "form_errors": {},
        "form_success": None,
        **_wallet_context(request.session),
    }
    return templates.TemplateResponse(request, "index.html", context)


@app.get("/authz/agent", tags=["ui"], summary="Agent authorization instructions")
async def agent_authorization(request: Request):
    """Render the agent authorization instructions page."""

    context = {
        "page_title": "Authorize agent wallet",
        "current_year": datetime.now(tz=UTC).year,
        "walletconnect_project_id": settings.walletconnect_project_id,
        "hl_env": settings.hl_env,
        "nav_active": "agent",
        "agent_deep_link": "https://app.hyperliquid.xyz/API",
        **_wallet_context(request.session),
    }
    return templates.TemplateResponse(request, "authz/agent.html", context)
