import asyncio
import os
import subprocess
import sys
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


async def test_admin_can_control_admission_and_workflow_availability(
    admin_engine: AsyncEngine,
) -> None:
    env = os.environ.copy()
    env["DATABASE_URL_FILE"] = str(Path(env["TEST_SECRETS_DIR"]) / "admin_database_url")
    for action, options in [
        ("admission", []),
        ("workflow-availability", ["--name", "analysis", "--version", "1"]),
    ]:
        for enabled in ("true", "false", "true"):
            result = await asyncio.to_thread(
                subprocess.run,
                [
                    sys.executable,
                    "-c",
                    "from task_platform.cli import main; main()",
                    "admin",
                    action,
                    "--enabled",
                    enabled,
                    *options,
                ],
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            assert result.returncode == 0, result.stderr
        async with admin_engine.connect() as c:
            query = (
                "SELECT admission_open FROM platform_control"
                if action == "admission"
                else "SELECT available FROM workflow_versions"
            )
            assert await c.scalar(text(query)) is True
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE platform_control SET admission_open=false"))
