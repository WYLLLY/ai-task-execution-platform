"""Global G1: B must not authorize after validation/flush waits consume its authority."""

import asyncio
from typing import cast
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession
from sqlalchemy.util.concurrency import await_only

from task_platform.db import create_engine
from task_platform.execution import Executor
from task_platform.task_models import Step, Task
from task_platform.transport import SimulationTransport

from .conftest import settings_for
from .test_commands import create
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch
from .test_execution_transport import Simulator
from .test_execution_transport import simulator as simulator

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("expiry", ["lease", "deadline", "key"])
@pytest.mark.parametrize("wait", ["validation", "insert_flush"])
async def test_authorization_rechecks_fresh_authority_after_wait(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
    simulator: Simulator,
    monkeypatch: pytest.MonkeyPatch,
    expiry: str,
    wait: str,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("fresh-B")
    assert lease
    engine = create_engine(settings_for("migrator"))
    statements = {
        "lease": "UPDATE steps SET lease_until=clock_timestamp()+interval '150 milliseconds'",
        "deadline": "UPDATE tasks SET deadline_at=clock_timestamp()+interval '150 milliseconds'",
        "key": "UPDATE api_keys SET expires_at=clock_timestamp()+interval '150 milliseconds' "
        "WHERE id=(SELECT key_id FROM tasks)",
    }
    try:
        async with engine.begin() as c:
            await c.execute(text(statements[expiry]))
        original = executor.authorization_reason

        async def delayed(
            session: AsyncSession, task: Task, step: Step, payload: bytes
        ) -> str | None:
            result = await original(session, task, step, payload)
            await asyncio.sleep(0.25)
            return result

        def blocked(
            connection: object,
            cursor: object,
            statement: str,
            parameters: object,
            context: object,
            executemany: bool,
        ) -> None:
            if statement.startswith("INSERT INTO external_calls"):
                await_only(asyncio.sleep(0.25))

        binding = cast(AsyncEngine, executor.factory.kw["bind"])
        if wait == "validation":
            monkeypatch.setattr(executor, "authorization_reason", delayed)
        else:
            event.listen(binding.sync_engine, "before_cursor_execute", blocked)
        try:
            permit = await executor.authorize(lease, uuid4(), b"{}")
            if permit is not None:
                async with SimulationTransport(simulator.url) as transport:
                    await transport.send(permit)
            assert permit is None, "expired authority cannot create a fresh transmission permission"
            assert simulator.count("request") == simulator.count("effect") == 0
            async with admin_engine.connect() as c:
                assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 0
                assert await c.scalar(text("SELECT count(*) FROM call_costs")) == 0
                assert (
                    await c.scalar(
                        text("SELECT count(*) FROM task_events WHERE kind='call.authorized'")
                    )
                    == 0
                )
        finally:
            if wait != "validation":
                event.remove(binding.sync_engine, "before_cursor_execute", blocked)
    finally:
        await engine.dispose()
