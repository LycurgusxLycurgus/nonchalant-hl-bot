"""Pydantic schemas for monitoring snapshots and responses."""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, Field, field_validator


class BotPnLSnapshot(BaseModel):
    """Represents a realtime snapshot of a bot run's trading state."""

    run_id: str = Field(..., min_length=1)
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))
    market: str = Field(..., min_length=1)
    status: Literal["running", "completed", "stopped"] = "running"
    position_notional: Decimal = Field(default=Decimal("0"))
    entry_price: Decimal = Field(default=Decimal("0"))
    mark_price: Decimal = Field(default=Decimal("0"))
    realized_pnl: Decimal = Field(default=Decimal("0"))
    unrealized_pnl: Decimal = Field(default=Decimal("0"))

    @field_validator("market")
    @classmethod
    def normalize_market(cls, value: str) -> str:
        return value.upper()

    @property
    def total_pnl(self) -> Decimal:
        return self.realized_pnl + self.unrealized_pnl

    def json_payload(self) -> dict[str, object]:
        """Return JSON-serializable payload."""

        return self.model_dump(mode="json") | {"total_pnl": str(self.total_pnl)}


class MonitoringEnvelope(BaseModel):
    """Envelope for responses from monitoring endpoints."""

    ok: bool = True
    data: BotPnLSnapshot

    @classmethod
    def wrap(cls, snapshot: BotPnLSnapshot) -> "MonitoringEnvelope":
        return cls(ok=True, data=snapshot)
