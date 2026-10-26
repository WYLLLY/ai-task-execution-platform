"""Operational signals come from real durable state and never disclose source content."""

import asyncio
import json
import logging
import os
import subprocess
import sys
from pathlib import Path

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine
from task_platform.execution import Executor

from .conftest import settings_for
from .test_commands import create
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch

pytestmark = pytest.mark.integration


async def test_runtime_metrics_are_bounded_and_event_gap_raises_actionable_signal(
    client: AsyncClient, command_owner: dict[str, str]
) -> None:
    item = await create(client, command_owner)
    metric = await client.get("/metrics")
    assert metric.status_code == 200, "M6 must expose local process metrics"
    assert "atp_sse_connections" in metric.text
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(text("DELETE FROM task_events"))
        response = await client.get(f"/v1/tasks/{item['task_id']}/events", headers=command_owner)
        assert response.status_code == 410
        metric = await client.get("/metrics")
        assert "atp_event_invariants" in metric.text
        assert str(item["task_id"]) not in metric.text
        assert "Synthetic dossier" not in metric.text and "Bearer" not in metric.text
    finally:
        await engine.dispose()


async def test_admin_operational_command_and_role_boundary(
    admin_engine: AsyncEngine,
) -> None:
    env = os.environ.copy()
    directory = Path(env["TEST_SECRETS_DIR"])
    env["DATABASE_URL_FILE"] = str(directory / "admin_database_url")
    command = [
        sys.executable,
        "-c",
        "from task_platform.cli import main; main()",
        "admin",
        "operations",
        "--format",
        "json",
    ]
    result = await asyncio.to_thread(
        subprocess.run, command, env=env, capture_output=True, text=True, timeout=8, check=False
    )
    assert result.returncode == 0, "administrative operations command is missing"
    body = json.loads(result.stdout)
    assert "metrics" in body and "alerts" in body
    assert "pool_utilization_percent" in body["metrics"], "CLI must collect actual API pool signals"
    assert "pool_acquire_milliseconds" in body["metrics"]
    assert {a["id"] for a in body["alerts"]} >= {"backup.check_missing", "storage.check_missing"}
    env["DATABASE_URL_FILE"] = str(directory / "api_database_url")
    denied = await asyncio.to_thread(
        subprocess.run, command, env=env, capture_output=True, text=True, timeout=8, check=False
    )
    assert denied.returncode == 1 and "command.failed" in denied.stderr


async def test_real_pool_wait_is_measured_without_consuming_control_capacity() -> None:
    settings = settings_for("api").model_copy(update={"read_pool_size": 1})
    engine, control = create_engine(settings), create_engine(settings, control=True)
    try:
        async with engine.connect():
            waiting = asyncio.create_task(engine.connect().__aenter__())
            await asyncio.sleep(1.05)
            assert not waiting.done()
            async with asyncio.timeout(0.5), control.connect() as c:
                assert await c.scalar(text("SELECT 1")) == 1
        connection = await waiting
        await connection.close()
        measured = getattr(engine.pool, "max_recent_acquire_milliseconds", 0)
        assert measured >= 1000, "real pool acquisition delay must be observable"
    finally:
        await engine.dispose()
        await control.dispose()


async def test_authorized_call_trace_correlates_all_ids_without_payload(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from uuid import uuid4

    from task_platform.observability import JSONFormatter

    item = await create(client, command_owner)
    lease = await executor.claim("trace-call")
    assert lease
    caplog.set_level(logging.INFO, logger="task_platform.telemetry")
    call = uuid4()
    assert await executor.authorize(lease, call, b'{"text":"TRACE_PRIVATE_CANARY"}')
    records = [
        record
        for record in caplog.records
        if getattr(record, "event", None) == "execution.progress"
    ]
    assert records, "committed B must correlate its call with task, step and attempt"
    value = json.loads(JSONFormatter().format(records[-1]))
    assert value["task_id"] == item["task_id"] and value["step_id"] == str(lease.step_id)
    assert value["attempt_id"] == str(lease.attempt_id) and value["call_id"] == str(call)
    assert len(value["trace_id"]) == 32 and "TRACE_PRIVATE_CANARY" not in str(value)


async def test_failed_renewals_emit_actionable_alert_without_resurrecting_lease(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    caplog: pytest.LogCaptureFixture,
) -> None:
    from task_platform import runtime_signals
    from task_platform.transport import SimulationTransport
    from task_platform.worker import Worker

    await create(client, command_owner)
    lease = await executor.claim("failed-renewals")
    assert lease
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE steps SET lease_until=clock_timestamp()-interval '1 second'")
            )
        runtime_signals.renewal_failures = 0
        async with SimulationTransport("http://127.0.0.1:1") as transport:
            worker = Worker(executor, transport, holder="failed-renewals", heartbeat_seconds=0.001)
            for _ in range(3):
                assert not await worker._heartbeat(lease)
        assert any(getattr(r, "alert_id", None) == "lease.renewal_failed" for r in caplog.records)
        assert runtime_signals.renewal_failures == 3
        async with engine.connect() as c:
            assert await c.scalar(text("SELECT lease_until < clock_timestamp() FROM steps"))
    finally:
        runtime_signals.renewal_failures = 0
        await engine.dispose()
