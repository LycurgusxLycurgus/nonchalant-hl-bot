"""Withdrawal preparation service."""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException

from app.authz import storage as auth_storage
from app.trading import storage as trading_storage
from app.withdraw.schemas import WithdrawInstructions, WithdrawPrepareRequest

CHAIN_IDS = {
    "arbitrum": 42161,
    "ethereum": 1,
}


async def prepare_withdrawal(
    payload: WithdrawPrepareRequest,
    wallet_address: str | None,
) -> WithdrawInstructions:
    run_entry = trading_storage.get_run(payload.run_id)
    if not run_entry:
        raise HTTPException(status_code=404, detail="Run not found")

    if wallet_address and wallet_address.lower() != run_entry.get("wallet_address"):
        raise HTTPException(status_code=403, detail="Wallet mismatch for run")

    agent_address = run_entry.get("agent_address")
    if not isinstance(agent_address, str):
        raise HTTPException(status_code=500, detail="Agent wallet unavailable")

    nonce = secrets.randbits(64)

    typed_data = _build_typed_data(
        chain=payload.chain,
        destination=payload.l1_destination,
        amount=payload.amount_usd,
        agent_address=agent_address,
        nonce=nonce,
    )

    message = typed_data["message"]
    transfer_id = secrets.token_hex(16)
    submitted_at = datetime.now(tz=UTC)

    instructions = WithdrawInstructions(
        typed_data=typed_data,
        message=message,
        human_readable=_build_human_readable(payload, agent_address),
        run_id=payload.run_id,
        transfer_id=transfer_id,
        l1_destination=payload.l1_destination,
        amount_usd=payload.amount_usd,
    )

    audit_entry = {
        "id": transfer_id,
        "ts": submitted_at.timestamp(),
        "action": "withdraw_prepare",
        "run_id": payload.run_id,
        "agent_address": agent_address,
        "payload": payload.model_dump(mode="json"),
        "typed_data": typed_data,
    }
    auth_storage.append_audit(audit_entry)

    return instructions


def _build_typed_data(*, chain: str, destination: str, amount: Any, agent_address: str, nonce: int) -> dict[str, Any]:
    chain_id = CHAIN_IDS.get(chain, 42161)
    amount_str = str(amount)
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "HLWithdraw": [
                {"name": "destination", "type": "address"},
                {"name": "amount", "type": "string"},
                {"name": "nonce", "type": "uint64"},
            ],
        },
        "primaryType": "HLWithdraw",
        "domain": {
            "name": "Hyperliquid",
            "version": "1",
            "chainId": chain_id,
            "verifyingContract": agent_address,
        },
        "message": {
            "destination": destination,
            "amount": amount_str,
            "nonce": nonce,
        },
    }


def _build_human_readable(payload: WithdrawPrepareRequest, agent_address: str) -> str:
    return (
        f"Withdraw {payload.amount_usd} USD from agent {agent_address} to "
        f"{payload.l1_destination} on {payload.chain.capitalize()}"
    )
