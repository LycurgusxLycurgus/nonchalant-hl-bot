"""Deposit flow routes for instructions and balance polling."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.lib.info_client import InfoClient, InfoClientError

router = APIRouter()


def get_info_client() -> InfoClient:
    settings = get_settings()
    return InfoClient(settings.hl_rest_base)


def get_templates(request: Request) -> Jinja2Templates:
    templates: Jinja2Templates = request.app.state.templates  # type: ignore[attr-defined]
    return templates


def _extract_usd_balance(payload: dict[str, Any]) -> Decimal:
    """Extract USDC balance from spot clearinghouse state response."""

    spot_state = payload.get("spotState") or {}
    balances = spot_state.get("balances") or []
    for balance in balances:
        coin = balance.get("coin")
        if isinstance(coin, str) and coin.upper() in {"USDC", "USD"}:
            value = balance.get("total") or balance.get("available") or balance.get("amount") or 0
            try:
                return Decimal(str(value))
            except (ValueError, ArithmeticError, InvalidOperation):
                return Decimal("0")
    return Decimal("0")


@router.get("/", response_class=HTMLResponse)
async def deposit_instructions(request: Request) -> HTMLResponse:
    """Render the deposit instructions page with balance polling panel."""

    settings = get_settings()
    context = {
        "request": request,
        "page_title": "Deposit funds",
        "current_year": datetime.now(tz=UTC).year,
        "walletconnect_project_id": settings.walletconnect_project_id,
        "hl_env": settings.hl_env,
        "nav_active": "deposit",
        **build_wallet_context(request),
    }
    return get_templates(request).TemplateResponse("deposit/instructions.html", context)


def build_wallet_context(request: Request) -> dict[str, Any]:
    builder: Callable[[dict[str, Any]], dict[str, Any]] | None = getattr(
        request.app.state, "wallet_context_builder", None
    )  # type: ignore[attr-defined]
    if callable(builder):
        return builder(request.session)
    return {"wallet_address": None, "wallet_address_short": None}


@router.get("/partial/balance", response_class=HTMLResponse)
async def balance_partial(
    request: Request,
    info_client: InfoClient = Depends(get_info_client),
) -> HTMLResponse:
    """Return the balance panel HTML for htmx swaps."""

    session = request.session
    wallet_address: str | None = session.get("wallet_address")
    balance_value: Decimal | None = None
    balance_display: str | None = None
    error_message: str | None = None

    if wallet_address:
        try:
            payload = await info_client.fetch_balances(wallet_address)
            balance_value = _extract_usd_balance(payload)
            balance_display = f"{balance_value.quantize(Decimal('0.01')):,.2f}"
        except InfoClientError:
            error_message = "Unable to reach Hyperliquid Info endpoint. Please retry."
    else:
        error_message = "Connect your Hyperliquid account wallet to see balances."

    context = {
        "wallet_address": wallet_address,
        "balance_value": balance_value,
        "balance_display": balance_display,
        "error_message": error_message,
    }
    return get_templates(request).TemplateResponse("deposit/_balance_panel.html", {"request": request, **context})


@router.get("/api/balance")
async def balance_api(
    request: Request,
    info_client: InfoClient = Depends(get_info_client),
) -> JSONResponse:
    """JSON API for wallet balances (USDC)."""

    wallet_address: str | None = request.session.get("wallet_address")
    if not wallet_address:
        raise HTTPException(status_code=400, detail="Wallet not connected")

    try:
        payload = await info_client.fetch_balances(wallet_address)
    except InfoClientError as exc:
        raise HTTPException(status_code=502, detail="Hyperliquid Info unavailable") from exc

    balance_value = _extract_usd_balance(payload)
    quantized = balance_value.quantize(Decimal("0.01"))
    response = {
        "ok": True,
        "data": {
            "wallet_address": wallet_address,
            "balance": f"{quantized:,.2f}",
        },
    }
    return JSONResponse(response)
