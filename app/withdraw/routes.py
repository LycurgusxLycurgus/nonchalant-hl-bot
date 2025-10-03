"""Withdrawal preparation routes."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.withdraw.schemas import WithdrawPrepareRequest
from app.withdraw.service import prepare_withdrawal

router = APIRouter()


@router.post("/prepare")
async def prepare_withdraw_endpoint(request: Request, payload: WithdrawPrepareRequest) -> JSONResponse:
    wallet_address: str | None = request.session.get("wallet_address")
    instructions = await prepare_withdrawal(payload, wallet_address)
    return JSONResponse({"ok": True, "data": instructions.model_dump(mode="json")})
