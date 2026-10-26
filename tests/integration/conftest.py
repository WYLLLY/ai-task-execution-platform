import asyncio
import os
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.api import create_app
from task_platform.config import Role, Settings
from task_platform.db import create_engine, sessions
from task_platform.identity import issue_key, provision_principal


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


@pytest.fixture
async def command_owner(admin_engine: AsyncEngine) -> AsyncIterator[dict[str, str]]:
    owner = await provision_principal(sessions(admin_engine), "M2 test")
    _, token = await issue_key(sessions(admin_engine), owner)
    async with admin_engine.begin() as connection:
        await connection.execute(text("UPDATE platform_control SET admission_open=true WHERE id=1"))
    yield {
        "Authorization": f"Bearer {token}",
        "X-Execution-Epoch": str(settings_for("api").execution_epoch),
    }
    # Disposable tests only; preserve all M1 identities and restore the initial closed admission.
    engine = create_engine(settings_for("migrator"))
    try:
        # Test-only DDL cleanup can contend with the running API health probe.
        # Retry only a confirmed lock-timeout rollback, never an ambiguous commit.
        for attempt in range(3):
            try:
                async with engine.begin() as connection:
                    if await connection.scalar(text("SELECT to_regclass('public.tasks')")):
                        await connection.execute(
                            text(
                                "TRUNCATE accounting_decisions, cost_evidence, call_costs, "
                                "external_calls, "
                                "tasks, steps, attempts, task_events, idempotency_records"
                            )
                        )
                        await connection.execute(
                            text("UPDATE principals SET open_tasks=0, storage_bytes=0")
                        )
                        await connection.execute(
                            text(
                                "UPDATE platform_control SET open_tasks=0, storage_bytes=0, "
                                "max_open_tasks=100"
                            )
                        )
                        await connection.execute(
                            text("UPDATE workflow_versions SET available=true")
                        )
                    await connection.execute(
                        text("UPDATE platform_control SET admission_open=false WHERE id=1")
                    )
                break
            except OperationalError as error:
                if getattr(error.orig, "sqlstate", None) != "55P03" or attempt == 2:
                    raise
                await asyncio.sleep(0.05)
    finally:
        await engine.dispose()
