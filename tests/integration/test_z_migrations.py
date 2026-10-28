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
from task_platform.execution import Executor
from task_platform.identity import initialize_platform, issue_key

from .conftest import settings_for
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch

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
        revision = await connection.scalar(text("SELECT version_num FROM alembic_version"))
        assert revision == "0011_recovery_authority"
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


async def test_m1_upgrade_preserves_identity_audit_epoch_and_roles(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    env = os.environ.copy()
    env["DATABASE_URL_FILE"] = str(Path(env["TEST_SECRETS_DIR"]) / "migrator_database_url")
    downgraded = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "downgrade", "0001_foundation"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert downgraded.returncode == 0, downgraded.stderr
    # M1-compatible direct SQL, since current ORM legitimately includes M2 columns.
    async with admin_engine.begin() as c:
        owner = await c.scalar(
            text(
                "INSERT INTO principals(id,name) "
                "VALUES(gen_random_uuid(),'M1 upgrade fixture') RETURNING id"
            )
        )
        before = (
            await c.execute(text("SELECT execution_epoch,version FROM platform_control WHERE id=1"))
        ).one()
        audits = await c.scalar(text("SELECT count(*) FROM audit_events"))
    assert (await client.get("/health/ready")).status_code == 503
    upgraded = await asyncio.to_thread(
        subprocess.run,
        [sys.executable, "-m", "alembic", "upgrade", "head"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert upgraded.returncode == 0, upgraded.stderr
    async with admin_engine.connect() as c:
        assert (
            await c.scalar(text("SELECT name FROM principals WHERE id=:id"), {"id": owner})
            == "M1 upgrade fixture"
        )
        assert (
            await c.execute(text("SELECT execution_epoch,version FROM platform_control WHERE id=1"))
        ).one() == before
        assert await c.scalar(text("SELECT count(*) FROM audit_events")) == audits
        assert (
            await c.scalar(text("SELECT open_tasks FROM principals WHERE id=:id"), {"id": owner})
            == 0
        )
    _, token = await issue_key(sessions(admin_engine), owner)
    assert (
        await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 200
    assert (await client.get("/health/ready")).status_code == 200


async def test_m3_downgrade_restores_m2_worker_privileges(admin_engine: AsyncEngine) -> None:
    env = os.environ.copy()
    env["DATABASE_URL_FILE"] = str(Path(env["TEST_SECRETS_DIR"]) / "migrator_database_url")
    try:
        down = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "downgrade", "0002_commands"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert down.returncode == 0, down.stderr
        async with admin_engine.connect() as c:
            for table, column in (
                ("tasks", "state"),
                ("tasks", "result"),
                ("steps", "state"),
                ("attempts", "state"),
                ("principals", "open_tasks"),
                ("platform_control", "storage_bytes"),
            ):
                assert not await c.scalar(
                    text("SELECT has_column_privilege('atp_worker',:table,:column,'UPDATE')"),
                    {"table": table, "column": column},
                ), (table, column)
            assert await c.scalar(
                text("SELECT has_column_privilege('atp_api','attempts','state','UPDATE')")
            )
    finally:
        up = await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        assert up.returncode == 0, up.stderr


async def test_m5_populated_upgrade_preserves_estimated_cost_and_legacy_attempt(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    from task_platform.provider_admin import reconcile

    from .test_accounting_admin import evidence, pending

    env = os.environ.copy()
    env["DATABASE_URL_FILE"] = str(Path(env["TEST_SECRETS_DIR"]) / "migrator_database_url")
    try:
        # M3 dispatch is intentionally opt-in even in this synthetic migration fixture.
        async with admin_engine.begin() as c:
            await c.execute(text("UPDATE platform_control SET dispatch_open=true"))
        call = await pending(client, command_owner, executor)
        assert await reconcile(sessions(admin_engine), call, evidence(estimated=True))
        for action, target in (("downgrade", "0005_provider"), ("upgrade", "head")):
            result = await asyncio.to_thread(
                subprocess.run,
                [sys.executable, "-m", "alembic", action, target],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, result.stderr
        async with admin_engine.connect() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT confirmed_cost,exposure_cost,estimated FROM call_costs "
                        "WHERE call_id=:id"
                    ),
                    {"id": call},
                )
            ).one()
            assert row == (20, 0, True)
            assert (
                await c.scalar(text("SELECT count(*) FROM attempts WHERE trace_context IS NULL"))
                == 1
            )
    finally:
        async with admin_engine.begin() as c:
            await c.execute(text("UPDATE platform_control SET dispatch_open=false"))
