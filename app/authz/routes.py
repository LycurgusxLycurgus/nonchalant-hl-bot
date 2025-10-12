"""Authorization routes for wallet session and agent registry management."""

from __future__ import annotations

import secrets
import re
import time
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, HTTPException, Request, Form, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field, ValidationError, field_validator

from app.authz import storage
from app.paths import TEMPLATES_DIR
from app.authz.view_models import agent_summary_view, agent_vault_view

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

    # owner_wallet supplied by handler, not exposed to clients directly
    owner_wallet: str | None = Field(default=None, exclude=True)

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
    owner_wallet: str


class AgentListItem(BaseModel):
    """Serialized agent entry for UI/API consumption."""

    label: str
    agent_address: str
    stored_at: float
    owner_wallet: str


class AgentAlreadyRegisteredError(ValueError):
    """Raised when attempting to register a duplicate agent address."""


def _success(data: dict[str, Any]) -> dict[str, Any]:
    """Return canonical success envelope."""

    return {"ok": True, "data": data}


def _agent_vault_context(request: Request) -> dict[str, Any]:
    wallet = request.session.get("wallet_address")
    normalized_wallet, items = agent_vault_view(wallet, request.session.get("active_agent_address"))

    return {
        "request": request,
        "wallet_address": normalized_wallet,
        "agents": items,
        "active_agent": storage.normalize_address(request.session.get("active_agent_address")),
    }


def _agent_vault_fragment(request: Request, *, trigger_refresh: bool = False) -> HTMLResponse:
    response = templates.TemplateResponse("authz/_agent_list.html", _agent_vault_context(request))
    if trigger_refresh:
        response.headers["HX-Trigger"] = "agent:refresh"
    return response


def _append_agent_audit(action: str, agent_address: str, wallet: str) -> None:
    audit_entry = {
        "id": secrets.token_hex(16),
        "ts": time.time(),
        "action": action,
        "agent_address": agent_address,
        "wallet_address": wallet,
    }
    storage.append_audit(audit_entry)


def _prune_agent(agent_address: str, wallet: str, request: Request) -> str:
    normalized_target = storage.normalize_address(agent_address) or agent_address

    removed = storage.delete_agent(normalized_target, wallet)
    if not removed:
        raise HTTPException(status_code=404, detail="Agent not found")

    if storage.normalize_address(request.session.get("active_agent_address")) == normalized_target:
        request.session.pop("active_agent_address", None)

    _append_agent_audit("agent_deleted", normalized_target, wallet)
    return normalized_target


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
    request.session.pop("active_agent_address", None)
    return _success(WalletSessionResponse(address=address).model_dump())


@router.delete("/session")
async def clear_wallet_session(request: Request) -> dict[str, Any]:
    """Remove any stored wallet address from the session."""

    request.session.pop("wallet_address", None)
    request.session.pop("active_agent_address", None)
    return _success(WalletSessionResponse(address=None).model_dump())


def _register_agent(payload: AgentRegistrationPayload) -> AgentRegistrationResponse:
    entries = storage.load_agents()

    if any(entry["agent_address"] == payload.agent_address for entry in entries):
        raise AgentAlreadyRegisteredError("Agent already registered")

    cipher = storage.get_fernet().encrypt(payload.private_key.encode("utf-8")).decode("utf-8")
    stored_at = time.time()
    owner_wallet = storage.normalize_address(payload.owner_wallet)
    if not owner_wallet:
        raise HTTPException(status_code=400, detail="Wallet not connected")
    registry_entry = {
        "agent_address": payload.agent_address,
        "label": payload.label,
        "stored_at": stored_at,
        "key_cipher": cipher,
        "owner_wallet": owner_wallet,
    }

    entries.append(registry_entry)
    storage.write_agents(entries)

    audit_entry = {
        "id": secrets.token_hex(16),
        "ts": time.time(),
        "action": "agent_registered",
        "agent_address": payload.agent_address,
        "wallet_address": owner_wallet,
    }
    storage.append_audit(audit_entry)

    return AgentRegistrationResponse(
        agent_address=payload.agent_address,
        label=payload.label,
        stored_at=stored_at,
        owner_wallet=owner_wallet,
    )


@router.post("/agent")
async def register_agent(request: Request) -> Response:
    session_wallet: str | None = request.session.get("wallet_address")
    content_type = request.headers.get("content-type", "")
    expects_json = "application/json" in content_type

    def _set_active(agent_addr: str) -> None:
        normalized = storage.normalize_address(agent_addr)
        if normalized:
            request.session["active_agent_address"] = normalized

    if expects_json:
        data = await request.json()
        try:
            payload = AgentRegistrationPayload(**data, owner_wallet=session_wallet)
            response = _register_agent(payload)
        except ValidationError as exc:
            raise HTTPException(status_code=422, detail=exc.errors()) from exc
        except AgentAlreadyRegisteredError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        _set_active(response.agent_address)
        summary = agent_summary_view(session_wallet, request.session.get("active_agent_address"))
        body = response.model_dump()
        body["summary"] = summary
        return JSONResponse(_success(body), headers={"HX-Trigger": "agent:refresh"})

    form = await request.form()
    try:
        payload = AgentRegistrationPayload(
            label=str(form.get("label", "")),
            agent_address=str(form.get("agent_address", "")),
            private_key=str(form.get("private_key", "")),
            owner_wallet=session_wallet,
        )
        response = _register_agent(payload)
    except ValidationError as exc:
        message = ", ".join(err["msg"] for err in exc.errors())
        context = {"status": "error", "message": message or "Invalid agent details provided"}
    except AgentAlreadyRegisteredError as exc:
        context = {"status": "error", "message": str(exc)}
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else "Wallet must be connected."
        context = {"status": "error", "message": detail}
    else:
        _set_active(response.agent_address)
        context = {
            "status": "success",
            "message": f"Agent {response.label} stored for {response.agent_address[:6]}…{response.agent_address[-4:]}",
        }

    template_response = templates.TemplateResponse(
        "authz/agent_status.html",
        {
            "request": request,
            **context,
            "agent_summary": agent_summary_view(session_wallet, request.session.get("active_agent_address")),
        },
    )
    if context.get("status") == "success":
        template_response.headers["HX-Trigger"] = "agent:refresh"
    return template_response


@router.get("/agents", response_class=JSONResponse)
async def list_agents(request: Request) -> JSONResponse:
    wallet = request.session.get("wallet_address")
    _, agents = agent_vault_view(wallet, request.session.get("active_agent_address"))
    serialized = [
        AgentListItem(
            label=item["label"],
            agent_address=item["agent_address"],
            stored_at=item["stored_at"],
            owner_wallet=wallet or "",
        ).model_dump()
        for item in agents
    ]
    return JSONResponse(_success({"agents": serialized}))


class AgentDeletePayload(BaseModel):
    agent_address: str

    @field_validator("agent_address")
    @classmethod
    def validate_agent_address(cls, value: str) -> str:
        if not _AGENT_ADDRESS_RE.fullmatch(value or ""):
            raise ValueError("Agent address must be 0x-prefixed (40 bytes).")
        return value.lower()


@router.delete("/agent", response_class=JSONResponse)
async def delete_agent_api(payload: AgentDeletePayload, request: Request) -> JSONResponse:
    wallet = request.session.get("wallet_address")
    if not wallet:
        raise HTTPException(status_code=400, detail="Wallet not connected")

    normalized = _prune_agent(payload.agent_address, wallet, request)
    return JSONResponse(_success({"agent_address": normalized}), headers={"HX-Trigger": "agent:refresh"})


@router.post("/agent/delete")
async def delete_agent(request: Request) -> HTMLResponse:
    wallet = request.session.get("wallet_address")
    if not wallet:
        raise HTTPException(status_code=400, detail="Wallet not connected")

    form = await request.form()
    try:
        payload = AgentDeletePayload(agent_address=str(form.get("agent_address", "")))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    _prune_agent(payload.agent_address, wallet, request)
    return _agent_vault_fragment(request, trigger_refresh=True)


@router.get("/agent/list", response_class=HTMLResponse)
async def agent_list_partial(request: Request) -> HTMLResponse:
    return _agent_vault_fragment(request)


class AgentSelectPayload(BaseModel):
    agent_address: str

    @field_validator("agent_address")
    @classmethod
    def validate_agent_address(cls, value: str) -> str:
        if not _AGENT_ADDRESS_RE.fullmatch(value or ""):
            raise ValueError("Agent address must be 0x-prefixed (40 bytes).")
        return value.lower()


@router.post("/agent/select")
async def select_active_agent(request: Request) -> HTMLResponse:
    wallet = request.session.get("wallet_address")
    if not wallet:
        raise HTTPException(status_code=400, detail="Wallet not connected")

    form = await request.form()
    try:
        payload = AgentSelectPayload(agent_address=str(form.get("agent_address", "")))
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=exc.errors()) from exc

    agents = storage.agents_for_wallet(wallet)
    normalized = storage.normalize_address(payload.agent_address)
    if not any(storage.normalize_address(agent["agent_address"]) == normalized for agent in agents):
        raise HTTPException(status_code=404, detail="Agent not found")

    request.session["active_agent_address"] = normalized
    return _agent_vault_fragment(request, trigger_refresh=True)
