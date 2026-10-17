"""Plan §2.3 arc matrix and structural invariants on real PostgreSQL.

These SQL tests exercise structural protection, not the M3 actor authorization,
lease, external-call or accounting preconditions. Setup reaches source states
through legal arcs with all deferred constraints enabled.
"""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from itertools import permutations
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.identity import issue_key, provision_principal

from .conftest import settings_for
from .test_commands import create

pytestmark = pytest.mark.integration

# Contract copied from the approved plan, not inferred from the migration.
TASK_ARCS = {
    "queued": {"running", "suspended", "failed", "cancelled", "timed_out"},
    "running": {"succeeded", "failed", "suspended", "cancelled", "timed_out"},
    "suspended": {"queued", "succeeded", "failed", "cancelled", "timed_out"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
    "timed_out": set(),
}
STEP_ARCS = {
    "ready": {"running", "cancelled"},
    "running": {"succeeded", "retry_wait", "failed", "unknown", "ready", "cancelled"},
    "retry_wait": {"ready", "cancelled"},
    "unknown": {"ready", "succeeded", "failed", "cancelled"},
    "succeeded": set(),
    "failed": set(),
    "cancelled": set(),
}
ATTEMPT_STATES = ("active", "succeeded", "failed", "expired", "revoked")


@dataclass(frozen=True)
class Subject:
    task: UUID
    step: UUID
    attempt: UUID


@pytest.fixture
async def migration_engine() -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings_for("migrator"))
    try:
        yield engine
    finally:
        await engine.dispose()


async def subject(client: AsyncClient, headers: dict[str, str], engine: AsyncEngine) -> Subject:
    response = await create(client, headers)
    task_id = UUID(str(response["task_id"]))
    async with engine.connect() as connection:
        step_id = await connection.scalar(
            text("SELECT id FROM steps WHERE task_id=:task"), {"task": task_id}
        )
    assert isinstance(step_id, UUID)
    return Subject(task_id, step_id, uuid4())


async def task_state(connection: AsyncConnection, item: Subject, state: str) -> None:
    await connection.execute(
        text(
            "UPDATE tasks SET state=:state,reason='matrix fixture',"
            "task_version=task_version+1 WHERE id=:task"
        ),
        {"task": item.task, "state": state},
    )


async def step_state(connection: AsyncConnection, item: Subject, state: str) -> None:
    await connection.execute(
        text(
            "UPDATE steps SET state=CAST(:state AS varchar),generation=1,"
            "holder=CASE WHEN CAST(:state AS varchar)='running' "
            "THEN 'matrix fixture' ELSE NULL END,"
            "lease_until=CASE WHEN CAST(:state AS varchar)='running' "
            "THEN clock_timestamp()+interval '60 seconds' ELSE NULL END WHERE id=:step"
        ),
        {"step": item.step, "state": state},
    )


async def active_attempt(connection: AsyncConnection, item: Subject, number: int = 1) -> None:
    await connection.execute(
        text(
            "INSERT INTO attempts(id,task_id,step_id,attempt_number,generation,state) "
            "VALUES(:id,:task,:step,:number,1,'active')"
        ),
        {
            "id": item.attempt if number == 1 else uuid4(),
            "task": item.task,
            "step": item.step,
            "number": number,
        },
    )


async def close_attempt(connection: AsyncConnection, item: Subject, state: str) -> None:
    await connection.execute(
        text("UPDATE attempts SET state=:state WHERE id=:id"),
        {"id": item.attempt, "state": state},
    )


async def seed_step(connection: AsyncConnection, item: Subject, state: str) -> None:
    await task_state(connection, item, "running")
    if state == "ready":
        return
    await step_state(connection, item, "running")
    await active_attempt(connection, item)
    if state != "running":
        await close_attempt(connection, item, "expired")
        await step_state(connection, item, state)
    if state == "unknown":
        await task_state(connection, item, "suspended")


@pytest.mark.parametrize("source,target", list(permutations(TASK_ARCS, 2)))
async def test_all_task_state_arcs(
    client: AsyncClient,
    command_owner: dict[str, str],
    migration_engine: AsyncEngine,
    source: str,
    target: str,
) -> None:
    item = await subject(client, command_owner, migration_engine)
    async with migration_engine.begin() as connection:
        # Closed children isolate the task arc from the separate coupling invariant.
        await step_state(connection, item, "cancelled")
        if source == "succeeded":
            await task_state(connection, item, "running")
        if source != "queued":
            await task_state(connection, item, source)
    if target in TASK_ARCS[source]:
        async with migration_engine.begin() as connection:
            await task_state(connection, item, target)
    else:
        with pytest.raises(DBAPIError) as failure:
            async with migration_engine.begin() as connection:
                await task_state(connection, item, target)
        assert getattr(failure.value.orig, "sqlstate", None) == "23514"
    async with migration_engine.connect() as connection:
        assert await connection.scalar(
            text("SELECT state FROM tasks WHERE id=:id"), {"id": item.task}
        ) == (target if target in TASK_ARCS[source] else source)


@pytest.mark.parametrize("source,target", list(permutations(STEP_ARCS, 2)))
async def test_all_step_state_arcs(
    client: AsyncClient,
    command_owner: dict[str, str],
    migration_engine: AsyncEngine,
    source: str,
    target: str,
) -> None:
    item = await subject(client, command_owner, migration_engine)
    async with migration_engine.begin() as connection:
        await seed_step(connection, item, source)

    async def transition(connection: AsyncConnection) -> None:
        if source == "running":
            await close_attempt(connection, item, "revoked")
        if source == "unknown":
            await task_state(connection, item, "queued")
            await task_state(connection, item, "running")
        if target == "unknown":
            await task_state(connection, item, "suspended")
        await step_state(connection, item, target)
        if target == "running":
            await active_attempt(connection, item, 2)

    if target in STEP_ARCS[source]:
        async with migration_engine.begin() as connection:
            await transition(connection)
    else:
        with pytest.raises(DBAPIError) as failure:
            async with migration_engine.begin() as connection:
                await transition(connection)
        assert getattr(failure.value.orig, "sqlstate", None) == "23514"
    async with migration_engine.connect() as connection:
        assert await connection.scalar(
            text("SELECT state FROM steps WHERE id=:id"), {"id": item.step}
        ) == (target if target in STEP_ARCS[source] else source)


@pytest.mark.parametrize("source,target", list(permutations(ATTEMPT_STATES, 2)))
async def test_all_attempt_state_arcs(
    client: AsyncClient,
    command_owner: dict[str, str],
    migration_engine: AsyncEngine,
    source: str,
    target: str,
) -> None:
    item = await subject(client, command_owner, migration_engine)
    async with migration_engine.begin() as connection:
        await seed_step(connection, item, "running")
        if source != "active":
            await close_attempt(connection, item, source)
            await step_state(connection, item, "ready")
    if source == "active":
        async with migration_engine.begin() as connection:
            await close_attempt(connection, item, target)
            await step_state(connection, item, "ready")
    else:
        with pytest.raises(DBAPIError) as failure:
            async with migration_engine.begin() as connection:
                await close_attempt(connection, item, target)
        assert getattr(failure.value.orig, "sqlstate", None) == "23514"
    async with migration_engine.connect() as connection:
        assert await connection.scalar(
            text("SELECT state FROM attempts WHERE id=:id"), {"id": item.attempt}
        ) == (target if source == "active" else source)


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled", "timed_out"])
async def test_terminal_task_result_cannot_be_replaced(
    client: AsyncClient,
    command_owner: dict[str, str],
    migration_engine: AsyncEngine,
    terminal: str,
) -> None:
    item = await subject(client, command_owner, migration_engine)
    async with migration_engine.begin() as connection:
        await step_state(connection, item, "cancelled")
        await task_state(connection, item, "running")
        await task_state(connection, item, terminal)
    with pytest.raises(DBAPIError) as failure:
        async with migration_engine.begin() as connection:
            await connection.execute(
                text("UPDATE tasks SET result='{}'::jsonb WHERE id=:id"), {"id": item.task}
            )
    assert getattr(failure.value.orig, "sqlstate", None) == "23514"


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
async def test_terminal_step_cannot_be_reassigned(
    client: AsyncClient,
    command_owner: dict[str, str],
    migration_engine: AsyncEngine,
    terminal: str,
) -> None:
    item = await subject(client, command_owner, migration_engine)
    async with migration_engine.begin() as connection:
        await seed_step(connection, item, terminal)
    with pytest.raises(DBAPIError) as failure:
        async with migration_engine.begin() as connection:
            await connection.execute(
                text("UPDATE steps SET generation=generation+1 WHERE id=:id"), {"id": item.step}
            )
    assert getattr(failure.value.orig, "sqlstate", None) == "23514"


@pytest.mark.parametrize("terminal", ["succeeded", "failed", "expired", "revoked"])
async def test_closed_attempt_cannot_be_reassigned(
    client: AsyncClient,
    command_owner: dict[str, str],
    migration_engine: AsyncEngine,
    terminal: str,
) -> None:
    item = await subject(client, command_owner, migration_engine)
    async with migration_engine.begin() as connection:
        await seed_step(connection, item, "running")
        await close_attempt(connection, item, terminal)
        await step_state(connection, item, "ready")
    with pytest.raises(DBAPIError) as failure:
        async with migration_engine.begin() as connection:
            await connection.execute(
                text("UPDATE attempts SET generation=generation+1 WHERE id=:id"),
                {"id": item.attempt},
            )
    assert getattr(failure.value.orig, "sqlstate", None) == "23514"


async def test_second_active_attempt_rejected_and_replacement_requires_closure(
    client: AsyncClient, command_owner: dict[str, str], migration_engine: AsyncEngine
) -> None:
    item = await subject(client, command_owner, migration_engine)
    async with migration_engine.begin() as connection:
        await seed_step(connection, item, "running")
    with pytest.raises(DBAPIError) as failure:
        async with migration_engine.begin() as connection:
            await active_attempt(connection, item, 2)
    assert getattr(failure.value.orig, "sqlstate", None) == "23505"
    async with migration_engine.begin() as connection:
        await close_attempt(connection, item, "expired")
        await active_attempt(connection, item, 2)
    async with migration_engine.connect() as connection:
        rows = (
            await connection.execute(
                text("SELECT attempt_number,state FROM attempts WHERE step_id=:id ORDER BY 1"),
                {"id": item.step},
            )
        ).all()
    assert [tuple(row) for row in rows] == [(1, "expired"), (2, "active")]


@pytest.mark.parametrize("boundary", ["step", "event", "command", "attempt", "task_key"])
async def test_real_foreign_owner_and_task_identifiers_rejected(
    client: AsyncClient,
    command_owner: dict[str, str],
    migration_engine: AsyncEngine,
    admin_engine: AsyncEngine,
    boundary: str,
) -> None:
    item = await subject(client, command_owner, migration_engine)
    foreign_owner = await provision_principal(sessions(admin_engine), "Matrix foreign owner")
    foreign_key, foreign_token = await issue_key(sessions(admin_engine), foreign_owner)
    foreign = await create(
        client, {**command_owner, "Authorization": f"Bearer {foreign_token}"}, "foreign"
    )
    statements = {
        "step": (
            "INSERT INTO steps(id,task_id,principal_id,path,state) "
            "VALUES(:new_id,:task,:owner,'foreign','cancelled')"
        ),
        "event": (
            "INSERT INTO task_events(task_id,sequence,principal_id,kind,data) "
            "VALUES(:task,2,:owner,'fixture','{}')"
        ),
        "command": (
            "INSERT INTO idempotency_records(principal_id,route,key,canonical_version,"
            "fingerprint,task_id,response_status,response_body) "
            "VALUES(:owner,'/v1/tasks','foreign-collision','v1',repeat('a',64),:task,202,'{}')"
        ),
        "attempt": (
            "INSERT INTO attempts(id,task_id,step_id,attempt_number,generation,state) "
            "VALUES(:new_id,:foreign_task,:step,1,1,'expired')"
        ),
        "task_key": (
            "INSERT INTO tasks(id,principal_id,key_id,workflow_name,workflow_version,"
            "versions,input,limits,execution_epoch,state,deadline_at,storage_bytes) "
            "SELECT :new_id,principal_id,:foreign_key,workflow_name,workflow_version,"
            "versions,input,limits,execution_epoch,'queued',deadline_at,storage_bytes "
            "FROM tasks WHERE id=:task"
        ),
    }
    with pytest.raises(DBAPIError) as failure:
        async with migration_engine.begin() as connection:
            await connection.execute(
                text(statements[boundary]),
                {
                    "new_id": uuid4(),
                    "task": item.task,
                    "step": item.step,
                    "owner": foreign_owner,
                    "foreign_task": UUID(str(foreign["task_id"])),
                    "foreign_key": foreign_key,
                },
            )
    assert getattr(failure.value.orig, "sqlstate", None) == "23503"
