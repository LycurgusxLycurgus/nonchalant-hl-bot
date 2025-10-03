"""Pydantic schemas for trading flows."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


VALID_LEVERAGE_RANGE = range(1, 51)


class BotStartRequest(BaseModel):
    """Input payload for starting a trading bot run."""

    market: str = Field(..., min_length=3, max_length=32)
    usd_notional: Decimal = Field(..., gt=Decimal("0"))
    leverage: int = Field(...)
    duration_minutes: float = Field(default=15.0, gt=0.0, le=240.0)

    @field_validator("market")
    @classmethod
    def normalize_market(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("leverage")
    @classmethod
    def validate_leverage(cls, value: int) -> int:
        if value not in VALID_LEVERAGE_RANGE:
            raise ValueError("Leverage must be between 1 and 50")
        return value


class BotRunRecord(BaseModel):
    """Represents the state of an active or completed bot run."""

    run_id: str
    market: str
    usd_notional: Decimal
    leverage: int
    wallet_address: str
    agent_address: str
    status: Literal["starting", "running", "completed", "cancelled", "failed", "closed"]
    started_at: datetime
    duration_minutes: float


class BotStartResponse(BaseModel):
    """Success response for bot start endpoint."""

    run_id: str
    status: str
    market: str
    usd_notional: Decimal
    leverage: int
    started_at: datetime

    @classmethod
    def from_record(cls, record: BotRunRecord) -> "BotStartResponse":
        return cls(
            run_id=record.run_id,
            status=record.status,
            market=record.market,
            usd_notional=record.usd_notional,
            leverage=record.leverage,
            started_at=record.started_at,
        )


class BotStopRequest(BaseModel):
    """Payload for stopping an active bot run."""

    run_id: str = Field(..., min_length=8)


class BotStopResponse(BaseModel):
    """Success response for stop endpoint."""

    run_id: str
    status: str
    market: str
    closed_at: datetime

    @classmethod
    def from_record(cls, *, run_id: str, market: str, status: str, closed_at: datetime) -> "BotStopResponse":
        return cls(run_id=run_id, market=market, status=status, closed_at=closed_at)
