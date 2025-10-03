"""API routes for internal transfers."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.transfers.schemas import InternalTransferRequest
from app.transfers.service import submit_internal_transfer

router = APIRouter()


@router.post("/transfer")
async def create_internal_transfer(request: Request, payload: InternalTransferRequest) -> JSONResponse:
    """Execute an internal transfer through the Hyperliquid exchange adapter."""

    wallet_address: str | None = request.session.get("wallet_address")
    result = await submit_internal_transfer(payload, wallet_address)
    return JSONResponse({"ok": True, "data": result.model_dump(mode="json")})
