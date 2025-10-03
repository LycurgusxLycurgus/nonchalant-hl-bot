"""Authorization routes for wallet session and agent registry management."""

from __future__ import annotations

import secrets
import re
import time
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.authz import storage
from app.paths import TEMPLATES_DIR

templates = Jinja2Templates(directory=str(TEMPLATES_DIR))
router = APIRouter()


_WALLET_ADDRESS_RE = re.compile(r"^0x[a-fA-F0-9]{40}$")
_AGENT_ADDRESS_RE = _WALLET_ADDRESS_RE


class WalletSessionPayload(BaseModel):
    """Schema for persisting a wallet address in the session store."""

    address: str = Field(..., description="0x-prefixed EVM wallet address")

    @field_validator("address")
    @classmethod
    def validate_evm_address(cls, value: str) -> str:
        """Ensure the value is a 0x-prefixed 40 byte hexadecimal string."""

        if not _WALLET_ADDRESS_RE.fullmatch(value or ""):
            raise ValueError("Must be a 0x-prefixed hexadecimal address (40 bytes).")
        return value.lower()


class WalletSessionResponse(BaseModel):
    """Response envelope data for wallet session endpoints."""

    address: str | None


class AgentRegistrationPayload(BaseModel):
    """Payload for registering an agent wallet and encrypted secret."""

    label: str = Field(..., min_length=1, max_length=64)
    agent_address: str = Field(..., description="0x-prefixed agent wallet address")
    private_key: str = Field(..., description="Hex-encoded agent private key")

    @field_validator("agent_address")
    @classmethod
    def validate_agent_address(cls, value: str) -> str:
        if not _AGENT_ADDRESS_RE.fullmatch(value or ""):
            raise ValueError("Agent address must be 0x-prefixed (40 bytes).")
        return value.lower()

    @field_validator("private_key")
    @classmethod
    def validate_private_key(cls, value: str) -> str:
        key = value.lower()
        if not key.startswith("0x") or len(key) != 66:
            raise ValueError("Private key must be 32-byte hex string with 0x prefix.")
        return key


class AgentRegistrationResponse(BaseModel):
    """Response data for agent registration."""

    agent_address: str
    label: str
    stored_at: float


class AgentAlreadyRegisteredError(ValueError):
    """Raised when attempting to register a duplicate agent address."""


def _success(data: dict[str, Any]) -> dict[str, Any]:
    """Return canonical success envelope."""

    return {"ok": True, "data": data}


@router.get("/session")
async def read_wallet_session(request: Request) -> dict[str, Any]:
    """Return the active wallet address (if any) stored in the session."""

    address = request.session.get("wallet_address")
    return _success(WalletSessionResponse(address=address).model_dump())


@router.post("/session")
async def upsert_wallet_session(
    payload: WalletSessionPayload, request: Request
) -> dict[str, Any]:
    """Persist the provided wallet address into the session."""

    address = payload.address
    request.session["wallet_address"] = address
    return _success(WalletSessionResponse(address=address).model_dump())


@router.delete("/session")
async def clear_wallet_session(request: Request) -> dict[str, Any]:
    """Remove any stored wallet address from the session."""

    request.session.pop("wallet_address", None)
    return _success(WalletSessionResponse(address=None).model_dump())


def _register_agent(payload: AgentRegistrationPayload) -> AgentRegistrationResponse:
    entries = storage.load_agents()

    if any(entry["agent_address"] == payload.agent_address for entry in entries):
        raise AgentAlreadyRegisteredError("Agent already registered")

    cipher = storage.get_fernet().encrypt(payload.private_key.encode("utf-8")).decode("utf-8")
    stored_at = time.time()
    registry_entry = {
        "agent_address": payload.agent_address,
        "label": payload.label,
        "stored_at": stored_at,
        "key_cipher": cipher,
    }

    entries.append(registry_entry)
    storage.write_agents(entries)

    audit_entry = {
        "id": secrets.token_hex(16),
        "ts": time.time(),
        "action": "agent_registered",
        "agent_address": payload.agent_address,
        "label": payload.label,
    }
    storage.append_audit(audit_entry)

    return AgentRegistrationResponse(
        agent_address=payload.agent_address,
        label=payload.label,
        stored_at=stored_at,
    )


@router.post("/agent", response_class=HTMLResponse)
async def register_agent_form(
    request: Request,
    label: str = Form(...),
    agent_address: str = Form(...),
    private_key: str = Form(...),
) -> HTMLResponse:
    try:
        payload = AgentRegistrationPayload(label=label, agent_address=agent_address, private_key=private_key)
        response = _register_agent(payload)
    except ValidationError as exc:
        message = ", ".join(err["msg"] for err in exc.errors())
        context = {"status": "error", "message": message or "Invalid agent details provided"}
    except AgentAlreadyRegisteredError as exc:
        context = {"status": "error", "message": str(exc)}
    else:
        context = {
            "status": "success",
            "message": f"Agent {response.label} stored for {response.agent_address[:6]}…{response.agent_address[-4:]}",
        }

    return templates.TemplateResponse("authz/agent_status.html", {"request": request, **context})
