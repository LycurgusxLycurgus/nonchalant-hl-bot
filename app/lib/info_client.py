"""Hyperliquid Info endpoint client for balance polling."""

from __future__ import annotations

from typing import Any

import httpx

from app.lib.logger import get_logger


class InfoClientError(RuntimeError):
    """Raised when the Hyperliquid Info client encounters an error."""


logger = get_logger(__name__)


class InfoClient:
    """Thin wrapper for calling Hyperliquid's Info endpoint."""

    def __init__(self, base_url: str, *, timeout: float = 10.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def fetch_balances(self, address: str) -> dict[str, Any]:
        """Fetch user balances for the provided Hyperliquid address."""

        payload = {
            "type": "spotClearinghouseState",
            "user": address,
        }

        try:
            logger.info(
                "hyperliquid.info.fetch_balances.start",
                extra={"address": address, "base_url": self.base_url},
            )
            async with httpx.AsyncClient(base_url=self.base_url, timeout=self.timeout) as client:
                response = await client.post("/info", json=payload)
                response.raise_for_status()
        except httpx.HTTPStatusError as exc:  # pragma: no cover - exercised via exception path tests
            status = exc.response.status_code
            try:
                detail_json = exc.response.json()
                detail = detail_json if isinstance(detail_json, str) else detail_json.get("msg")
            except ValueError:  # response not json
                detail = exc.response.text
            detail_display = detail[:200] if isinstance(detail, str) else str(detail)
            message = f"Hyperliquid Info request failed ({status}): {detail_display}"
            logger.warning(
                "hyperliquid.info.fetch_balances.http_error",
                extra={
                    "address": address,
                    "base_url": self.base_url,
                    "status": status,
                    "detail": detail_display,
                },
            )
            raise InfoClientError(message) from exc
        except httpx.HTTPError as exc:  # pragma: no cover
            logger.warning(
                "hyperliquid.info.fetch_balances.network_error",
                extra={"address": address, "base_url": self.base_url},
            )
            raise InfoClientError("Hyperliquid Info request failed (network)") from exc

        try:
            data = response.json()
        except ValueError as exc:  # pragma: no cover
            raise InfoClientError("Invalid JSON returned from Hyperliquid Info") from exc

        if not isinstance(data, dict):
            raise InfoClientError("Unexpected Info response shape")

        return data
