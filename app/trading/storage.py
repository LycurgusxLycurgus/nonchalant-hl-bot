"""Storage helpers for trading bot run records."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.authz import storage as auth_storage

_RUN_STORAGE_PATH = auth_storage.storage_dir() / "runs.json"


def _runs_path() -> Path:
    directory = auth_storage.storage_dir()
    directory.mkdir(parents=True, exist_ok=True)
    return _RUN_STORAGE_PATH


def load_runs() -> list[dict[str, Any]]:
    path = _runs_path()
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _write_runs(entries: list[dict[str, Any]]) -> None:
    path = _runs_path()
    path.write_text(json.dumps(entries, indent=2), encoding="utf-8")


def get_run(run_id: str) -> dict[str, Any] | None:
    """Return a single run record by identifier, if present."""

    for entry in load_runs():
        if entry.get("run_id") == run_id:
            return entry
    return None


def append_run(entry: dict[str, Any]) -> None:
    runs = load_runs()
    runs.append(entry)
    _write_runs(runs)


def update_run(run_id: str, updates: dict[str, Any]) -> None:
    entries = load_runs()
    changed = False
    for entry in entries:
        if entry.get("run_id") == run_id:
            entry.update(updates)
            changed = True
            break
    if changed:
        _write_runs(entries)


def runs_path() -> Path:
    """Expose runs file path for test assertions."""

    return _runs_path()
