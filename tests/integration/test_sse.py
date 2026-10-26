"""Owner isolation, shared polling and notification loss on real infrastructure."""

import asyncio
from typing import cast
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.pool import AsyncAdaptedQueuePool

from task_platform.db import create_engine, sessions
from task_platform.event_hub import Hub
from task_platform.event_reads import read_events
from task_platform.identity import Identity, issue_key, provision_principal

from .conftest import settings_for
from .test_commands import create

pytestmark = pytest.mark.integration


async def test_event_endpoint_authorizes_before_validating_cursor(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    item = await create(client, command_owner)
    url = f"/v1/tasks/{item['task_id']}/events"
    assert (await client.get(url)).status_code == 401
    owner = await provision_principal(sessions(admin_engine), "foreign SSE")
    _, token = await issue_key(sessions(admin_engine), owner)
    assert (
        await client.get(url + "?after=bad", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 404
    for cursor in ("bad", f"{uuid4()}:0", f"{item['task_id']}:2"):
        assert (
            await client.get(url, params={"after": cursor}, headers=command_owner)
        ).status_code == 400
    response = await client.get(
        url,
        params={"after": f"{item['task_id']}:0"},
        headers={**command_owner, "Last-Event-ID": f"{item['task_id']}:1"},
    )
    assert response.status_code == 400


async def test_shared_polling_and_cache_release_connections_while_waiting(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    item = await create(client, command_owner)
    task_id = UUID(str(item["task_id"]))
    async with admin_engine.connect() as c:
        owner, key = (await c.execute(text("SELECT principal_id,key_id FROM tasks"))).one()
    identity = Identity(owner, key)
    engine = create_engine(settings_for("api"))
    hub = Hub(sessions(engine))
    queries: list[str] = []

    def record(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if "FROM tasks" in statement:
            queries.append(statement)

    event.listen(engine.sync_engine, "before_cursor_execute", record)
    try:
        initial = await read_events(sessions(engine), identity, task_id)
        tickets = [hub.subscribe(task_id, identity, initial) for _ in range(5)]
        queries.clear()
        batches = await asyncio.gather(*(hub.batch(identity, task_id, 0) for _ in range(5)))
        assert all([e.sequence for e in b.events] == [1] for b in batches)
        assert len(queries) <= 2, "one replay query, at most one shared metadata poll"
        await asyncio.sleep(1.15)
        assert len(queries) <= 4, "polling must be per task, not per subscriber"
        assert cast(AsyncAdaptedQueuePool, engine.pool).checkedout() == 0
        for release in tickets:
            release()
        await asyncio.sleep(0)
        assert hub.followed_tasks == 0
    finally:
        await hub.close()
        await engine.dispose()


async def test_retention_before_headers_returns_owned_snapshot_url(
    client: AsyncClient, command_owner: dict[str, str]
) -> None:
    item = await create(client, command_owner)
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(text("UPDATE tasks SET oldest_retained_sequence=2"))
            await c.execute(text("DELETE FROM task_events"))
        response = await client.get(f"/v1/tasks/{item['task_id']}/events", headers=command_owner)
        assert response.status_code == 410
        assert response.json()["snapshot_url"] == f"/v1/tasks/{item['task_id']}"
    finally:
        await engine.dispose()
