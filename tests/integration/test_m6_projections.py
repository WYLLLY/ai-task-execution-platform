"""M6 public accounting provenance, retained ownership and durable trace linkage."""

import json
import re
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.execution import Executor
from task_platform.identity import issue_key, provision_principal
from task_platform.provider_admin import reconcile

from .conftest import settings_for
from .test_accounting_admin import evidence, pending
from .test_commands import create
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch

pytestmark = pytest.mark.integration


async def test_usage_includes_only_owner_allocated_disaster_reserve_without_fabricated_calls(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    owner = (await client.get("/v1/me", headers=command_owner)).json()["principal_id"]
    try:
        async with admin_engine.begin() as c:
            await c.execute(
                text("UPDATE principals SET reserve_cost=71,reserve_tokens=91 WHERE id=:id"),
                {"id": owner},
            )
            await c.execute(
                text("UPDATE platform_control SET reserve_cost=999,reserve_tokens=1999")
            )
        response = (await client.get("/v1/usage", headers=command_owner)).json()
        assert response["disaster_provision"] == {"micro_usd": 71, "tokens": 91}
        assert response["costs"]["uncertain_micro_usd"] == 71
        assert response["costs"]["uncertain_tokens"] == 91
        other = await provision_principal(sessions(admin_engine), "foreign disaster usage")
        _, token = await issue_key(sessions(admin_engine), other)
        foreign = (
            await client.get("/v1/usage", headers={"Authorization": "Bearer " + token})
        ).json()
        assert foreign["disaster_provision"] == {"micro_usd": 0, "tokens": 0}
        assert all(value == 0 for value in foreign["costs"].values())
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 0
    finally:
        async with admin_engine.begin() as c:
            await c.execute(
                text("UPDATE principals SET reserve_cost=0,reserve_tokens=0 WHERE id=:id"),
                {"id": owner},
            )
            await c.execute(text("UPDATE platform_control SET reserve_cost=0,reserve_tokens=0"))


async def test_usage_distinguishes_estimate_provision_invoice_and_owner(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    call = await pending(client, command_owner, executor)
    response = await client.get("/v1/usage", headers=command_owner)
    assert response.status_code == 200, "M6 must expose owner-scoped usage"
    assert response.json()["costs"]["uncertain_micro_usd"] == 4004
    assert await reconcile(
        sessions(admin_engine),
        call,
        evidence(estimated=True, source="PRIVATE_SOURCE_CANARY", reason="PRIVATE_REASON_CANARY"),
    )
    estimated = (await client.get("/v1/usage", headers=command_owner)).json()
    assert estimated["costs"]["confirmed_micro_usd"] == 0
    assert estimated["costs"]["estimated_micro_usd"] == 20
    assert estimated["costs"]["estimated_tokens"] == 10
    assert estimated["costs"]["uncertain_micro_usd"] == 0
    async with admin_engine.connect() as c:
        task_id = await c.scalar(
            text("SELECT task_id FROM external_calls WHERE id=:id"), {"id": call}
        )
    snapshot = (await client.get(f"/v1/tasks/{task_id}", headers=command_owner)).json()
    assert snapshot["costs"] == estimated["costs"]
    assert "PRIVATE_" not in json.dumps(estimated) + json.dumps(snapshot)
    # Confirming the same amount changes provenance, never charges it a second time.
    assert await reconcile(
        sessions(admin_engine),
        call,
        evidence(evidence_id="invoice-confirmed", revision=1, estimated=False),
    )
    confirmed = (await client.get("/v1/usage", headers=command_owner)).json()["costs"]
    assert confirmed["confirmed_micro_usd"] == 20 and confirmed["estimated_micro_usd"] == 0
    assert confirmed["confirmed_tokens"] == 10 and confirmed["uncertain_tokens"] == 0
    owner = await provision_principal(sessions(admin_engine), "usage foreign owner")
    _, token = await issue_key(sessions(admin_engine), owner)
    foreign = await client.get("/v1/usage", headers={"Authorization": f"Bearer {token}"})
    assert foreign.status_code == 200 and all(v == 0 for v in foreign.json()["costs"].values())


async def test_m6_tombstone_schema_and_private_owner_lookup(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT to_regclass('public.task_tombstones')")), (
            "missing retained ownership"
        )
    owner = UUID((await client.get("/v1/me", headers=command_owner)).json()["principal_id"])
    task = uuid4()
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text("INSERT INTO task_tombstones(task_id,principal_id) VALUES(:task,:owner)"),
                {"task": task, "owner": owner},
            )
        for suffix in ("", "/events?after=malformed"):
            response = await client.get(f"/v1/tasks/{task}{suffix}", headers=command_owner)
            assert response.status_code == 410
            assert response.json()["snapshot_url"] == f"/v1/tasks/{task}"
        other = await provision_principal(sessions(admin_engine), "tombstone foreign owner")
        _, token = await issue_key(sessions(admin_engine), other)
        for suffix in ("", "/events?after=malformed"):
            response = await client.get(
                f"/v1/tasks/{task}{suffix}", headers={"Authorization": f"Bearer {token}"}
            )
            assert response.status_code == 404 and response.json() == {"detail": "Not found"}
    finally:
        async with engine.begin() as c:
            await c.execute(text("DELETE FROM task_tombstones WHERE task_id=:id"), {"id": task})
        await engine.dispose()


async def test_attempt_trace_context_survives_crash_and_links_replacement(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as c:
        columns = (
            (
                await c.execute(
                    text(
                        "SELECT column_name FROM information_schema.columns "
                        "WHERE table_name='attempts'"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert "trace_context" in columns, "attempt trace must survive process loss"
    await create(client, command_owner)
    first = await executor.claim("trace-worker-lost")
    assert first
    async with admin_engine.connect() as c:
        before = await c.scalar(
            text("SELECT trace_context FROM attempts WHERE id=:id"), {"id": first.attempt_id}
        )
    assert before is not None, "claim must persist its trace context"
    assert re.fullmatch(r"[0-9a-f]{32}", before["trace_id"])
    assert re.fullmatch(r"[0-9a-f]{16}", before["span_id"])
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE steps SET lease_until=clock_timestamp()-interval '1 second'")
            )
        await executor.maintenance()
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE steps SET available_at=clock_timestamp()-interval '1 second'")
            )
        await executor.maintenance()
        replacement = await executor.claim("trace-worker-replacement")
        assert replacement
        async with admin_engine.connect() as c:
            after = await c.scalar(
                text("SELECT trace_context FROM attempts WHERE id=:id"),
                {"id": replacement.attempt_id},
            )
            retained = await c.scalar(
                text("SELECT trace_context FROM attempts WHERE id=:id"), {"id": first.attempt_id}
            )
        assert retained == before
        assert after["trace_id"] == before["trace_id"]
        assert after["span_id"] != before["span_id"]
        assert after["previous_span_id"] == before["span_id"]
    finally:
        await engine.dispose()


async def test_api_cannot_reclassify_cost_or_write_tombstones(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT to_regclass('public.task_tombstones')")), (
            "missing ownership schema"
        )
    engine = create_engine(settings_for("api"))
    try:
        for query in (
            "UPDATE call_costs SET estimated=true",
            "DELETE FROM task_tombstones",
            "INSERT INTO task_tombstones(task_id,principal_id) "
            "VALUES(gen_random_uuid(),gen_random_uuid())",
        ):
            with pytest.raises(DBAPIError) as error:
                async with engine.begin() as c:
                    await c.execute(text(query))
            assert getattr(error.value.orig, "sqlstate", None) == "42501"
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "context",
    [
        {},
        {"trace_id": "a" * 32},
        {"trace_id": "a" * 32, "span_id": "b" * 16, "flags": "zz"},
        {"trace_id": "0" * 32, "span_id": "b" * 16, "flags": "01"},
        {"trace_id": "a" * 32, "span_id": "0" * 16, "flags": "01"},
    ],
)
async def test_trace_metadata_constraint_rejects_missing_invalid_or_zero_ids(
    client: AsyncClient, command_owner: dict[str, str], executor: Executor, context: dict[str, str]
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("trace-constraint")
    assert lease
    engine = create_engine(settings_for("migrator"))
    try:
        with pytest.raises(DBAPIError) as error:
            async with engine.begin() as c:
                await c.execute(
                    text("""INSERT INTO attempts(id,task_id,step_id,attempt_number,
                    generation,state,started_at,ended_at,reason,worker,execution_epoch,trace_context)
                    SELECT gen_random_uuid(),task_id,step_id,attempt_number+100,generation+100,
                    'failed',started_at,clock_timestamp(),'test',worker,execution_epoch,
                    CAST(:context AS jsonb)
                    FROM attempts WHERE id=:id"""),
                    {"id": lease.attempt_id, "context": json.dumps(context)},
                )
        assert getattr(error.value.orig, "sqlstate", None) == "23514"
    finally:
        await engine.dispose()


@pytest.mark.parametrize("flags", ["00", "01", "02", "03"])
async def test_trace_constraint_preserves_pinned_sdk_sampled_and_random_flags(
    client: AsyncClient, command_owner: dict[str, str], executor: Executor, flags: str
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("trace-sdk-flags")
    assert lease
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text("""INSERT INTO attempts(id,task_id,step_id,attempt_number,
                generation,state,started_at,ended_at,reason,worker,execution_epoch,trace_context)
                SELECT gen_random_uuid(),task_id,step_id,attempt_number+100,generation+100,
                'failed',started_at,clock_timestamp(),'test',worker,execution_epoch,
                CAST(:context AS jsonb) FROM attempts WHERE id=:id"""),
                {
                    "id": lease.attempt_id,
                    "context": json.dumps(
                        {"trace_id": "a" * 32, "span_id": "b" * 16, "flags": flags}
                    ),
                },
            )
    finally:
        await engine.dispose()


async def test_snapshot_costs_and_watermark_share_one_mvcc_snapshot_during_settlement(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    import asyncio

    from sqlalchemy import event
    from sqlalchemy.util.concurrency import await_only

    from task_platform.event_reads import task_snapshot
    from task_platform.identity import Identity

    call = await pending(client, command_owner, executor)
    async with admin_engine.connect() as c:
        task, owner, key = (await c.execute(text("SELECT id,principal_id,key_id FROM tasks"))).one()
    engine = create_engine(settings_for("api"))
    factory = sessions(engine)
    async with factory() as session:
        before = await task_snapshot(session, Identity(owner, key), task)
    queried, resume = asyncio.Event(), asyncio.Event()

    def hold(
        connection: object,
        cursor: object,
        statement: str,
        parameters: object,
        context: object,
        executemany: bool,
    ) -> None:
        if "FROM tasks" in statement and not queried.is_set():
            queried.set()
            await_only(resume.wait())

    event.listen(engine.sync_engine, "after_cursor_execute", hold)
    try:
        async with factory() as session:
            reading = asyncio.create_task(task_snapshot(session, Identity(owner, key), task))
            async with asyncio.timeout(3):
                await queried.wait()
                assert await reconcile(sessions(admin_engine), call, evidence())
                resume.set()
                observed = await reading
            assert observed == before, "one statement must not mix pre/post-settlement projections"
        async with factory() as session:
            after = await task_snapshot(session, Identity(owner, key), task)
        assert after["costs"] != before["costs"]
        assert int(str(after["event_sequence"])) > int(str(before["event_sequence"]))
    finally:
        resume.set()
        event.remove(engine.sync_engine, "after_cursor_execute", hold)
        await engine.dispose()
