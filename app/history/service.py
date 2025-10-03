"""History and audit data access helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Iterable

from app.authz import storage as auth_storage
from app.history.schemas import HistoryEvent, HistoryResponse
from app.trading import storage as trading_storage


def _iter_audit_entries(path: Path) -> Iterable[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def load_history(*, offset: int = 0, limit: int = 20, run_id: str | None = None) -> HistoryResponse:
    offset = max(offset, 0)
    limit = max(1, min(limit, 100))

    audit_path = auth_storage.audit_log_path()
    entries = list(_iter_audit_entries(audit_path))

    if run_id:
        entries = [entry for entry in entries if entry.get("run_id") == run_id]

    entries.sort(key=lambda item: item.get("ts") or 0.0, reverse=True)

    total = len(entries)
    page_entries = entries[offset : offset + limit]

    items: list[HistoryEvent] = []
    for entry in page_entries:
        ts = float(entry.get("ts") or 0.0)
        occurred_at = datetime.fromtimestamp(ts, tz=UTC) if ts else datetime.now(tz=UTC)
        run_key = entry.get("run_id")
        run_status = None
        if run_key:
            run_record = trading_storage.get_run(run_key)
            if run_record:
                run_status = run_record.get("status")
        explorer_url = _derive_explorer_url(entry)
        items.append(
            HistoryEvent(
                id=str(entry.get("id") or ""),
                action=str(entry.get("action") or "unknown"),
                run_id=run_key,
                ts=ts,
                occurred_at=occurred_at,
                payload=entry,
                explorer_url=explorer_url,
                run_status=run_status,
            )
        )

    return HistoryResponse(items=items, total=total, offset=offset, limit=limit)


def _derive_explorer_url(entry: dict) -> str | None:
    tx_hash = entry.get("tx_hash") or entry.get("transaction_hash")
    if not tx_hash:
        return None
    if isinstance(tx_hash, str) and tx_hash.startswith("0x"):
        # Default to Arbiscan; callers can override once chain knowledge is present
        return f"https://arbiscan.io/tx/{tx_hash}"
    return None
