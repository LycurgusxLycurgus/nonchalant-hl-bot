"""FastAPI application entrypoint for the Hyperliquid bot skeleton (Phases 0-1)."""

from datetime import UTC, datetime
from typing import Any

from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.paths import STATIC_DIR, TEMPLATES_DIR
from app.authz.routes import router as authz_router
from app.config import get_settings
from app.deposit.routes import router as deposit_router
from app.trading.routes import router as trading_router
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

    context = {
        "page_title": "Hyperliquid Bot",
        "current_year": datetime.now(tz=UTC).year,
        "walletconnect_project_id": settings.walletconnect_project_id,
        "hl_env": settings.hl_env,
        "nav_active": "overview",
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
