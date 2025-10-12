"""Presentation helpers for agent vault and summary views."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable

from app.authz import storage


def _format_timestamp(value: Any) -> str:
    """Return human-readable timestamp or em dash fallback."""

    try:
        return datetime.fromtimestamp(float(value), tz=UTC).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "—"


def _short_address(address: str | None) -> str:
    if not address or len(address) < 10:
        return address or ""
    return f"{address[:6]}…{address[-4:]}"


def agent_vault_view(
    wallet_address: str | None,
    active_agent_address: str | None,
) -> tuple[str | None, list[dict[str, Any]]]:
    """Build list data for vault display, marking active agent."""

    normalized_wallet = storage.normalize_address(wallet_address)
    normalized_active = storage.normalize_address(active_agent_address)

    entries = storage.agents_for_wallet(wallet_address)

    items: list[dict[str, Any]] = []
    for entry in entries:
        agent_address = storage.normalize_address(entry.get("agent_address"))
        items.append(
            {
                "label": entry.get("label", "Unnamed"),
                "agent_address": agent_address or "",
                "agent_short": _short_address(agent_address),
                "stored_at": entry.get("stored_at"),
                "stored_display": _format_timestamp(entry.get("stored_at")),
                "is_active": agent_address == normalized_active,
            }
        )

    return normalized_wallet, items


def agent_summary_view(
    wallet_address: str | None,
    active_agent_address: str | None,
) -> dict[str, Any]:
    """Assemble compact summary for overview/start panel."""

    normalized_wallet, items = agent_vault_view(wallet_address, active_agent_address)
    normalized_active = storage.normalize_address(active_agent_address)

    active_item = next((item for item in items if item["is_active"]), None)
    fallback_item = next((item for item in items if item["agent_address"] == normalized_active), None)
    if not active_item and fallback_item:
        active_item = fallback_item

    secondary_items = [item for item in items if not item.get("is_active")]

    return {
        "wallet_address": normalized_wallet,
        "has_wallet": bool(normalized_wallet),
        "total_agents": len(items),
        "active": active_item,
        "secondary": secondary_items,
        "items": items,
    }
