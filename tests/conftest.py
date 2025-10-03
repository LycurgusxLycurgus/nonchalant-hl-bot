"""Pytest fixtures for Hyperliquid bot tests."""

from collections.abc import AsyncIterator
import os
from pathlib import Path

import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import AsyncClient

os.environ.setdefault("SECRET_KEY_SALT", "test-secret-key")
os.environ.setdefault("HL_ENV", "dev")
os.environ.setdefault("WALLETCONNECT_PROJECT_ID", "test-project-id")

_STORAGE_PATH = Path(__file__).resolve().parent / "__storage"
os.environ.setdefault("STORAGE_DIR", str(_STORAGE_PATH))
_STORAGE_PATH.mkdir(parents=True, exist_ok=True)

from app.main import app as fastapi_app


@pytest.fixture(scope="session")
def app() -> FastAPI:
    """Return the FastAPI application instance."""
    return fastapi_app


@pytest_asyncio.fixture()
async def async_client(app: FastAPI) -> AsyncIterator[AsyncClient]:
    """Provide an `httpx.AsyncClient` configured for the FastAPI app."""
    async with AsyncClient(app=app, base_url="http://testserver") as client:
        yield client


@pytest.fixture(autouse=True)
def clean_storage() -> AsyncIterator[None]:
    """Ensure the storage directory is empty before and after each test."""

    for child in _STORAGE_PATH.glob("*"):
        if child.is_file():
            child.unlink()
    yield
    for child in _STORAGE_PATH.glob("*"):
        if child.is_file():
            child.unlink()


@pytest.fixture(autouse=True)
def reset_runtime(app: FastAPI) -> AsyncIterator[None]:
    """Reset metrics, rate limiter, and rate limits across tests."""

    metrics = getattr(app.state, "metrics", None)
    limiter = getattr(app.state, "rate_limiter", None)
    original_limit = getattr(app.state, "rate_limit_per_minute", None)
    if metrics is not None:
        metrics.reset()
    if limiter is not None:
        limiter.reset()
    yield
    if metrics is not None:
        metrics.reset()
    if limiter is not None:
        limiter.reset()
    if original_limit is not None:
        app.state.rate_limit_per_minute = original_limit


@pytest.fixture()
def storage_dir() -> Path:
    """Return the configured storage directory path for assertions."""

    return _STORAGE_PATH
