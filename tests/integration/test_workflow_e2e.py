"""API creation, real model HTTP, local tool, demo HTTP, selected evidence and restart."""

import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.analysis_worker import AnalysisWorker
from task_platform.execution import Executor
from task_platform.report_transport import ReportTransport
from task_platform.transport import SimulationTransport

from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch
from .test_execution_transport import Simulator
from .test_execution_transport import simulator as simulator
from .test_workflow_reports import report_transport as report_transport
from .test_workflows import analysis, dossier

pytestmark = pytest.mark.integration


async def test_complete_workflow_restarts_between_every_unit_with_lost_final_response(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
    simulator: Simulator,
    report_transport: ReportTransport,
) -> None:
    await dossier(client, command_owner, admin_engine)
    async with SimulationTransport(simulator.url) as model:
        for _ in range(4):
            # A new executor/worker sees only PostgreSQL state; no in-memory conversation.
            assert await AnalysisWorker(
                analysis(executor), model, report_transport, holder=str(uuid4())
            ).run_once()
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT state FROM tasks")) == "suspended"
            assert await c.scalar(text("SELECT count(*) FROM steps")) == 4
        assert await analysis(executor).recover_reports(report_transport)
        assert not await AnalysisWorker(
            analysis(executor), model, report_transport, holder="done"
        ).run_once()
    assert simulator.count("request") == 2
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "succeeded"
        result = await c.scalar(text("SELECT result FROM tasks"))
        assert result["report"]["citations"][0]["quote"].startswith("Revenue is 42.")
        assert await c.scalar(text("SELECT tool_calls FROM tasks")) == 2
        assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 3


async def test_cli_process_restart_after_each_unit_and_final_reconciliation(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    simulator: Simulator,
    report_transport: ReportTransport,
) -> None:
    import os
    import sys
    from pathlib import Path

    await dossier(client, command_owner, admin_engine)
    directory = Path(os.environ["TEST_SECRETS_DIR"])
    env = {
        **os.environ,
        "DATABASE_URL_FILE": str(directory / "worker_database_url"),
        "ATP_TEST_PROFILE": "1",
        "DISPATCH_ENABLED": "true",
        "SIMULATOR_URL": simulator.url,
        "DEMO_URL": report_transport.url,
        "DEMO_SERVICE_KEY_FILE": str(directory / "demo_service_key"),
    }
    for _ in range(5):
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            "from task_platform.cli import main; main()",
            "worker",
            "--analysis",
            "--once",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(child.communicate(), 15)
        assert child.returncode == 0, stderr.decode()
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "succeeded"
        assert await c.scalar(text("SELECT count(*) FROM attempts")) == 4
    assert simulator.count("request") == 2


async def test_hard_process_death_after_each_committed_unit_keeps_one_continuation(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    simulator: Simulator,
    report_transport: ReportTransport,
    executor: Executor,
) -> None:
    import os
    import sys
    from pathlib import Path

    await dossier(client, command_owner, admin_engine)
    directory = Path(os.environ["TEST_SECRETS_DIR"])
    env = {
        **os.environ,
        "DATABASE_URL_FILE": str(directory / "worker_database_url"),
        "ATP_TEST_PROFILE": "1",
        "DISPATCH_ENABLED": "true",
        "SIMULATOR_URL": simulator.url,
        "DEMO_URL": report_transport.url,
        "DEMO_SERVICE_KEY_FILE": str(directory / "demo_service_key"),
    }
    for _ in range(4):
        child = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "tests.support.workflow_crash",
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await asyncio.wait_for(child.communicate(), 15)
        assert child.returncode == 91, stderr.decode()
    assert await analysis(executor).recover_reports(report_transport)
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "succeeded"
        assert await c.scalar(text("SELECT count(*) FROM steps")) == 4
        assert await c.scalar(text("SELECT count(*) FROM attempts")) == 4
        assert await c.scalar(text("SELECT count(*) FROM cost_evidence")) == 3
    assert simulator.count("request") == 2
