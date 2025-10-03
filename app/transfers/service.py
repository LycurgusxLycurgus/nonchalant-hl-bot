"""Internal transfer business logic."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from app.authz import storage as auth_storage
from app.config import get_settings
from app.lib.hyperliquid_adapter import ExchangeCredentials, HyperliquidExchangeClient
from app.trading import storage as trading_storage
from app.transfers.schemas import InternalTransferRequest, InternalTransferResponse


async def submit_internal_transfer(
    payload: InternalTransferRequest,
    wallet_address: str | None,
) -> InternalTransferResponse:
    """Execute an internal transfer (usdSend/spotSend) via Hyperliquid exchange."""

    run_entry = trading_storage.get_run(payload.run_id)
    if not run_entry:
        raise HTTPException(status_code=404, detail="Run not found")

    if wallet_address and run_entry.get("wallet_address") != wallet_address:
        raise HTTPException(status_code=403, detail="Wallet mismatch for run")

    agent_address = run_entry.get("agent_address")
    if not isinstance(agent_address, str):
        raise HTTPException(status_code=500, detail="Run entry missing agent wallet")

    agents = auth_storage.load_agents()
    agent_entry = next((entry for entry in agents if entry.get("agent_address") == agent_address), None)
    if not agent_entry:
        raise HTTPException(status_code=400, detail="Agent wallet unavailable")

    private_key = _decrypt_private_key(agent_entry)

    settings = get_settings()
    client = HyperliquidExchangeClient(
        ExchangeCredentials(agent_address, private_key),
        base_url=settings.hl_rest_base,
    )

    amount = float(payload.amount)
    response_data: dict[str, Any]
    try:
        if payload.kind == "usdSend":
            response_data = await client.usd_send(payload.destination, amount)
        else:
            asset = payload.asset or ""
            response_data = await client.spot_send(asset, payload.destination, amount)
    finally:
        await client.close()

    transfer_id = secrets.token_hex(16)
    submitted_at = datetime.now(tz=UTC)
    result = InternalTransferResponse(
        kind=payload.kind,
        amount=payload.amount,
        destination=payload.destination,
        asset=payload.asset,
        run_id=payload.run_id,
        transfer_id=transfer_id,
        submitted_at=submitted_at.isoformat(),
    )

    audit_entry = {
        "id": transfer_id,
        "ts": submitted_at.timestamp(),
        "action": "internal_transfer",
        "kind": payload.kind,
        "run_id": payload.run_id,
        "destination": payload.destination,
        "amount": str(payload.amount),
        "asset": payload.asset,
        "response": response_data,
    }
    auth_storage.append_audit(audit_entry)

    return result


def _decrypt_private_key(agent_entry: dict[str, Any]) -> str:
    cipher = agent_entry.get("key_cipher")
    if not cipher:
        raise HTTPException(status_code=400, detail="Agent entry missing key cipher")
    fernet = auth_storage.get_fernet()
    try:
        return fernet.decrypt(cipher.encode("utf-8")).decode("utf-8")
    except Exception as exc:  # pragma: no cover - defensive
        raise HTTPException(status_code=500, detail="Unable to decrypt agent key") from exc
