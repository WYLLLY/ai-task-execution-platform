import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.api import create_app
from task_platform.config import Role, Settings
from task_platform.db import create_engine


def settings_for(role: Role) -> Settings:
    directory = Path(os.environ["TEST_SECRETS_DIR"])
    return Settings(
        role=role,
        database_url=SecretStr((directory / f"{role}_database_url").read_text().strip()),
        redis_url=SecretStr((directory / "redis_url").read_text().strip())
        if role == "api"
        else None,
        execution_epoch=UUID(os.environ["EXECUTION_EPOCH"]),
    )


@pytest.fixture
async def admin_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings_for("admin"))
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def client() -> AsyncIterator[AsyncClient]:
    app = create_app(settings_for("api"))
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
    ):
        yield client
