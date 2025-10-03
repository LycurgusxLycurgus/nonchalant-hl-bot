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


def append_audit(entry: dict[str, Any]) -> None:
    """Append a structured audit record."""

    _ensure_storage_dir()
    with audit_log_path().open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry) + "\n")


def get_fernet() -> Fernet:
    """Return Fernet instance seeded with application secret."""

    settings = _settings()
    return Fernet(settings.fernet_key)
