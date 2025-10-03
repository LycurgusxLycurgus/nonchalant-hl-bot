"""Pydantic schemas for internal transfer operations."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


TRANSFER_KINDS = {"usdSend", "spotSend"}


class InternalTransferRequest(BaseModel):
    """Request payload for initiating an internal transfer via Hyperliquid exchange."""

    kind: Literal["usdSend", "spotSend"]
    amount: Decimal = Field(..., gt=Decimal("0"))
    destination: str = Field(..., min_length=42, max_length=42)
    asset: str | None = Field(default=None, description="Spot asset symbol when kind=spotSend")
    run_id: str = Field(..., min_length=8)

    @field_validator("destination")
    @classmethod
    def validate_destination(cls, value: str) -> str:
        if not value.startswith("0x") or len(value) != 42:
            raise ValueError("Destination must be a 42-character 0x-prefixed address")
        return value.lower()

    @field_validator("asset")
    @classmethod
    def normalize_asset(cls, value: str | None, info):
        kind = info.data.get("kind")
        if kind == "spotSend" and not value:
            raise ValueError("asset is required for spotSend")
        if value:
            return value.upper()
        return value


class InternalTransferResponse(BaseModel):
    """Response payload returned after a successful transfer."""

    kind: str
    amount: Decimal
    destination: str
    asset: str | None = None
    run_id: str
    transfer_id: str
    submitted_at: str
