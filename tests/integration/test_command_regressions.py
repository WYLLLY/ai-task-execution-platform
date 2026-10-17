"""Review regressions: write first, prove failure, then repair the implementation."""

import asyncio

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine

from .conftest import settings_for
from .test_commands import create

pytestmark = pytest.mark.integration


async def test_cancel_samples_time_after_waiting_for_children(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    task = await create(client, command_owner)
    engine = create_engine(settings_for("migrator"))
    pending = None
    try:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE tasks SET deadline_at=clock_timestamp()+interval '450 milliseconds' "
                    "WHERE id=:id"
                ),
                {"id": task["task_id"]},
            )
        async with engine.begin() as holder:
            await holder.execute(
                text("SELECT id FROM steps WHERE task_id=:id FOR UPDATE"), {"id": task["task_id"]}
            )
            pending = asyncio.create_task(
                client.post(
                    f"/v1/tasks/{task['task_id']}/cancel",
                    json={"expected_task_version": 1},
                    headers={**command_owner, "Idempotency-Key": "deadline-wait"},
                )
            )
            async with asyncio.timeout(0.8), admin_engine.connect() as observer:
                await observer.execution_options(isolation_level="AUTOCOMMIT")
                while not await observer.scalar(  # noqa: ASYNC110 -- observe PostgreSQL time/locks
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE usename='atp_api' "
                        "AND cardinality(pg_blocking_pids(pid))>0"
                    )
                ):
                    await asyncio.sleep(0.005)
                # Database time establishes deadline crossing, rather than a guessed sleep.
                while not await observer.scalar(  # noqa: ASYNC110 -- observe PostgreSQL time/locks
                    text("SELECT clock_timestamp()>=deadline_at FROM tasks WHERE id=:id"),
                    {"id": task["task_id"]},
                ):
                    await asyncio.sleep(0.005)
        response = await pending
        assert response.status_code == 200, response.text
        assert response.json()["state"] == "timed_out"
    finally:
        if pending is not None and not pending.done():
            pending.cancel()
            await asyncio.gather(pending, return_exceptions=True)
        await engine.dispose()


async def test_terminal_cancel_new_key_conflicts_but_original_replays(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    task = await create(client, command_owner)
    route = f"/v1/tasks/{task['task_id']}/cancel"
    headers = {**command_owner, "Idempotency-Key": "original"}
    first = await client.post(route, json={"expected_task_version": 1}, headers=headers)
    assert first.status_code == 200
    fresh = await client.post(
        route,
        json={"expected_task_version": 2},
        headers={**command_owner, "Idempotency-Key": "fresh"},
    )
    assert fresh.status_code == 409
    replay = await client.post(route, json={"expected_task_version": 1}, headers=headers)
    assert replay.content == first.content and replay.status_code == 200
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM idempotency_records")) == 2
        assert await c.scalar(text("SELECT count(*) FROM task_events")) == 2
        assert await c.scalar(text("SELECT open_tasks FROM platform_control")) == 0


@pytest.mark.parametrize(
    "privilege,table", [("INSERT", "tasks"), ("UPDATE (open_tasks)", "principals")]
)
async def test_readiness_detects_missing_command_privileges(
    client: AsyncClient, command_owner: dict[str, str], privilege: str, table: str
) -> None:
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(text(f"REVOKE {privilege} ON {table} FROM atp_api"))
        response = await client.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["submission_available"] is False
    finally:
        async with engine.begin() as c:
            await c.execute(text(f"GRANT {privilege} ON {table} TO atp_api"))
        await engine.dispose()
