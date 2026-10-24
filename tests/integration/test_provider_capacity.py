"""F16 shared provider windows survive concurrent owners and worker replacement."""

import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.execution import Executor
from task_platform.execution_models import ProviderAdmission
from task_platform.identity import issue_key, provision_principal
from task_platform.provider_admin import configure_provider
from task_platform.responses import validate_profile
from tests.unit.test_responses import profile_data

from .conftest import settings_for
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch
from .test_responses_workflow import submit, worker

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "limit", ["requests_per_minute", "max_active", "global_daily_cost", "global_daily_tokens"]
)
async def test_concurrent_owner_reservations_are_serialized(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
    limit: str,
) -> None:
    await configure_provider(sessions(admin_engine), validate_profile(profile_data()))
    owner = await provision_principal(sessions(admin_engine), "Second M5 owner")
    _, token = await issue_key(sessions(admin_engine), owner)
    other = {**command_owner, "Authorization": f"Bearer {token}"}
    await submit(client, command_owner)
    await submit(client, other)
    service = worker(executor)
    leases = [await service.claim("one"), await service.claim("two")]
    assert all(leases)
    first, second = leases
    assert first and second
    payload1, payload2 = await service.context(first), await service.context(second)
    assert payload1 and payload2
    from task_platform.responses import estimate_input, price

    profile = validate_profile(profile_data())
    amounts = {
        "requests_per_minute": 1,
        "max_active": 1,
        "global_daily_cost": price(profile, estimate_input(payload1), 2000, 0),
        "global_daily_tokens": estimate_input(payload1) + 2000,
    }
    async with admin_engine.begin() as c:
        # The column is exclusively selected by the fixed parametrization.
        await c.execute(update(ProviderAdmission).values({limit: amounts[limit]}))
    # Observe actual PostgreSQL contention behind the global admission lock.
    async with admin_engine.begin() as blocker:
        await blocker.execute(text("SELECT id FROM platform_control FOR UPDATE"))
        calls = [
            asyncio.create_task(service.authorize(first, uuid4(), payload1)),
            asyncio.create_task(service.authorize(second, uuid4(), payload2)),
        ]
        async with asyncio.timeout(0.9), admin_engine.connect() as observer:
            await observer.execution_options(isolation_level="AUTOCOMMIT")
            for _ in range(150):
                if (
                    await observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE usename='atp_worker' "
                            "AND cardinality(pg_blocking_pids(pid))>0"
                        )
                    )
                    == 2
                ):
                    break
                await asyncio.sleep(0.005)
            else:
                pytest.fail("Expected two actual PostgreSQL lock waiters")
    permits = await asyncio.gather(*calls)
    assert sum(p is not None for p in permits) == 1
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 1
        assert await c.scalar(text("SELECT count(*) FROM call_costs")) == 1
        assert await c.scalar(text("SELECT count(*) FROM steps WHERE state='retry_wait'")) == 1


async def test_shared_429_cooldown_and_waiting_deadline_survive_restart(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    await configure_provider(sessions(admin_engine), validate_profile(profile_data()))
    await submit(client, command_owner)
    service = worker(executor)
    lease = await service.claim("original")
    assert lease
    payload = await service.context(lease)
    assert payload
    call = uuid4()
    assert await service.authorize(lease, call, payload)
    assert await service.complete(lease, call, "unknown", retry_after_seconds=30)
    await submit(client, command_owner, "new-task")
    replacement = worker(executor)
    waiting = await replacement.claim("restart")
    assert waiting
    next_payload = await replacement.context(waiting)
    assert next_payload and await replacement.authorize(waiting, uuid4(), next_payload) is None
    async with admin_engine.connect() as c:
        before = await c.scalar(text("SELECT next_allowed_at FROM provider_admission"))
    await configure_provider(sessions(admin_engine), validate_profile(profile_data()))
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT next_allowed_at FROM provider_admission")) == before
        assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 1
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE tasks SET deadline_at=clock_timestamp()+interval '100 milliseconds'")
            )
        async with engine.connect() as c:
            await c.execute(text("SELECT pg_sleep(0.15)"))
        await replacement.maintenance()
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT count(*) FROM tasks WHERE state='timed_out'")) == 2
            assert (
                await c.scalar(text("SELECT exposure_cost=reserved_cost FROM call_costs")) is True
            )
    finally:
        await engine.dispose()
