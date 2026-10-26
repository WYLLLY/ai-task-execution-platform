"""B/C simulated accounting and recovery contracts; no paid provider traffic."""

import asyncio
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.execution import Executor
from task_platform.execution_models import ProviderAdmission
from task_platform.identity import revoke_key

from .conftest import settings_for
from .test_commands import create
from .test_execution import executor as executor

pytestmark = pytest.mark.integration


@pytest.fixture(autouse=True)
async def dispatch(admin_engine: AsyncEngine) -> AsyncIterator[None]:
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE platform_control SET dispatch_open=true WHERE id=1"))
    yield
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE platform_control SET dispatch_open=false WHERE id=1"))
        await c.execute(
            text(
                "UPDATE provider_admission SET requests_per_minute=100, "
                "tokens_per_minute=1000000,max_active=4,max_owner_active=2,"
                "global_daily_cost=100000000,owner_daily_cost=10000000,"
                "global_daily_tokens=4000000,owner_daily_tokens=400000,next_allowed_at='2000-01-01T00:00:00Z'"
            )
        )


async def test_b_reserves_exact_payload_once_and_never_reissues_permission(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease is not None
    call_id = uuid4()
    permit = await executor.authorize(lease, call_id, b'{"synthetic":true}')
    assert permit is not None and permit.payload == b'{"synthetic":true}'
    assert await executor.authorize(lease, call_id, b'{"synthetic":true}') is None
    assert await executor.authorize(lease, uuid4(), b'{"synthetic":true}') is None
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 1
        assert await c.scalar(text("SELECT count(*) FROM call_costs")) == 1
        row = (
            await c.execute(
                text(
                    "SELECT reserved_cost,exposure_cost,confirmed_cost,"
                    "reserved_tokens,exposure_tokens FROM call_costs"
                )
            )
        ).one()
        assert row == (4036, 4036, 0, 2018, 2018)


async def test_revocation_committed_before_b_blocks_dispatch(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease is not None
    async with admin_engine.connect() as c:
        owner, key = (await c.execute(text("SELECT principal_id,key_id FROM tasks"))).one()
    await revoke_key(sessions(admin_engine), owner, key)
    assert await executor.authorize(lease, uuid4(), b"{}") is None
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 0
        assert await c.scalar(text("SELECT state FROM tasks")) == "suspended"
        assert await c.scalar(text("SELECT state FROM attempts")) == "revoked"


@pytest.mark.parametrize(
    "setting,value",
    [
        ("max_active", 0),
        ("max_owner_active", 0),
        ("requests_per_minute", 0),
        ("tokens_per_minute", 2001),
        ("global_daily_cost", 4003),
        ("owner_daily_cost", 4003),
        ("global_daily_tokens", 2001),
        ("owner_daily_tokens", 2001),
    ],
)
async def test_b_shared_capacity_denial_does_not_consume_call(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
    setting: str,
    value: int,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease is not None
    async with admin_engine.begin() as c:
        # Setting comes exclusively from the fixed parametrization above.
        await c.execute(update(ProviderAdmission).values({setting: value}))
    assert await executor.authorize(lease, uuid4(), b"{}") is None
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 0
        assert await c.scalar(text("SELECT state FROM steps")) == "retry_wait"
        assert await c.scalar(text("SELECT reason FROM attempts")) == "capacity_wait"
        assert await c.scalar(text("SELECT available_at>clock_timestamp() FROM steps"))


@pytest.mark.parametrize("limits", [{"cost_micro_usd": 4003}, {"tokens": 2001}, {"llm_calls": 0}])
async def test_b_task_limits_fail_without_dispatch(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
    limits: dict[str, int],
) -> None:
    response = await client.post(
        "/v1/tasks",
        headers={**command_owner, "Idempotency-Key": "limit"},
        json={
            "workflow": {"name": "analysis", "version": "1"},
            "input": {"texts": ["synthetic"]},
            "limits": limits,
        },
    )
    assert response.status_code == 202
    lease = await executor.claim("worker")
    assert lease is not None
    assert await executor.authorize(lease, uuid4(), b"{}") is None
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "failed"
        assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 0
        assert await c.scalar(text("SELECT open_tasks FROM platform_control")) == 0


async def test_concurrent_b_respects_last_slot(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    await create(client, command_owner, "a")
    await create(client, command_owner, "b")
    leases = await asyncio.gather(executor.claim("a"), executor.claim("b"))
    assert all(leases)
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE provider_admission SET max_active=1"))
    permits = await asyncio.gather(
        *(executor.authorize(lease, uuid4(), b"{}") for lease in leases if lease)
    )
    assert sum(p is not None for p in permits) == 1
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 1


async def test_c_success_is_atomic_and_repeated_ack_is_not_duplicate(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease is not None
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    assert await executor.complete(lease, call, "succeeded", cost=20, tokens=10)
    assert await executor.complete(lease, call, "succeeded", cost=20, tokens=10)
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "succeeded"
        assert await c.scalar(text("SELECT state FROM attempts")) == "succeeded"
        assert await c.scalar(text("SELECT open_tasks FROM platform_control")) == 0
        assert (
            await c.execute(text("SELECT confirmed_cost,exposure_cost,revision FROM call_costs"))
        ).one() == (20, 0, 1)
        assert (
            await c.scalar(text("SELECT count(*) FROM task_events WHERE kind='task.succeeded'"))
            == 1
        )
        assert await c.scalar(text("SELECT count(*) FROM cost_evidence")) == 1


@pytest.mark.parametrize("after_b", [False, True])
async def test_crash_recovery_distinguishes_before_and_after_b(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
    after_b: bool,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("lost-worker")
    assert lease is not None
    if after_b:
        assert await executor.authorize(lease, uuid4(), b"{}")
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE steps SET lease_until=clock_timestamp()-interval '1 second'")
            )
        await asyncio.gather(executor.maintenance(), executor.maintenance())
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT state FROM attempts")) == "expired"
            assert await c.scalar(text("SELECT state FROM steps")) == (
                "unknown" if after_b else "retry_wait"
            )
            assert await c.scalar(text("SELECT state FROM tasks")) == (
                "suspended" if after_b else "running"
            )
            assert await c.scalar(text("SELECT count(*) FROM external_calls")) == int(after_b)
            if after_b:
                assert await c.scalar(text("SELECT exposure_cost FROM call_costs")) == 4004
                assert not await c.scalar(text("SELECT local_active FROM external_calls"))
        assert not await executor.heartbeat(lease)
        assert await executor.authorize(lease, uuid4(), b"{}") is None
    finally:
        await engine.dispose()


@pytest.mark.parametrize("terminal", ["cancelled", "timed_out"])
async def test_terminal_cleanup_retains_provision_and_rejects_stale_c(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
    terminal: str,
) -> None:
    item = await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease is not None
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    if terminal == "cancelled":
        state = (await client.get(f"/v1/tasks/{item['task_id']}", headers=command_owner)).json()
        assert (
            await client.post(
                f"/v1/tasks/{item['task_id']}/cancel",
                json={"expected_task_version": state["task_version"]},
                headers={**command_owner, "Idempotency-Key": "cancel"},
            )
        ).status_code == 200
    else:
        engine = create_engine(settings_for("migrator"))
        try:
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "UPDATE tasks SET created_at=clock_timestamp()-interval '2 minutes',"
                        "deadline_at=clock_timestamp()-interval '1 minute'"
                    )
                )
            await executor.maintenance()
        finally:
            await engine.dispose()
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == terminal
        assert await c.scalar(text("SELECT state FROM external_calls")) == "unknown"
        assert await c.scalar(text("SELECT exposure_cost FROM call_costs")) == 4004
        assert not await c.scalar(text("SELECT local_active FROM external_calls"))
    assert not await executor.complete(lease, call, "succeeded", cost=20, tokens=10)
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == terminal
        assert await c.scalar(text("SELECT result FROM tasks")) is None
        assert await c.scalar(text("SELECT confirmed_cost FROM call_costs")) == 20


async def test_unknown_outcome_retains_provision_and_stops_automatic_claim(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease is not None
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    assert await executor.complete(lease, call, "unknown")
    await executor.maintenance()
    assert await executor.claim("replacement") is None
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "suspended"
        assert await c.scalar(text("SELECT exposure_cost FROM call_costs")) == 4004


async def test_concurrent_settlement_is_idempotent_conflict_detected(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease is not None
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    results = await asyncio.gather(*(executor.settle(call, "proof-1", 20, 10, 0) for _ in range(6)))
    assert all(results)
    with pytest.raises(ValueError, match="conflict"):
        await executor.settle(call, "proof-1", 22, 10, 0)
    async with admin_engine.connect() as c:
        assert (
            await c.execute(text("SELECT confirmed_cost,exposure_cost,revision FROM call_costs"))
        ).one() == (20, 0, 1)
        assert await c.scalar(text("SELECT count(*) FROM cost_evidence")) == 1


async def test_partial_usage_retains_exposure_overrun_recorded_in_full(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    await create(client, command_owner, "first")
    lease = await executor.claim("worker")
    assert lease is not None
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    assert await executor.settle(call, "partial", 4, 2, 0, final=False)
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT exposure_cost FROM call_costs")) == 4004
    assert await executor.settle(call, "final", 5000, 2500, 0)
    await create(client, command_owner, "second")
    other = await executor.claim("worker-2")
    assert other is not None
    assert await executor.authorize(other, uuid4(), b"{}") is None
    async with admin_engine.connect() as c:
        assert (
            await c.execute(text("SELECT confirmed_cost,exposure_cost,overrun FROM call_costs"))
        ).one() == (5000, 0, True)


async def test_prior_matching_settlement_does_not_prevent_c(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    assert await executor.settle(call, "independent-proof", 20, 10, 0)
    assert await executor.complete(lease, call, "succeeded", cost=20, tokens=10)
    assert await executor.complete(lease, call, "succeeded", cost=20, tokens=10)


@pytest.mark.parametrize("scope", ["principals", "platform_control"])
async def test_b_storage_admission_is_atomic(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
    scope: str,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease
    from task_platform.models import PlatformControl, Principal

    model = Principal if scope == "principals" else PlatformControl
    async with admin_engine.begin() as c:
        await c.execute(update(model).values(storage_bytes=model.max_storage_bytes - 1))
    assert await executor.authorize(lease, uuid4(), b"{}") is None
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 0
        assert await c.scalar(text("SELECT count(*) FROM call_costs")) == 0


async def test_b_charges_payload_storage_once(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease
    async with admin_engine.connect() as c:
        before = await c.scalar(text("SELECT storage_bytes FROM platform_control"))
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    assert await executor.authorize(lease, call, b"{}") is None
    async with admin_engine.connect() as c:
        assert (
            await c.scalar(text("SELECT storage_bytes FROM platform_control")) == before + 2 + 2048
        )
        assert await c.scalar(text("SELECT storage_bytes FROM tasks")) == before + 2 + 2048


async def test_c_rechecks_deadline_after_cost_lock_wait(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE tasks SET deadline_at=clock_timestamp()+interval '350 milliseconds'")
            )
        async with engine.begin() as c:
            await c.execute(text("SELECT call_id FROM call_costs FOR UPDATE"))
            completing = asyncio.create_task(
                executor.complete(lease, call, "succeeded", cost=20, tokens=10)
            )
            async with asyncio.timeout(0.8), admin_engine.connect() as observer:
                await observer.execution_options(isolation_level="AUTOCOMMIT")
                for query in (
                    "SELECT count(*) FROM pg_stat_activity WHERE usename='atp_worker' "
                    "AND cardinality(pg_blocking_pids(pid))>0",
                    "SELECT clock_timestamp()>=deadline_at FROM tasks",
                ):
                    observed = False
                    for _ in range(150):
                        if await observer.scalar(text(query)):
                            observed = True
                            break
                        await asyncio.sleep(0.005)
                    assert observed
        assert not await completing
        await executor.maintenance()
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT state FROM tasks")) == "timed_out"
            assert await c.scalar(text("SELECT confirmed_cost FROM call_costs")) == 20
    finally:
        await engine.dispose()


async def test_utc_rollover_counts_old_exposure_but_not_old_confirmed_charge(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    from datetime import timedelta

    from sqlalchemy import select

    from task_platform.accounting import admission_reason
    from task_platform.execution import db_now
    from task_platform.task_models import Task

    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    assert await executor.complete(lease, call, "unknown")
    async with admin_engine.begin() as c:
        await c.execute(
            text("UPDATE provider_admission SET global_daily_cost=4004,owner_daily_cost=4004")
        )
    async with executor.factory() as session:
        task = (await session.scalars(select(Task))).one()
        provider = (await session.scalars(select(ProviderAdmission))).one()
        tomorrow = (await db_now(session)) + timedelta(days=1)
        assert (
            await admission_reason(session, task, provider, tomorrow, 4004, 2002) == "capacity_wait"
        )
    assert await executor.settle(call, "late", 4004, 2002, 0)
    async with executor.factory() as session:
        task = (await session.scalars(select(Task))).one()
        provider = (await session.scalars(select(ProviderAdmission))).one()
        assert await admission_reason(session, task, provider, tomorrow, 4004, 2002) is None


async def test_b_transaction_rollback_leaves_no_authorization_or_provision(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import Session

    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease

    def reject_commit(session: Session) -> None:
        raise RuntimeError("injected before B commit")

    event.listen(Session, "before_commit", reject_commit)
    try:
        with pytest.raises(RuntimeError, match="injected before B commit"):
            await executor.authorize(lease, uuid4(), b"{}")
    finally:
        event.remove(Session, "before_commit", reject_commit)
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 0
        assert await c.scalar(text("SELECT count(*) FROM call_costs")) == 0
        assert await c.scalar(text("SELECT selected_call_id FROM steps")) is None
        assert await c.scalar(text("SELECT state FROM attempts")) == "active"


async def test_external_call_identity_and_cost_evidence_are_immutable(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
) -> None:
    from sqlalchemy.exc import DBAPIError

    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    assert await executor.settle(call, "proof", 20, 10, 0)
    engine = create_engine(settings_for("migrator"))
    try:
        for query in [
            "UPDATE external_calls SET payload='changed'::bytea",
            "UPDATE call_costs SET reserved_cost=0",
            "UPDATE cost_evidence SET cost=0",
            "DELETE FROM cost_evidence",
        ]:
            with pytest.raises(DBAPIError) as error:
                async with engine.begin() as c:
                    await c.execute(text(query))
            assert getattr(error.value.orig, "sqlstate", None) == "23514"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("expired", [False, True])
async def test_f07_observed_heartbeat_c_reaper_lock_contention(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
    expired: bool,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    engine = create_engine(settings_for("migrator"))

    async def waiters(count: int) -> None:
        async with asyncio.timeout(0.8), admin_engine.connect() as c:
            await c.execution_options(isolation_level="AUTOCOMMIT")
            for _ in range(150):
                found = await c.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE usename='atp_worker' "
                        "AND cardinality(pg_blocking_pids(pid))>0"
                    )
                )
                if found == count:
                    return
                await asyncio.sleep(0.005)
            pytest.fail("Expected actual lock contention")

    try:
        if expired:
            async with engine.begin() as c:
                await c.execute(
                    text("UPDATE steps SET lease_until=clock_timestamp()-interval '1 second'")
                )
        async with engine.begin() as c:
            await c.execute(text("SELECT id FROM steps FOR UPDATE"))
            heartbeat = asyncio.create_task(executor.heartbeat(lease))
            await waiters(1)
            completion = asyncio.create_task(
                executor.complete(lease, call, "succeeded", cost=20, tokens=10)
            )
            await waiters(2)
            reaper = asyncio.create_task(executor.maintenance())
            await waiters(3)
        assert await heartbeat is (not expired)
        assert await completion is (not expired)
        await reaper
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT state FROM tasks")) == (
                "suspended" if expired else "succeeded"
            )
            assert await c.scalar(text("SELECT count(*) FROM attempts WHERE state='active'")) == 0
            assert await c.scalar(text("SELECT confirmed_cost FROM call_costs")) == 20
    finally:
        await engine.dispose()


@pytest.mark.parametrize("recovered", [False, True])
async def test_replayed_c_does_not_extend_shared_retry_after(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
    recovered: bool,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    if recovered:
        engine = create_engine(settings_for("migrator"))
        try:
            async with engine.begin() as c:
                await c.execute(
                    text("UPDATE steps SET lease_until=clock_timestamp()-interval '1 second'")
                )
            await executor.maintenance()
        finally:
            await engine.dispose()
    assert await executor.complete(
        lease,
        call,
        "retryable",
        cost=0,
        tokens=0,
        retry_after_seconds=30,
    ) is (not recovered)
    async with admin_engine.connect() as c:
        before = await c.scalar(text("SELECT next_allowed_at FROM provider_admission"))
        assert await c.scalar(
            text(
                "SELECT next_allowed_at > clock_timestamp() + interval '28 seconds' "
                "FROM provider_admission"
            )
        )
    await executor.complete(
        lease,
        call,
        "retryable",
        cost=0,
        tokens=0,
        retry_after_seconds=30,
    )
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT next_allowed_at FROM provider_admission")) == before
