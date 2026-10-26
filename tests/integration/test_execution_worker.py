"""M3 runner, physical simulator and PostgreSQL form a complete synthetic workflow."""

import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.execution import Executor
from task_platform.transport import SimulationTransport
from task_platform.worker import Worker

from .test_commands import create
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch
from .test_execution_transport import Simulator
from .test_execution_transport import simulator as simulator

pytestmark = pytest.mark.integration


async def test_worker_executes_via_http_and_commits_result(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    simulator: Simulator,
    admin_engine: AsyncEngine,
) -> None:
    await create(client, command_owner)
    async with SimulationTransport(simulator.url) as transport:
        worker = Worker(executor, transport, holder=str(uuid4()))
        assert await worker.run_once()
        assert not await worker.run_once()
    assert simulator.count("request") == simulator.count("effect") == 1
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "succeeded"
        assert await c.scalar(text("SELECT exposure_cost FROM call_costs")) == 0


async def test_worker_heartbeat_and_cancel_interrupt_local_http(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    simulator: Simulator,
    admin_engine: AsyncEngine,
) -> None:
    item = await client.post(
        "/v1/tasks",
        headers={**command_owner, "Idempotency-Key": "slow"},
        json={"workflow": {"name": "analysis", "version": "1"}, "input": {"texts": ["slow"]}},
    )
    assert item.status_code == 202
    task_id = item.json()["task_id"]
    async with SimulationTransport(simulator.url) as transport:
        worker = Worker(executor, transport, holder="worker", heartbeat_seconds=0.05)
        running = asyncio.create_task(worker.run_once())
        async with asyncio.timeout(1):
            while simulator.count("request") == 0:
                if running.done():
                    assert await running and simulator.count("request") == 1
                await asyncio.sleep(0.005)
        snapshot = (await client.get(f"/v1/tasks/{task_id}", headers=command_owner)).json()
        result = await client.post(
            f"/v1/tasks/{task_id}/cancel",
            headers={**command_owner, "Idempotency-Key": "cancel"},
            json={"expected_task_version": snapshot["task_version"]},
        )
        assert result.status_code == 200
        assert await asyncio.wait_for(running, 1)
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "cancelled"
        assert await c.scalar(text("SELECT state FROM external_calls")) == "unknown"
        assert await c.scalar(text("SELECT exposure_cost FROM call_costs")) > 0
    assert simulator.count("request") == simulator.count("effect") == 1


async def test_m3_health_is_ready_with_execution_schema(client: AsyncClient) -> None:
    response = await client.get("/health/ready")
    assert response.status_code == 200
    assert response.json()["schema_compatible"]


async def test_metrics_expose_durable_aggregate_state_only(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
) -> None:
    from task_platform.metrics import execution_metrics

    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    assert await executor.complete(lease, call, "unknown")
    metrics = await execution_metrics(executor.control)
    assert metrics["unknown_calls"] == 1
    assert metrics["active_calls"] == 0
    assert metrics["exposure_micro_usd"] == 4004
    assert metrics["exposure_tokens"] == 2002
    assert metrics["suspended_tasks"] == 1
    assert all(type(value) is int for value in metrics.values())


async def test_worker_cli_preflight_and_explicit_simulation_profile(
    client: AsyncClient,
    command_owner: dict[str, str],
    simulator: Simulator,
) -> None:
    import json
    import os
    import sys
    from pathlib import Path

    env = os.environ.copy()
    env["DATABASE_URL_FILE"] = str(Path(env["TEST_SECRETS_DIR"]) / "worker_database_url")
    command = [sys.executable, "-c", "from task_platform.cli import main; main()", "worker"]
    check = await asyncio.create_subprocess_exec(
        *command, "--check", env=env, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    out, err = await asyncio.wait_for(check.communicate(), 5)
    assert check.returncode == 0, err.decode()
    assert json.loads(out)["execution_implemented"] is True
    await create(client, command_owner)
    env["SIMULATOR_URL"] = simulator.url
    denied = await asyncio.create_subprocess_exec(
        *command,
        "--simulate",
        "--once",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    await asyncio.wait_for(denied.communicate(), 5)
    assert denied.returncode == 1
    assert simulator.count("request") == 0
    env["ATP_TEST_PROFILE"] = "1"
    env["DISPATCH_ENABLED"] = "true"
    process = await asyncio.create_subprocess_exec(
        *command,
        "--simulate",
        "--once",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, err = await asyncio.wait_for(process.communicate(), 5)
    assert process.returncode == 0, err.decode()
    assert simulator.count("request") == simulator.count("effect") == 1


async def test_worker_context_overflow_fails_explicitly_without_http(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    simulator: Simulator,
    admin_engine: AsyncEngine,
) -> None:
    result = await client.post(
        "/v1/tasks",
        headers={**command_owner, "Idempotency-Key": "oversized-context"},
        json={"workflow": {"name": "analysis", "version": "1"}, "input": {"texts": ["x" * 12000]}},
    )
    assert result.status_code == 202
    async with SimulationTransport(simulator.url) as transport:
        assert await Worker(executor, transport, holder="worker").run_once()
    async with admin_engine.connect() as c:
        assert (await c.execute(text("SELECT state,reason FROM tasks"))).one() == (
            "failed",
            "context_limit",
        )
    assert simulator.count("request") == 0


async def test_control_database_connection_loss_stops_local_request(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    simulator: Simulator,
    admin_engine: AsyncEngine,
) -> None:
    from pydantic import SecretStr

    from task_platform.db import create_engine, sessions

    from .conftest import settings_for

    response = await client.post(
        "/v1/tasks",
        headers={**command_owner, "Idempotency-Key": "slow-db"},
        json={"workflow": {"name": "analysis", "version": "1"}, "input": {"texts": ["slow"]}},
    )
    assert response.status_code == 202
    broken = create_engine(
        settings_for("worker").model_copy(
            update={
                "database_url": SecretStr(
                    "postgresql+psycopg://synthetic:invalid@127.0.0.1:1/unavailable"
                )
            }
        ),
        control=True,
    )
    original = executor.control
    try:
        async with SimulationTransport(simulator.url) as transport:
            work = asyncio.create_task(
                Worker(executor, transport, holder="worker", heartbeat_seconds=0.05).run_once()
            )
            for _ in range(150):
                if simulator.count("request"):
                    break
                await asyncio.sleep(0.005)
            assert simulator.count("request") == 1
            executor.control = sessions(broken)
            assert await asyncio.wait_for(work, 1)
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT state FROM external_calls")) == "authorized"
            assert await c.scalar(text("SELECT exposure_cost FROM call_costs")) > 0
        executor.control = original
        engine = create_engine(settings_for("migrator"))
        try:
            async with engine.begin() as c:
                await c.execute(
                    text("UPDATE steps SET lease_until=clock_timestamp()-interval '1 second'")
                )
            await executor.maintenance()
        finally:
            await engine.dispose()
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT state FROM tasks")) == "suspended"
            assert await c.scalar(text("SELECT state FROM external_calls")) == "unknown"
    finally:
        executor.control = original
        await broken.dispose()


@pytest.mark.parametrize(
    "table,privilege", [("external_calls", "INSERT"), ("call_costs", "UPDATE(exposure_cost)")]
)
async def test_worker_preflight_requires_execution_write_grants(table: str, privilege: str) -> None:
    from task_platform.db import create_engine
    from task_platform.health import check_database

    from .conftest import settings_for

    migration = create_engine(settings_for("migrator"))
    worker = create_engine(settings_for("worker"), control=True)
    try:
        async with migration.begin() as c:
            await c.execute(text(f"REVOKE {privilege} ON {table} FROM atp_worker"))
        postgres, compatible, _ = await check_database(worker, settings_for("worker"))
        assert postgres
        assert not compatible
    finally:
        async with migration.begin() as c:
            await c.execute(text(f"GRANT {privilege} ON {table} TO atp_worker"))
        await worker.dispose()
        await migration.dispose()


@pytest.mark.parametrize(
    "mode,state,minimum,maximum",
    [
        ("retry_after", "running", 29, 31),
        ("retry_after_date", "running", 28, 31),
        ("rate_limited", "suspended", 29, 31),
        ("bad_retry_after", "suspended", 0, 2),
        ("huge_retry_after", "suspended", 86398, 86401),
    ],
)
async def test_http_retry_after_is_durable_and_shared(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    simulator: Simulator,
    admin_engine: AsyncEngine,
    mode: str,
    state: str,
    minimum: int,
    maximum: int,
) -> None:
    response = await client.post(
        "/v1/tasks",
        headers={**command_owner, "Idempotency-Key": "retry-after"},
        json={"workflow": {"name": "analysis", "version": "1"}, "input": {"texts": [mode]}},
    )
    assert response.status_code == 202
    async with SimulationTransport(simulator.url) as transport:
        assert await Worker(executor, transport, holder="worker").run_once()
    async with admin_engine.connect() as c:
        delay = await c.scalar(
            text(
                "SELECT extract(epoch FROM next_allowed_at-clock_timestamp()) "
                "FROM provider_admission"
            )
        )
        assert minimum <= delay <= maximum
        assert await c.scalar(text("SELECT state FROM tasks")) == state
        if state == "running":
            assert await c.scalar(
                text(
                    "SELECT available_at >= next_allowed_at "
                    "FROM steps CROSS JOIN provider_admission"
                )
            )
        else:
            assert await c.scalar(text("SELECT state FROM external_calls")) == "unknown"
            assert await c.scalar(text("SELECT exposure_cost FROM call_costs")) > 0
    await create(client, command_owner, key="second-rate-limited-task")
    lease = await executor.claim("second-worker")
    assert lease
    assert await executor.authorize(lease, uuid4(), b"{}") is None
    assert simulator.count("request") == 1
