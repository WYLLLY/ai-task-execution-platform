"""Real PostgreSQL A/heartbeat contracts, with independent worker connections."""

import asyncio
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.execution import Executor

from .conftest import settings_for
from .test_commands import create

pytestmark = pytest.mark.integration


@pytest.fixture
async def executor() -> AsyncIterator[Executor]:
    settings = settings_for("worker")
    engine = create_engine(settings)
    control = create_engine(settings, control=True)
    try:
        yield Executor(sessions(engine), sessions(control), settings.execution_epoch)
    finally:
        await engine.dispose()
        await control.dispose()


async def test_claim_and_heartbeat_are_durable_without_business_version_change(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    item = await create(client, command_owner)
    lease = await executor.claim("worker-a")
    assert lease is not None
    assert lease.task_id == UUID(str(item["task_id"]))
    assert lease.generation == 1
    async with admin_engine.connect() as c:
        before = (await c.execute(text("SELECT task_version,event_sequence FROM tasks"))).one()
        expiry = await c.scalar(text("SELECT lease_until FROM steps"))
        assert await c.scalar(text("SELECT state FROM attempts")) == "active"
    assert await executor.heartbeat(lease)
    async with admin_engine.connect() as c:
        assert (
            await c.execute(text("SELECT task_version,event_sequence FROM tasks"))
        ).one() == before
        assert await c.scalar(text("SELECT lease_until FROM steps")) > expiry
    assert await executor.claim("worker-b") is None


async def test_concurrent_claim_has_one_owner(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
) -> None:
    await create(client, command_owner)
    leases = await asyncio.gather(*(executor.claim(f"worker-{i}") for i in range(8)))
    assert sum(lease is not None for lease in leases) == 1


async def test_claim_skip_locked_moves_to_next_task(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
) -> None:
    first = await create(client, command_owner, "first")
    second = await create(client, command_owner, "second")
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text("SELECT id FROM tasks WHERE id=:id FOR UPDATE"), {"id": first["task_id"]}
            )
            lease = await asyncio.wait_for(executor.claim("other"), 0.8)
            assert lease is not None and str(lease.task_id) == second["task_id"]
    finally:
        await engine.dispose()


async def test_expired_heartbeat_cannot_resurrect_and_stale_holder_cannot_renew(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
) -> None:
    from dataclasses import replace

    await create(client, command_owner)
    lease = await executor.claim("worker-a")
    assert lease is not None
    assert not await executor.heartbeat(replace(lease, holder="forged"))
    assert not await executor.heartbeat(replace(lease, epoch=uuid4()))
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE steps SET lease_until=clock_timestamp()-interval '1 second'")
            )
        assert not await executor.heartbeat(lease)
    finally:
        await engine.dispose()


async def test_heartbeat_uses_control_pool_when_execution_pool_full(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker-a")
    assert lease is not None
    async with (
        executor.factory() as a,
        executor.factory() as b,
        executor.factory() as c,
        executor.factory() as d,
    ):
        for session in (a, b, c, d):
            await session.execute(text("SELECT 1"))
        assert await asyncio.wait_for(executor.heartbeat(lease), 0.8)


async def test_claim_never_runs_expired_or_cancelled_task(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
) -> None:
    item = await create(client, command_owner)
    assert (
        await client.post(
            f"/v1/tasks/{item['task_id']}/cancel",
            json={"expected_task_version": 1},
            headers={**command_owner, "Idempotency-Key": "cancel"},
        )
    ).status_code == 200
    assert await executor.claim("worker") is None


async def test_heartbeat_fresh_time_after_observed_lock_wait(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease is not None
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE steps SET lease_until=clock_timestamp()+interval '350 milliseconds'")
            )
        async with engine.begin() as c:
            await c.execute(text("SELECT id FROM tasks FOR UPDATE"))
            waiting = asyncio.create_task(executor.heartbeat(lease))
            async with admin_engine.connect() as observer:
                await observer.execution_options(isolation_level="AUTOCOMMIT")
                for _ in range(100):
                    blocked = await observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE usename='atp_worker' "
                            "AND cardinality(pg_blocking_pids(pid))>0"
                        )
                    )
                    if blocked:
                        break
                    await asyncio.sleep(0.005)
                assert blocked
            await asyncio.sleep(timedelta(milliseconds=400).total_seconds())
        assert not await waiting
    finally:
        await engine.dispose()
