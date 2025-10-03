"""Simple in-memory rate limiter."""

from __future__ import annotations

import threading
import time
from typing import Dict, Tuple

from fastapi import HTTPException, Request


class RateLimiter:
    """Enforces a maximum number of events per window per key."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: Dict[str, Tuple[int, float]] = {}

    def allow(self, key: str, limit: int, window_seconds: int = 60) -> bool:
        if limit <= 0:
            return True
        now = time.monotonic()
        with self._lock:
            count, window_start = self._entries.get(key, (0, now))
            if now - window_start >= window_seconds:
                count = 0
                window_start = now
            if count >= limit:
                return False
            self._entries[key] = (count + 1, window_start)
            return True

    def reset(self) -> None:
        with self._lock:
            self._entries.clear()


def enforce_rate_limit(request: Request, scope: str) -> None:
    rate_limiter: RateLimiter | None = getattr(request.app.state, "rate_limiter", None)  # type: ignore[attr-defined]
    limit: int = getattr(request.app.state, "rate_limit_per_minute", 0)  # type: ignore[attr-defined]
    if rate_limiter is None or limit <= 0:
        return

    base = request.session.get("wallet_address")  # type: ignore[arg-type]
    if not base:
        client = getattr(request, "client", None)
        base = getattr(client, "host", "anonymous")
    key = f"{scope}:{base}"
    if not rate_limiter.allow(key, limit):
        raise HTTPException(status_code=429, detail="Too many requests")
