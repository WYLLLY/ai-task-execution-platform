"""Real PostgreSQL admission races, rollback and bounded transaction retries."""

import asyncio
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.identity import issue_key, provision_principal

from .conftest import settings_for
from .test_commands import BODY, create

pytestmark = pytest.mark.integration


async def wait_for_blocked(engine: AsyncEngine, count: int) -> None:
    async with asyncio.timeout(0.8), engine.connect() as c:
        await c.execution_options(isolation_level="AUTOCOMMIT")
        while (  # noqa: ASYNC110 -- observe actual PostgreSQL waits
            await c.scalar(
                text(
                    "SELECT count(*) FROM pg_stat_activity WHERE usename='atp_api' "
                    "AND cardinality(pg_blocking_pids(pid))>0"
                )
            )
            != count
        ):
            await asyncio.sleep(0.005)


@pytest.mark.parametrize("scenario", ["global", "collision", "cancel"])
async def test_concurrent_command_serialization(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine, scenario: str
) -> None:
    other = command_owner.copy()
    route = "/v1/tasks"
    bodies = [BODY, BODY]
    keys = ["race-a", "race-b"]
    expected = [202, 429]
    if scenario == "global":
        owner = await provision_principal(sessions(admin_engine), "Second owner")
        _, token = await issue_key(sessions(admin_engine), owner)
        other["Authorization"] = f"Bearer {token}"
        async with admin_engine.begin() as c:
            await c.execute(text("UPDATE platform_control SET max_open_tasks=1"))
    elif scenario == "collision":
        keys = ["collision", "collision"]
        bodies = [BODY, {**BODY, "input": {"texts": ["Different body"]}}]
        expected = [202, 409]
    else:
        task = await create(client, command_owner)
        route = f"/v1/tasks/{task['task_id']}/cancel"
        bodies = [{"expected_task_version": 1}] * 2
        expected = [200, 409]
    async with admin_engine.begin() as holder:
        await holder.execute(text("SELECT id FROM platform_control FOR UPDATE"))
        calls = [
            asyncio.create_task(
                client.post(route, json=body, headers={**headers, "Idempotency-Key": key})
            )
            for body, headers, key in zip(bodies, [command_owner, other], keys, strict=True)
        ]
        await wait_for_blocked(admin_engine, 2)
    responses = await asyncio.gather(*calls)
    assert sorted(r.status_code for r in responses) == expected
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM tasks")) == 1
        assert await c.scalar(text("SELECT open_tasks FROM platform_control")) == (
            0 if scenario == "cancel" else 1
        )
        assert await c.scalar(text("SELECT count(*) FROM task_events")) == (
            2 if scenario == "cancel" else 1
        )


@pytest.mark.parametrize("cancel", [False, True])
async def test_revocation_committed_while_command_waits_prevents_mutation(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine, cancel: bool
) -> None:
    identity = (await client.get("/v1/me", headers=command_owner)).json()
    route, body = "/v1/tasks", BODY
    if cancel:
        task = await create(client, command_owner)
        route, body = f"/v1/tasks/{task['task_id']}/cancel", {"expected_task_version": 1}
    async with admin_engine.begin() as holder:
        await holder.execute(
            text("SELECT id FROM principals WHERE id=:id FOR UPDATE"),
            {"id": UUID(identity["principal_id"])},
        )
        pending = asyncio.create_task(
            client.post(
                route, json=body, headers={**command_owner, "Idempotency-Key": "revocation-race"}
            )
        )
        await wait_for_blocked(admin_engine, 1)
        await holder.execute(
            text("UPDATE api_keys SET revoked_at=clock_timestamp() WHERE id=:id"),
            {"id": UUID(identity["key_id"])},
        )
    response = await pending
    assert response.status_code == 401
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM tasks")) == int(cancel)
        assert await c.scalar(text("SELECT count(*) FROM idempotency_records")) == int(cancel)
        assert await c.scalar(text("SELECT open_tasks FROM platform_control")) == int(cancel)


@pytest.mark.parametrize("scope", ["owner", "global"])
async def test_storage_limit_rejects_atomically_and_retained_replay_survives(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine, scope: str
) -> None:
    first = await create(client, command_owner)
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "UPDATE principals SET max_storage_bytes=storage_bytes"
                if scope == "owner"
                else "UPDATE platform_control SET max_storage_bytes=storage_bytes"
            )
        )
    try:
        denied = await client.post(
            "/v1/tasks", json=BODY, headers={**command_owner, "Idempotency-Key": "over-storage"}
        )
        assert denied.status_code == 429
        replay = await create(client, command_owner)
        assert replay == first
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT count(*) FROM tasks")) == 1
            assert await c.scalar(text("SELECT count(*) FROM idempotency_records")) == 1
            assert await c.scalar(text("SELECT open_tasks FROM platform_control")) == 1
    finally:
        async with admin_engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE principals SET max_storage_bytes=:limit"
                    if scope == "owner"
                    else "UPDATE platform_control SET max_storage_bytes=:limit"
                ),
                {"limit": 104857600 if scope == "owner" else 2147483648},
            )


@pytest.mark.parametrize(
    "code,failures,expected,invocations",
    [
        ("40001", 2, 202, 3),
        ("40P01", 1, 202, 2),
        ("40001", 4, 503, 3),
        ("23514", 1, 503, 1),
    ],
)
async def test_real_transaction_failure_retry_bounds_and_atomicity(
    client: AsyncClient,
    command_owner: dict[str, str],
    code: str,
    failures: int,
    expected: int,
    invocations: int,
) -> None:
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(text("CREATE SEQUENCE test_invocations"))
            await c.execute(text("GRANT USAGE, SELECT ON SEQUENCE test_invocations TO atp_api"))
            await c.execute(
                text(f"""
                CREATE FUNCTION test_fail_command() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                  IF nextval('test_invocations') <= {failures} THEN
                    RAISE EXCEPTION 'synthetic test failure' USING ERRCODE='{code}';
                  END IF;
                  RETURN NEW;
                END $$
            """)
            )
            # Last retained response insertion: task, counters and event must all roll back.
            await c.execute(
                text(
                    "CREATE TRIGGER test_fault BEFORE INSERT ON idempotency_records "
                    "FOR EACH ROW EXECUTE FUNCTION test_fail_command()"
                )
            )
        response = await client.post(
            "/v1/tasks", json=BODY, headers={**command_owner, "Idempotency-Key": "fault"}
        )
        assert response.status_code == expected, response.text
        async with engine.connect() as c:
            assert await c.scalar(text("SELECT last_value FROM test_invocations")) == invocations
            for table in ("tasks", "steps", "task_events", "idempotency_records"):
                assert await c.scalar(text(f"SELECT count(*) FROM {table}")) == int(expected == 202)  # noqa: S608 -- fixed test table list
            assert await c.scalar(text("SELECT open_tasks FROM platform_control")) == int(
                expected == 202
            )
            if expected == 503:
                assert await c.scalar(text("SELECT storage_bytes FROM platform_control")) == 0
    finally:
        async with engine.begin() as c:
            await c.execute(text("DROP TRIGGER IF EXISTS test_fault ON idempotency_records"))
            await c.execute(text("DROP FUNCTION IF EXISTS test_fail_command()"))
            await c.execute(text("DROP SEQUENCE IF EXISTS test_invocations"))
        await engine.dispose()
