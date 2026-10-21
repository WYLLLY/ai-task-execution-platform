"""Owner-authorized regeneration must not select old evidence or reset budgets."""

import asyncio
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine
from task_platform.execution import Executor

from .conftest import settings_for
from .test_commands import create
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch

pytestmark = pytest.mark.integration


async def test_resolve_replay_concurrent_version_and_late_old_result(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    item = await create(client, command_owner)
    lease = await executor.claim("old")
    assert lease
    old = uuid4()
    assert await executor.authorize(lease, old, b"{}")
    assert await executor.complete(lease, old, "unknown")
    snapshot = (await client.get(f"/v1/tasks/{item['task_id']}", headers=command_owner)).json()
    body = {
        "action": "regenerate",
        "accept_overlap_cost_risk": True,
        "expected_task_version": snapshot["task_version"],
    }
    url = f"/v1/tasks/{item['task_id']}/resolve"
    responses = await asyncio.gather(
        *(
            client.post(
                url, json=body, headers={**command_owner, "Idempotency-Key": f"resolve-{i}"}
            )
            for i in range(2)
        )
    )
    assert sorted(r.status_code for r in responses) == [200, 409]
    winner = next(i for i, r in enumerate(responses) if r.status_code == 200)
    assert responses[winner].json()["state"] == "queued"
    replay = await client.post(
        url, json=body, headers={**command_owner, "Idempotency-Key": f"resolve-{winner}"}
    )
    assert replay.content == responses[winner].content
    assert not await executor.complete(lease, old, "succeeded", cost=8, tokens=4)
    replacement = await executor.claim("new")
    assert replacement and replacement.generation > lease.generation
    new = uuid4()
    assert await executor.authorize(replacement, new, b"{}")
    assert await executor.complete(replacement, new, "succeeded", cost=10, tokens=5)
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 2
        assert await c.scalar(text("SELECT count(*) FROM call_costs")) == 2
        assert await c.scalar(text("SELECT sum(confirmed_cost) FROM call_costs")) == 18
        assert await c.scalar(text("SELECT selected_call_id FROM steps")) == new
        assert (
            await c.scalar(text("SELECT count(*) FROM task_events WHERE kind='task.succeeded'"))
            == 1
        )


@pytest.mark.parametrize(
    "body",
    [
        {"action": "regenerate", "expected_task_version": 1},
        {"action": "regenerate", "expected_task_version": 1, "accept_overlap_cost_risk": False},
        {"action": "succeed", "expected_task_version": 1, "accept_overlap_cost_risk": True},
    ],
)
async def test_resolve_requires_explicit_risk_and_never_fabricates_success(
    client: AsyncClient,
    command_owner: dict[str, str],
    body: dict[str, object],
) -> None:
    item = await create(client, command_owner)
    result = await client.post(
        f"/v1/tasks/{item['task_id']}/resolve",
        json=body,
        headers={**command_owner, "Idempotency-Key": "resolve"},
    )
    assert result.status_code == 422


async def test_resolve_cross_owner_and_quarantine_are_denied(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
) -> None:
    body = {"action": "regenerate", "expected_task_version": 1, "accept_overlap_cost_risk": True}
    result = await client.post(
        f"/v1/tasks/{uuid4()}/resolve",
        json=body,
        headers={**command_owner, "Idempotency-Key": "foreign"},
    )
    assert result.status_code == 404
    item = await create(client, command_owner)
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE tasks SET state='suspended',reason='restore_quarantine',task_version=2"
                )
            )
        body["expected_task_version"] = 2
        result = await client.post(
            f"/v1/tasks/{item['task_id']}/resolve",
            json=body,
            headers={**command_owner, "Idempotency-Key": "quarantine"},
        )
        assert result.status_code == 409
    finally:
        await engine.dispose()


async def test_resolve_expired_task_cleans_children_instead_of_regenerating(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    item = await create(client, command_owner)
    lease = await executor.claim("worker")
    assert lease
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    assert await executor.complete(lease, call, "unknown")
    snapshot = (await client.get(f"/v1/tasks/{item['task_id']}", headers=command_owner)).json()
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE tasks SET created_at=clock_timestamp()-interval '2 minutes',"
                    "deadline_at=clock_timestamp()-interval '1 minute'"
                )
            )
        response = await client.post(
            f"/v1/tasks/{item['task_id']}/resolve",
            json={
                "action": "regenerate",
                "accept_overlap_cost_risk": True,
                "expected_task_version": snapshot["task_version"],
            },
            headers={**command_owner, "Idempotency-Key": "expired"},
        )
        assert response.status_code == 200 and response.json()["state"] == "timed_out"
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT state FROM steps")) == "cancelled"
            assert await c.scalar(text("SELECT exposure_cost FROM call_costs")) == 4004
    finally:
        await engine.dispose()


async def test_resolve_existing_foreign_task_is_not_disclosed(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
) -> None:
    from task_platform.db import sessions
    from task_platform.identity import issue_key, provision_principal

    item = await create(client, command_owner)
    owner = await provision_principal(sessions(admin_engine), "other owner")
    _, token = await issue_key(sessions(admin_engine), owner)
    response = await client.post(
        f"/v1/tasks/{item['task_id']}/resolve",
        json={"forged": True},
        headers={"Authorization": f"Bearer {token}", "Idempotency-Key": "cross-owner"},
    )
    assert response.status_code == 404
    assert response.json() == {"detail": "Not found"}
