"""F04/F05/F08: actual worker process death, including committed/lost acknowledgements."""

import asyncio
import os
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
from .test_execution_transport import Simulator
from .test_execution_transport import simulator as simulator

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "phase,requests,state",
    [
        ("a", 0, "running"),
        ("b", 0, "suspended"),
        ("b_ack", 0, "suspended"),
        ("c", 1, "succeeded"),
        ("c_ack", 1, "succeeded"),
    ],
)
async def test_real_process_crash_and_lost_ack(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    simulator: Simulator,
    admin_engine: AsyncEngine,
    phase: str,
    requests: int,
    state: str,
) -> None:
    await create(client, command_owner)
    env = os.environ.copy()
    env["DATABASE_URL_FILE"] = str(Path(env["TEST_SECRETS_DIR"]) / "worker_database_url")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.crash_worker",
        phase,
        simulator.url,
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await asyncio.wait_for(process.communicate(), 10)
    assert process.returncode == 77, stderr.decode()
    assert simulator.count("request") == simulator.count("effect") == requests
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE steps SET lease_until=clock_timestamp()-interval '1 second' "
                    "WHERE state='running'"
                )
            )
        await executor.maintenance()
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT state FROM tasks")) == state
            assert await c.scalar(text("SELECT count(*) FROM attempts WHERE state='active'")) == 0
            assert await c.scalar(text("SELECT count(*) FROM external_calls")) == (phase != "a")
            if phase in {"b", "b_ack"}:
                assert await c.scalar(text("SELECT exposure_cost FROM call_costs")) == 4004
            if phase in {"c", "c_ack"}:
                assert await c.scalar(text("SELECT confirmed_cost FROM call_costs")) == 20
                assert await c.scalar(text("SELECT exposure_cost FROM call_costs")) == 0
                assert (
                    await c.scalar(
                        text("SELECT count(*) FROM task_events WHERE kind='task.succeeded'")
                    )
                    == 1
                )
    finally:
        await engine.dispose()


@pytest.mark.parametrize("failure", ["loop", "cleanup"])
async def test_supervisor_stops_unresponsive_worker_process(
    client: AsyncClient,
    command_owner: dict[str, str],
    simulator: Simulator,
    failure: str,
) -> None:
    await create(client, command_owner)
    env = os.environ.copy()
    env.update(
        DATABASE_URL_FILE=str(Path(env["TEST_SECRETS_DIR"]) / "worker_database_url"),
        ATP_TEST_PROFILE="1",
        DISPATCH_ENABLED="true",
        SIMULATOR_URL=simulator.url,
        TEST_STALL=failure,
    )
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.unresponsive_worker",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        # Separate bounded process/import startup from the five-second fault grace.
        assert process.stdout is not None
        assert await asyncio.wait_for(process.stdout.readline(), 10) == b"fault-ready\n"
        try:
            await asyncio.wait_for(process.communicate(), 6)
        except TimeoutError:
            pytest.fail(
                "Worker process did not terminate under independent supervision; "
                f"exit={process.returncode}, requests={simulator.count('request')}"
            )
        assert process.returncode != 0
        assert simulator.count("request") == simulator.count("effect") == 1
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


async def test_b_db_measurement_deducts_elapsed_transaction_time(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    simulator: Simulator,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime
    from uuid import uuid4

    from sqlalchemy.ext.asyncio import AsyncSession

    import task_platform.execution as execution_module
    from task_platform.execution_models import ProviderAdmission
    from task_platform.task_models import Task
    from task_platform.transport import SimulationTransport

    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease
    from task_platform.accounting import admission_reason

    original = admission_reason

    async def delayed(
        session: AsyncSession,
        task: Task,
        provider: ProviderAdmission,
        now: datetime,
        cost: int,
        tokens: int,
    ) -> str | None:
        await asyncio.sleep(0.3)
        return await original(session, task, provider, now, cost, tokens)

    monkeypatch.setattr(execution_module, "admission_reason", delayed)
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE tasks SET deadline_at=clock_timestamp()+interval '200 milliseconds'")
            )
        permit = await executor.authorize(lease, uuid4(), b"{}")
        if permit is not None:
            async with SimulationTransport(simulator.url) as transport:
                assert (await transport.send(permit)).outcome == "unknown"
        assert simulator.count("request") == 0
    finally:
        await engine.dispose()


@pytest.mark.parametrize("change", ["revoke", "cancel", "expire"])
async def test_f06_authorized_first_send_can_arrive_late_but_c_cannot_progress(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    simulator: Simulator,
    admin_engine: AsyncEngine,
    change: str,
) -> None:
    from uuid import uuid4

    from task_platform.db import sessions
    from task_platform.identity import revoke_key
    from task_platform.transport import SimulationTransport

    item = await create(client, command_owner)
    lease = await executor.claim("old-worker")
    assert lease
    call = uuid4()
    permit = await executor.authorize(lease, call, b"{}")
    assert permit
    engine = create_engine(settings_for("migrator"))
    try:
        if change == "revoke":
            async with admin_engine.connect() as c:
                owner, key = (await c.execute(text("SELECT principal_id,key_id FROM tasks"))).one()
            await revoke_key(sessions(admin_engine), owner, key)
        if change == "cancel":
            snapshot = (
                await client.get(f"/v1/tasks/{item['task_id']}", headers=command_owner)
            ).json()
            assert (
                await client.post(
                    f"/v1/tasks/{item['task_id']}/cancel",
                    json={"expected_task_version": snapshot["task_version"]},
                    headers={**command_owner, "Idempotency-Key": "cancel"},
                )
            ).status_code == 200
        else:
            async with engine.begin() as c:
                await c.execute(
                    text("UPDATE steps SET lease_until=clock_timestamp()-interval '1 second'")
                )
            await executor.maintenance()
        async with SimulationTransport(simulator.url) as transport:
            response = await transport.send(permit)
        assert response.outcome == "succeeded"
        assert simulator.count("request") == simulator.count("effect") == 1
        assert not await executor.complete(lease, call, "succeeded", cost=20, tokens=10)
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT result FROM tasks")) is None
            assert await c.scalar(text("SELECT confirmed_cost FROM call_costs")) == 20
    finally:
        await engine.dispose()
