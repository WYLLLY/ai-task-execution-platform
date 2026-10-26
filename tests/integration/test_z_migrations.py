"""Destructive round-trip only in the disposable M1 test Compose project."""

import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import sessions
from task_platform.identity import initialize_platform

from .conftest import settings_for

pytestmark = pytest.mark.integration


async def test_migration_roundtrip_and_schema_readiness(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    env = os.environ.copy()
    env["DATABASE_URL_FILE"] = str(Path(env["TEST_SECRETS_DIR"]) / "migrator_database_url")
    for target in ("base", "head", "head"):
        action = "downgrade" if target == "base" else "upgrade"
        completed = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", action, target],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert completed.returncode == 0, completed.stderr
        assert (await client.get("/health/ready")).status_code == 503
    await initialize_platform(sessions(admin_engine), settings_for("admin").execution_epoch)
    assert (await client.get("/health/ready")).status_code == 200
    async with admin_engine.connect() as connection:
        assert (
            await connection.scalar(text("SELECT version_num FROM alembic_version"))
            == "0001_foundation"
        )
    env["DATABASE_URL_FILE"] = str(Path(env["TEST_SECRETS_DIR"]) / "api_database_url")
    denied = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert denied.returncode != 0
    assert "Dedicated migration role required" in denied.stderr
