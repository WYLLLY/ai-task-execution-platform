from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.api import create_app
from task_platform.db import sessions
from task_platform.identity import issue_key, provision_principal

from .conftest import settings_for

pytestmark = pytest.mark.integration


async def test_real_redis_and_postgres_readiness(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["postgres"] is True
    assert response.json()["redis"] is True
    assert response.json()["schema_compatible"] is True
    assert response.json()["dispatch_available"] is False
    assert response.json()["submission_available"] is False
    assert (await client.post("/v1/tasks")).status_code == 404


async def test_redis_unavailable_preserves_authenticated_reads(admin_engine: AsyncEngine) -> None:
    principal = await provision_principal(sessions(admin_engine), "Redis outage")
    _, token = await issue_key(sessions(admin_engine), principal)
    config = settings_for("api").model_copy(update={"redis_url": SecretStr("redis://127.0.0.1:1")})
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        result = await client.get("/health/ready")
        assert result.status_code == 200
        assert result.json()["redis"] is False
        assert result.json()["submission_available"] is False
        assert (
            await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        ).status_code == 200


async def test_epoch_mismatch_is_not_ready() -> None:
    app = create_app(settings_for("api").model_copy(update={"execution_epoch": uuid4()}))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        result = await client.get("/health/ready")
        assert result.status_code == 503
        assert result.json()["epoch_matches"] is False
        assert (await client.get("/health/live")).status_code == 200


async def test_database_outage_is_sanitized_and_not_ready() -> None:
    config = settings_for("api").model_copy(
        update={
            "database_url": SecretStr(
                "postgresql+psycopg://atp_api:SENSITIVE_CANARY@127.0.0.1:1/atp"
            )
        }
    )
    app = create_app(config)
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        assert (await client.get("/health/live")).status_code == 200
        result = await client.get("/health/ready")
        assert result.status_code == 503 and result.json()["postgres"] is False
        response = await client.get("/v1/me", headers={"Authorization": "Bearer atp_" + "a" * 43})
        assert response.status_code == 503
        assert "SENSITIVE_CANARY" not in result.text + response.text
