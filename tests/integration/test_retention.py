"""F20: administrative redaction never deletes unresolved financial exposure."""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.config import Role
from task_platform.execution import Executor

from .test_commands import create
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch

pytestmark = pytest.mark.integration


async def test_retention_records_real_terminal_and_command_clocks(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
) -> None:
    item = await create(client, command_owner)
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT to_jsonb(t)->>'terminal_at' FROM tasks t")) is None
        accepted = await c.scalar(
            text("SELECT to_jsonb(r)->>'created_at' FROM idempotency_records r")
        )
        assert accepted is not None, "accepted command needs its own retention clock"
    response = await client.post(
        f"/v1/tasks/{item['task_id']}/cancel",
        headers={**command_owner, "Idempotency-Key": "retention-cancel"},
        json={"expected_task_version": item["task_version"]},
    )
    assert response.status_code == 200
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT to_jsonb(t)->>'terminal_at' FROM tasks t")) is not None


async def test_final_accounting_records_conservative_resolution_clock(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("retention-clock")
    assert lease
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT to_jsonb(c)->>'resolved_at' FROM call_costs c")) is None
    assert await executor.complete(lease, call, "succeeded", cost=20, tokens=10)
    async with admin_engine.connect() as c:
        stamp = await c.scalar(text("SELECT to_jsonb(c)->>'resolved_at' FROM call_costs c"))
        assert stamp is not None, "financial retention starts from an observed resolution"


async def age_terminal_task(task: object) -> None:
    from task_platform.db import create_engine

    from .conftest import settings_for

    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            # Test-only exclusive DDL: change historical clocks, never production guards.
            await c.execute(text("ALTER TABLE tasks DISABLE TRIGGER USER"))
            await c.execute(
                text(
                    "UPDATE tasks SET terminal_at=clock_timestamp()-interval '31 days',"
                    "created_at=clock_timestamp()-interval '31 days 1 minute',"
                    "deadline_at=clock_timestamp()-interval '31 days'+interval '14 minutes' "
                    "WHERE id=:id"
                ),
                {"id": task},
            )
            await c.execute(text("ALTER TABLE tasks ENABLE TRIGGER USER"))
            await c.execute(text("ALTER TABLE idempotency_records DISABLE TRIGGER USER"))
            await c.execute(
                text(
                    "UPDATE idempotency_records SET "
                    "created_at=clock_timestamp()-interval '31 days' "
                    "WHERE task_id=:id"
                ),
                {"id": task},
            )
            await c.execute(text("ALTER TABLE idempotency_records ENABLE TRIGGER USER"))
    finally:
        await engine.dispose()


async def test_purge_preserves_unknown_exposure_and_late_settlement(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    from task_platform.db import sessions
    from task_platform.provider_admin import reconcile
    from task_platform.retention import purge

    from .test_accounting_admin import evidence, pending

    call = await pending(client, command_owner, executor)
    async with admin_engine.connect() as c:
        task = await c.scalar(text("SELECT task_id FROM external_calls WHERE id=:id"), {"id": call})
        digest = await c.scalar(
            text("SELECT payload_hash FROM external_calls WHERE id=:id"), {"id": call}
        )
    snapshot = (await client.get(f"/v1/tasks/{task}", headers=command_owner)).json()
    cancelled = await client.post(
        f"/v1/tasks/{task}/cancel",
        headers={**command_owner, "Idempotency-Key": "purge-cancel"},
        json={"expected_task_version": snapshot["task_version"]},
    )
    assert cancelled.status_code == 200
    await age_terminal_task(task)
    assert (await purge(sessions(admin_engine)))["tasks"] == 1
    for path in (
        f"/v1/tasks/{task}",
        f"/v1/tasks/{task}/events?after=malformed",
        f"/v1/tasks/{task}/steps",
    ):
        assert (await client.get(path, headers=command_owner)).status_code == 410
    async with admin_engine.connect() as c:
        row = (
            await c.execute(
                text(
                    "SELECT payload,payload_hash,to_jsonb(x)->>'replay_available' "
                    "FROM external_calls x"
                )
            )
        ).one()
        assert row == (b"", digest, "false")
        assert await c.scalar(text("SELECT exposure_cost FROM call_costs")) == 4004
        assert await c.scalar(text("SELECT input FROM tasks")) == {}
        assert await c.scalar(text("SELECT count(*) FROM task_events")) == 0
    assert await reconcile(sessions(admin_engine), str(call), evidence())
    usage = (await client.get("/v1/usage", headers=command_owner)).json()["costs"]
    assert usage["confirmed_micro_usd"] == 20 and usage["uncertain_micro_usd"] == 0
    assert (await purge(sessions(admin_engine)))["tasks"] == 0
    # A retained key cannot recreate a purged command even when admission changes.
    from .test_commands import BODY

    replay = await client.post(
        "/v1/tasks", json=BODY, headers={**command_owner, "Idempotency-Key": "create-1"}
    )
    assert replay.status_code == 410


async def test_retention_never_purges_nonterminal_or_young_tasks(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
) -> None:
    from task_platform.db import sessions
    from task_platform.retention import purge

    await create(client, command_owner)
    assert await purge(sessions(admin_engine)) == {"tasks": 0, "responses": 0}


@pytest.mark.parametrize("role", ["api", "worker"])
async def test_only_admin_can_invoke_purge_and_runtime_cannot_edit_payloads(role: Role) -> None:
    from sqlalchemy.exc import DBAPIError

    from task_platform.db import create_engine, sessions
    from task_platform.retention import purge

    from .conftest import settings_for

    engine = create_engine(settings_for(role))
    try:
        with pytest.raises((DBAPIError, ValueError)):
            await purge(sessions(engine))
    finally:
        await engine.dispose()


async def test_concurrent_purge_serializes_and_retains_foreign_not_found(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
) -> None:
    import asyncio
    from uuid import UUID

    from task_platform.db import sessions
    from task_platform.identity import issue_key, provision_principal
    from task_platform.retention import purge

    item = await create(client, command_owner)
    task = UUID(str(item["task_id"]))
    await client.post(
        f"/v1/tasks/{task}/cancel",
        headers={**command_owner, "Idempotency-Key": "cancel-before-purge"},
        json={"expected_task_version": item["task_version"]},
    )
    await age_terminal_task(task)
    owner = await provision_principal(sessions(admin_engine), "foreign retention")
    _, token = await issue_key(sessions(admin_engine), owner)
    from task_platform.db import create_engine

    from .conftest import settings_for

    contenders = [create_engine(settings_for("admin")) for _ in range(2)]
    async with admin_engine.begin() as lock:
        await lock.execute(text("SELECT id FROM platform_control FOR UPDATE"))
        jobs = [asyncio.create_task(purge(sessions(engine))) for engine in contenders]
        # Observe real blocking, not merely a start barrier.
        for _ in range(100):
            async with admin_engine.connect() as c:
                blocked = await c.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE usename='atp_admin' "
                        "AND cardinality(pg_blocking_pids(pid))>0"
                    )
                )
            if int(blocked or 0) >= 2:
                break
            await asyncio.sleep(0.01)
        else:
            pytest.fail("both purgers must contend on the platform lock")
    try:
        results = await asyncio.gather(*jobs)
    finally:
        for engine in contenders:
            await engine.dispose()
    assert sum(r["tasks"] for r in results) == 1
    assert (
        await client.get(f"/v1/tasks/{task}", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 404
    async with admin_engine.connect() as c:
        assert (
            await c.scalar(
                text("SELECT count(*) FROM task_tombstones WHERE task_id=:id"), {"id": task}
            )
            == 1
        )
        assert await c.scalar(text("SELECT storage_bytes>=0 FROM platform_control"))


async def test_corpus_delete_requires_finished_references_and_preserves_version_identity(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
) -> None:
    from uuid import UUID

    from task_platform.corpus import delete_document, import_document
    from task_platform.db import sessions

    owner = UUID((await client.get("/v1/me", headers=command_owner)).json()["principal_id"])
    doc = uuid4()
    factory = sessions(admin_engine)
    assert await import_document(factory, owner, doc, "private corpus") == 1
    response = await client.post(
        "/v1/tasks",
        headers={**command_owner, "Idempotency-Key": "corpus-delete-ref"},
        json={
            "workflow": {"name": "analysis", "version": "1"},
            "input": {"texts": ["analyze"], "documents": [{"id": str(doc), "version": 1}]},
        },
    )
    assert response.status_code == 202
    with pytest.raises(ValueError, match="active references"):
        await delete_document(factory, owner, doc, 1)
    item = response.json()
    assert (
        await client.post(
            f"/v1/tasks/{item['task_id']}/cancel",
            headers={**command_owner, "Idempotency-Key": "corpus-cancel"},
            json={"expected_task_version": item["task_version"]},
        )
    ).status_code == 200
    assert await delete_document(factory, owner, doc, 1)
    assert not await delete_document(factory, owner, doc, 1)
    async with admin_engine.connect() as c:
        row = (
            await c.execute(
                text("SELECT content,readable FROM documents WHERE id=:id"), {"id": doc}
            )
        ).one()
        assert row == ("", False)
    assert await import_document(factory, owner, doc, "new corpus") == 2


@pytest.mark.parametrize("role", ["api", "worker"])
async def test_runtime_sql_cannot_purge_or_rewrite_exact_payload(role: Role) -> None:
    from sqlalchemy.exc import DBAPIError

    from task_platform.db import create_engine

    from .conftest import settings_for

    engine = create_engine(settings_for(role))
    try:
        for statement in (
            "SELECT purge_task_contents(gen_random_uuid())",
            "UPDATE external_calls SET payload=''::bytea",
            "UPDATE tasks SET purged_at=clock_timestamp()",
        ):
            async with engine.begin() as c:
                with pytest.raises(DBAPIError):
                    await c.execute(text(statement))
    finally:
        await engine.dispose()
