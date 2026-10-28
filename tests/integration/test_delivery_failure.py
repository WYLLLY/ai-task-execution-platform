"""Actual transactional Alembic failure cannot leave partial DDL or reopen dispatch."""

import asyncio
import os
import shutil
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import sessions
from task_platform.delivery import delivery_phase
from task_platform.health import SCHEMA_REVISION

from .conftest import settings_for

pytestmark = pytest.mark.integration


async def test_actual_failed_forward_migration_keeps_old_schema_and_dispatch_closed(
    command_owner: dict[str, str], admin_engine: AsyncEngine, tmp_path: Path
) -> None:
    await delivery_phase(sessions(admin_engine), settings_for("admin"), "close")

    def fixture() -> Path:
        target = tmp_path / "migrations"
        shutil.copytree(Path("migrations"), target)
        (target / "versions" / "9999_fault.py").write_text(
            'from alembic import op\nrevision="9999_fault"\n'
            f'down_revision="{SCHEMA_REVISION}"\n'
            "def upgrade():\n"
            '    op.execute("CREATE TABLE m7_failed_partial(id integer)")\n'
            '    op.execute("SELECT definitely_missing_m7_migration_function()")\n'
            'def downgrade():\n    raise RuntimeError("not a real release")\n'
        )
        config = tmp_path / "alembic.ini"
        config.write_text(f"[alembic]\nscript_location={target}\n")
        return config

    config = await asyncio.to_thread(fixture)
    child = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "alembic",
        "-c",
        str(config),
        "upgrade",
        "head",
        env={
            **os.environ,
            "DATABASE_URL_FILE": str(
                Path(os.environ["TEST_SECRETS_DIR"]) / "migrator_database_url"
            ),
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(child.communicate(), 15)
    assert child.returncode != 0
    assert b"definitely_missing_m7_migration_function" in stderr
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT to_regclass('public.m7_failed_partial')")) is None
        assert await c.scalar(text("SELECT version_num FROM alembic_version")) == SCHEMA_REVISION
        assert not await c.scalar(
            text("SELECT dispatch_open OR admission_open FROM platform_control")
        )
    # Compatibility of the retained application is explicit; migration failure
    # itself never invokes a reopen or an automatic destructive downgrade.
    assert (await delivery_phase(sessions(admin_engine), settings_for("admin"), "preflight"))[
        "ready"
    ]
