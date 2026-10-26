"""M6 read projections use real PostgreSQL and owner-scoped snapshots."""

import json
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.event_cursor import HistoryExpired
from task_platform.event_reads import read_events
from task_platform.execution import Executor
from task_platform.identity import Identity, NotFoundError

from .conftest import settings_for
from .test_commands import create
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch

pytestmark = pytest.mark.integration


async def test_snapshot_contains_costs_and_no_private_payload(
    client: AsyncClient, command_owner: dict[str, str], executor: Executor
) -> None:
    item = await create(client, command_owner)
    lease = await executor.claim("snapshot")
    assert lease
    call = uuid4()
    assert await executor.authorize(lease, call, b'{"private":"source-canary"}')
    response = await client.get(f"/v1/tasks/{item['task_id']}", headers=command_owner)
    costs = response.json().get("costs")
    assert costs is not None, "atomic snapshot must include accounting"
    assert costs["confirmed_micro_usd"] == 0 and costs["uncertain_micro_usd"] > 0
    assert await executor.settle(call, "observed", 20, 10, 0)
    settled = (await client.get(f"/v1/tasks/{item['task_id']}", headers=command_owner)).json()
    assert settled["costs"]["confirmed_micro_usd"] == 20
    assert settled["costs"]["uncertain_micro_usd"] == 0
    assert settled["event_sequence"] > response.json()["event_sequence"]
    assert "source-canary" not in json.dumps(settled)


async def test_batches_are_contiguous_bounded_and_owner_checked_before_cursor(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    item = await create(client, command_owner)
    task_id = UUID(str(item["task_id"]))
    async with admin_engine.connect() as c:
        owner, key = (await c.execute(text("SELECT principal_id,key_id FROM tasks"))).one()
    identity = Identity(owner, key)
    engine = create_engine(settings_for("api"))
    try:
        batch = await read_events(sessions(engine), identity, task_id, after=None)
        assert batch.watermark == 1
        assert batch.position == 0
        assert [row.sequence for row in batch.events] == [1]
        assert b"event: task.created\n" in batch.events[0].wire
        assert sum(len(row.wire) for row in batch.events) <= 256 * 1024
        with pytest.raises(NotFoundError):
            await read_events(sessions(engine), Identity(uuid4(), key), task_id, after="bad")
        # Retention is modified only by the migration role in this isolated test.
        migrator = create_engine(settings_for("migrator"))
        try:
            async with migrator.begin() as c:
                await c.execute(text("UPDATE tasks SET oldest_retained_sequence=2"))
                await c.execute(text("DELETE FROM task_events"))
            with pytest.raises(HistoryExpired):
                await read_events(sessions(engine), identity, task_id, after=None)
            empty = await read_events(sessions(engine), identity, task_id, after=f"{task_id}:1")
            assert not empty.events and empty.oldest == 2
        finally:
            await migrator.dispose()
    finally:
        await engine.dispose()


async def test_unexpected_hole_is_not_silently_skipped(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    from task_platform.event_reads import EventInvariantError

    item = await create(client, command_owner)
    async with admin_engine.connect() as c:
        owner, key = (await c.execute(text("SELECT principal_id,key_id FROM tasks"))).one()
    migrator = create_engine(settings_for("migrator"))
    engine = create_engine(settings_for("api"))
    try:
        async with migrator.begin() as c:
            await c.execute(text("DELETE FROM task_events"))
        with pytest.raises(EventInvariantError):
            await read_events(sessions(engine), Identity(owner, key), UUID(str(item["task_id"])))
    finally:
        await engine.dispose()
        await migrator.dispose()
