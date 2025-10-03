"""Schemas for audit history responses."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class HistoryEvent(BaseModel):
    """Represents a single audit event entry."""

    id: str
    action: str
    run_id: str | None = None
    ts: float
    occurred_at: datetime = Field(..., description="UTC timestamp for rendering")
    payload: dict[str, Any] = Field(default_factory=dict)
    explorer_url: str | None = None
    run_status: str | None = None


class HistoryResponse(BaseModel):
    """Paginated history response payload."""

    items: list[HistoryEvent]
    total: int
    offset: int
    limit: int
