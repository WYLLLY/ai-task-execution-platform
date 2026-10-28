"""System readiness remains structured and bounded across a second DB failure."""

import asyncio
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.exc import SQLAlchemyError

import task_platform.api as api
import task_platform.recovery as recovery
from task_platform.config import Settings
from task_platform.health import Health


@pytest.mark.parametrize("initial_ready", [False, True])
@pytest.mark.parametrize("failure", [OSError, TimeoutError, SQLAlchemyError])
async def test_system_outage_preserves_structured_unavailable_status(
    monkeypatch: pytest.MonkeyPatch, initial_ready: bool, failure: type[Exception]
) -> None:
    status = Health(
        ready=initial_ready,
        postgres=initial_ready,
        redis=True,
        schema_compatible=initial_ready,
        epoch_matches=True,
        execution_epoch=UUID(int=1),
        submission_available=initial_ready,
        dispatch_available=False,
    )
    monkeypatch.setattr(api, "health", AsyncMock(return_value=status))
    read = AsyncMock(side_effect=failure("private DB details"))
    monkeypatch.setattr(recovery, "recovery_status", read)
    app = api.create_app(
        Settings(
            role="api",
            database_url=SecretStr("postgresql+psycopg://unused:unused@localhost/test"),
            redis_url=SecretStr("redis://localhost/0"),
            execution_epoch=UUID(int=1),
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/v1/system")
    assert response.status_code == 503
    assert response.json()["ready"] is False
    assert response.json()["postgres"] is False
    assert response.json()["submission_available"] is False
    assert response.json()["recovery"] is None
    assert "private" not in response.text
    assert read.await_count == int(initial_ready)


async def test_system_recovery_read_has_absolute_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    async def stalled(_: object) -> None:
        await asyncio.Event().wait()

    status = Health(
        ready=True,
        postgres=True,
        redis=True,
        schema_compatible=True,
        epoch_matches=True,
        execution_epoch=UUID(int=1),
        submission_available=True,
        dispatch_available=False,
    )
    monkeypatch.setattr(api, "health", AsyncMock(return_value=status))
    monkeypatch.setattr(recovery, "recovery_status", stalled)
    app = api.create_app(
        Settings(
            role="api",
            database_url=SecretStr("postgresql+psycopg://unused:unused@localhost/test"),
            redis_url=SecretStr("redis://localhost/0"),
            execution_epoch=UUID(int=1),
        )
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        async with asyncio.timeout(5):
            response = await client.get("/v1/system")
    assert response.status_code == 503
    assert response.json()["recovery"] is None
