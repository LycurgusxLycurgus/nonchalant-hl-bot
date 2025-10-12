"""Storage helpers for agent metadata and audit logs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet

from app.config import get_settings


def _settings():
    return get_settings()


def storage_dir() -> Path:
    settings = _settings()
    return settings.storage_dir


def agents_path() -> Path:
    return storage_dir() / "agents.json"


def audit_log_path() -> Path:
    return storage_dir() / "audit_log.jsonl"


def _ensure_storage_dir() -> None:
    storage_dir().mkdir(parents=True, exist_ok=True)


def load_agents() -> list[dict[str, Any]]:
    """Return list of stored agent metadata records."""

    path = agents_path()
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def write_agents(entries: list[dict[str, Any]]) -> None:
    """Persist full list of agent records."""

    _ensure_storage_dir()
    agents_path().write_text(json.dumps(entries, indent=2), encoding="utf-8")


def normalize_address(value: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    lowered = value.strip().lower()
    return lowered if lowered.startswith("0x") and len(lowered) == 42 else None


def agents_for_wallet(owner_wallet: str | None) -> list[dict[str, Any]]:
    """Return agent entries owned by the provided wallet (auto-claim legacy records)."""

    entries = load_agents()
    if not owner_wallet:
        return []

    owner_wallet_normalized = normalize_address(owner_wallet)
    if not owner_wallet_normalized:
        return []

    mutated = False
    for entry in entries:
        if "owner_wallet" not in entry or not normalize_address(entry.get("owner_wallet")):
            entry["owner_wallet"] = owner_wallet_normalized
            mutated = True

    if mutated:
        write_agents(entries)

    return [entry for entry in entries if normalize_address(entry.get("owner_wallet")) == owner_wallet_normalized]


def append_audit(entry: dict[str, Any]) -> None:
    """Append a structured audit record."""

    _ensure_storage_dir()
    with audit_log_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def get_fernet() -> Fernet:
    """Return Fernet instance seeded with application secret."""

    settings = _settings()
    return Fernet(settings.fernet_key)


def delete_agent(agent_address: str, owner_wallet: str) -> bool:
    """Remove an agent entry owned by the specified wallet."""

    normalized_wallet = normalize_address(owner_wallet)
    normalized_agent = normalize_address(agent_address)
    if not normalized_wallet or not normalized_agent:
        return False

    entries = load_agents()
    new_entries: list[dict[str, Any]] = []
    removed = False
    for entry in entries:
        entry_agent = normalize_address(entry.get("agent_address"))
        entry_owner = normalize_address(entry.get("owner_wallet"))
        if entry_agent == normalized_agent and entry_owner == normalized_wallet:
            removed = True
            continue
        new_entries.append(entry)

    if removed:
        write_agents(new_entries)

    return removed
