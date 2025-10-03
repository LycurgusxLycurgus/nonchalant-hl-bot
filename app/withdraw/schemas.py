"""Schemas for withdrawal preparation."""

from __future__ import annotations

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class WithdrawPrepareRequest(BaseModel):
    """Payload from UI to prepare a user-signed withdrawal."""

    run_id: str = Field(..., min_length=8)
    amount_usd: Decimal = Field(..., gt=Decimal("0"))
    l1_destination: str = Field(..., min_length=42, max_length=42)
    chain: Literal["arbitrum", "ethereum"] = Field(default="arbitrum")

    @field_validator("l1_destination")
    @classmethod
    def normalize_destination(cls, value: str) -> str:
        if not value.startswith("0x") or len(value) != 42:
            raise ValueError("Destination must be a 42-character 0x-prefixed address")
        return value.lower()


class WithdrawInstructions(BaseModel):
    """Typed data instructions returned to the client for signing."""

    typed_data: dict
    message: dict
    human_readable: str
    run_id: str
    transfer_id: str
    l1_destination: str
    amount_usd: Decimal
