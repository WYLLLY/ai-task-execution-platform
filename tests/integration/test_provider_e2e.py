"""Fresh worker processes replay Responses items and reconcile the demo report."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import sessions
from task_platform.provider_admin import configure_provider
from task_platform.report_transport import ReportTransport
from task_platform.responses import validate_profile
from tests.unit.test_responses import profile_data

from .test_execution_calls import dispatch as dispatch
from .test_execution_transport import Simulator
from .test_provider_contracts import responses_server as responses_server
from .test_responses_workflow import submit
from .test_workflow_reports import report_transport as report_transport

pytestmark = pytest.mark.integration


async def test_openai_cli_process_restart_and_remote_report_reconciliation(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    responses_server: Simulator,
    report_transport: ReportTransport,
    tmp_path: Path,
) -> None:
    profile_file = tmp_path / "profile.json"
    profile_file.write_text(json.dumps(profile_data()))
    await configure_provider(sessions(admin_engine), validate_profile(profile_data()))
    await submit(client, command_owner)
    owned = (await client.get("/v1/tasks", headers=command_owner)).json()["items"]
    assert len(owned) == 1
    task_id = owned[0]["task_id"]
    directory = Path(os.environ["TEST_SECRETS_DIR"])
    env = {
        **os.environ,
        "DATABASE_URL_FILE": str(directory / "worker_database_url"),
        "ATP_TEST_PROFILE": "1",
        "DISPATCH_ENABLED": "true",
        "OPENAI_PROFILE_FILE": str(profile_file),
        "OPENAI_API_KEY": "synthetic-test-key",
        "RESPONSES_TEST_URL": responses_server.url,
        "DEMO_URL": report_transport.url,
        "DEMO_SERVICE_KEY_FILE": str(directory / "demo_service_key"),
    }
    history: list[object] = []
    wait_budget = 10.0
    for _ in range(5):
        # Process startup is not a clock. Honor both persisted eligibility gates,
        # including the synthetic server's explicit shared Retry-After cooldown.
        async with admin_engine.connect() as connection:
            wait = await connection.scalar(
                text(
                    "SELECT greatest(0,extract(epoch FROM "
                    "(SELECT min(available_at) FROM steps WHERE task_id=:task "
                    "AND state IN ('ready','retry_wait','unknown'))-clock_timestamp()),"
                    "extract(epoch FROM (SELECT next_allowed_at FROM provider_admission "
                    "WHERE id=1)-clock_timestamp()))"
                ),
                {"task": task_id},
            )
        assert wait is not None and 0 <= wait <= min(5, wait_budget)
        wait_budget -= float(wait)
        await asyncio.sleep(float(wait))
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "from task_platform.cli import main; main()",
            "worker",
            "--openai",
            "--once",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(process.communicate(), 20)
        assert process.returncode == 0, stderr.decode()
        assert b"synthetic-test-key" not in stdout + stderr
        async with admin_engine.connect() as connection:
            history.append(
                (
                    await connection.execute(
                        text(
                            "SELECT path,state,probe_count,failures, "
                            "extract(epoch FROM available_at-clock_timestamp()) AS delay "
                            "FROM steps WHERE task_id=:task ORDER BY path"
                        ),
                        {"task": task_id},
                    )
                ).all()
            )
    assert responses_server.count("request") == 2
    requests = [json.loads(line) for line in responses_server.journal.read_text().splitlines()]
    assert all(r["payload"]["store"] is False for r in requests)
    assert requests[1]["payload"]["input"][1]["encrypted_content"] == "opaque-do-not-decode"
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "succeeded", history
        assert await c.scalar(text("SELECT sum(confirmed_cost) FROM call_costs")) == 276
        assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 3
        assert await c.scalar(text("SELECT count(*) FROM steps")) == 4
