"""Application configuration loading via Pydantic settings."""

from __future__ import annotations

import base64
import hashlib
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field
from pydantic_settings import BaseSettings


_TESTNET_REST = "https://api.hyperliquid-testnet.xyz"
_TESTNET_WS = "wss://api.hyperliquid-testnet.xyz/ws"
_MAINNET_REST = "https://api.hyperliquid.xyz"
_MAINNET_WS = "wss://api.hyperliquid.xyz/ws"


class Settings(BaseSettings):
    """Runtime configuration sourced from environment variables."""

    secret_key_salt: str = Field(..., alias="SECRET_KEY_SALT")
    hl_env: Literal["dev", "prod"] | None = Field(default="dev", alias="HL_ENV")
    walletconnect_project_id: str | None = Field(default=None, alias="WALLETCONNECT_PROJECT_ID")
    hl_rest_base: str = Field(default=_TESTNET_REST, alias="HL_REST_BASE")
    hl_ws_url: str = Field(default=_TESTNET_WS, alias="HL_WS_URL")
    storage_dir: Path = Field(default=Path("storage"), alias="STORAGE_DIR")
    request_rate_limit_per_minute: int = Field(default=60, alias="REQUEST_RATE_LIMIT_PER_MINUTE")

    model_config = {
        "env_file": ".env",
        "env_file_encoding": "utf-8",
        "extra": "ignore",
    }

    def model_post_init(self, __context: Any) -> None:  # pragma: no cover - simple field mutation
        """Normalize endpoint defaults based on the configured environment."""

        if self.hl_env == "prod":
            if not self.model_fields_set.intersection({"hl_rest_base"}):
                self.hl_rest_base = _MAINNET_REST
            if not self.model_fields_set.intersection({"hl_ws_url"}):
                self.hl_ws_url = _MAINNET_WS

    @property
    def fernet_key(self) -> bytes:
        """Return a Fernet-compatible key derived from the secret salt."""

        digest = hashlib.sha256(self.secret_key_salt.encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)


@lru_cache
def get_settings() -> Settings:
    """Return cached application settings instance."""

    settings = Settings()  # type: ignore[call-arg]
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
