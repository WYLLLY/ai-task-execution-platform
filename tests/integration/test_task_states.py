"""F03 and negative SQL gates; no executor is introduced by these fixtures."""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from task_platform.db import create_engine

from .conftest import settings_for
from .test_commands import create

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "task_state,step_state,attempt_state",
    [
        ("queued", "ready", None),
        ("running", "running", "active"),
        ("running", "retry_wait", "failed"),
        ("running", "ready", "expired"),
        ("suspended", "unknown", "expired"),
        ("suspended", "ready", "revoked"),
    ],
)
@pytest.mark.parametrize("expired", [False, True])
async def test_f03_cancel_cleans_every_representable_nonterminal_combination(
    client: AsyncClient,
    command_owner: dict[str, str],
    task_state: str,
    step_state: str,
    attempt_state: str | None,
    expired: bool,
) -> None:
    first = await create(client, command_owner)
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            task_id = first["task_id"]
            step_id = await c.scalar(
                text("SELECT id FROM steps WHERE task_id=:id"), {"id": task_id}
            )
            if task_state != "queued":
                await c.execute(
                    text(
                        "UPDATE tasks SET state='running',task_version=task_version+1 WHERE id=:id"
                    ),
                    {"id": task_id},
                )
                await c.execute(
                    text(
                        "UPDATE steps SET "
                        "state='running',generation=1,holder='fixture',lease_until=clock_t"
                        "imestamp()+interval '60 seconds' WHERE id=:id"
                    ),
                    {"id": step_id},
                )
                await c.execute(
                    text(
                        "INSERT INTO "
                        "attempts(id,task_id,step_id,attempt_number,state,generation) "
                        "VALUES(:id,:task,:step,1,'active',1)"
                    ),
                    {"id": uuid4(), "task": task_id, "step": step_id},
                )
                if attempt_state != "active":
                    await c.execute(
                        text("UPDATE attempts SET state=:state WHERE step_id=:id"),
                        {"state": attempt_state, "id": step_id},
                    )
                    await c.execute(
                        text(
                            "UPDATE steps SET state=:state,holder=NULL,lease_until=NULL WHERE "
                            "id=:id"
                        ),
                        {"state": step_state, "id": step_id},
                    )
                if task_state == "suspended":
                    await c.execute(
                        text(
                            "UPDATE tasks SET "
                            "state='suspended',reason='fixture',task_version=task_version+1 "
                            "WHERE id=:id"
                        ),
                        {"id": task_id},
                    )
        if expired:
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "UPDATE tasks SET created_at=clock_timestamp()-interval '2 hours', "
                        "deadline_at=clock_timestamp()-interval '1 hour' WHERE id=:id"
                    ),
                    {"id": first["task_id"]},
                )
        snapshot = (await client.get(f"/v1/tasks/{first['task_id']}", headers=command_owner)).json()
        cancelled = await client.post(
            f"/v1/tasks/{first['task_id']}/cancel",
            json={"expected_task_version": snapshot["task_version"]},
            headers={**command_owner, "Idempotency-Key": "cancel"},
        )
        assert cancelled.status_code == 200 and cancelled.json()["state"] == (
            "timed_out" if expired else "cancelled"
        )
        async with engine.connect() as c:
            rows = (
                await c.execute(
                    text("SELECT state,holder,lease_until,generation FROM steps WHERE task_id=:id"),
                    {"id": first["task_id"]},
                )
            ).all()
            assert all(
                r.state == "cancelled"
                and r.holder is None
                and r.lease_until is None
                and r.generation > 0
                for r in rows
            )
            assert (
                await c.scalar(
                    text("SELECT count(*) FROM attempts WHERE task_id=:id AND state='active'"),
                    {"id": first["task_id"]},
                )
                == 0
            )
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE tasks SET state='succeeded',task_version=2 WHERE id=:id",
        "UPDATE tasks SET state='bogus',task_version=2 WHERE id=:id",
        "UPDATE tasks SET state='cancelled',task_version=2 WHERE id=:id",
        "UPDATE steps SET state='succeeded' WHERE task_id=:id",
        "UPDATE steps SET state='running' WHERE task_id=:id",
        (
            "INSERT INTO steps(id,task_id,principal_id,path,state) SELECT "
            "gen_random_uuid(),id,principal_id,'second','ready' FROM tasks "
            "WHERE id=:id"
        ),
        (
            "INSERT INTO steps(id,task_id,principal_id,path,state) SELECT "
            "gen_random_uuid(),id,gen_random_uuid(),'foreign','ready' FROM "
            "tasks WHERE id=:id"
        ),
    ],
)
async def test_illegal_arcs_coupling_and_unique_ready_constraints(
    client: AsyncClient, command_owner: dict[str, str], statement: str
) -> None:
    first = await create(client, command_owner)
    engine = create_engine(settings_for("migrator"))
    try:
        with pytest.raises(DBAPIError) as error:
            async with engine.begin() as c:
                await c.execute(text(statement), {"id": first["task_id"]})
        assert getattr(error.value.orig, "sqlstate", "") in {"23514", "23503", "23505"}
    finally:
        await engine.dispose()


async def test_terminal_task_immutable_and_runtime_grants(
    client: AsyncClient, command_owner: dict[str, str]
) -> None:
    first = await create(client, command_owner)
    cancelled = await client.post(
        f"/v1/tasks/{first['task_id']}/cancel",
        json={"expected_task_version": 1},
        headers={**command_owner, "Idempotency-Key": "cancel"},
    )
    assert cancelled.status_code == 200
    engine = create_engine(settings_for("migrator"))
    try:
        with pytest.raises(DBAPIError) as error:
            async with engine.begin() as c:
                await c.execute(
                    text("UPDATE tasks SET state='queued',task_version=3 WHERE id=:id"),
                    {"id": first["task_id"]},
                )
        assert getattr(error.value.orig, "sqlstate", "") == "23514"
    finally:
        await engine.dispose()
    api = create_engine(settings_for("api"))
    try:
        for statement in (
            "DELETE FROM task_events",
            "UPDATE idempotency_records SET response_status=500",
            "UPDATE workflow_versions SET available=false",
            "UPDATE platform_control SET admission_open=true",
        ):
            with pytest.raises(DBAPIError) as error:
                async with api.begin() as c:
                    await c.execute(text(statement))
            assert getattr(error.value.orig, "sqlstate", "") == "42501"
    finally:
        await api.dispose()
