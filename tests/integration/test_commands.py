"""M2 behavioral contracts, written against M1 before command implementation."""

import asyncio
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.identity import issue_key, provision_principal, revoke_key

from .conftest import settings_for

pytestmark = pytest.mark.integration
BODY = {"workflow": {"name": "analysis", "version": "1"}, "input": {"texts": ["Synthetic dossier"]}}


async def create(
    client: AsyncClient, headers: dict[str, str], key: str = "create-1"
) -> dict[str, object]:
    response = await client.post(
        "/v1/tasks", json=BODY, headers={**headers, "Idempotency-Key": key}
    )
    assert response.status_code == 202, response.text
    return dict(response.json())


async def test_creation_persists_pins_step_event_and_retained_response(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    response = await create(client, command_owner)
    snapshot = await client.get(f"/v1/tasks/{response['task_id']}", headers=command_owner)
    assert snapshot.status_code == 200
    assert snapshot.json()["state"] == "queued"
    assert snapshot.json()["task_version"] == snapshot.json()["event_sequence"] == 1
    assert snapshot.json()["versions"] == {
        "workflow": "1",
        "prompt": "1",
        "tools": "1",
        "context_builder": "1",
    }
    steps = await client.get(f"/v1/tasks/{response['task_id']}/steps", headers=command_owner)
    assert steps.status_code == 200 and len(steps.json()["items"]) == 1
    assert steps.json()["items"][0]["state"] == "ready"
    async with admin_engine.connect() as c:
        assert (
            await c.scalar(
                text("SELECT count(*) FROM task_events WHERE task_id=:id"),
                {"id": response["task_id"]},
            )
            == 1
        )
        assert (
            await c.scalar(
                text("SELECT count(*) FROM idempotency_records WHERE task_id=:id"),
                {"id": response["task_id"]},
            )
            == 1
        )
        assert await c.scalar(text("SELECT open_tasks FROM platform_control WHERE id=1")) == 1


async def test_f01_replay_full_quota_retired_workflow_closed_admission_and_stale_epoch(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    first = await create(client, command_owner)
    for i in range(4):
        await create(client, command_owner, f"fill-{i}")
    rejected = await client.post(
        "/v1/tasks", json=BODY, headers={**command_owner, "Idempotency-Key": "sixth"}
    )
    assert rejected.status_code == 429
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE workflow_versions SET available=false"))
        await c.execute(text("UPDATE platform_control SET admission_open=false"))
    replay = await client.post(
        "/v1/tasks",
        json=BODY,
        headers={**command_owner, "X-Execution-Epoch": str(uuid4()), "Idempotency-Key": "create-1"},
    )
    assert replay.status_code == 202 and replay.json() == first
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT open_tasks FROM platform_control")) == 5


async def test_canonical_replay_and_collision(
    client: AsyncClient, command_owner: dict[str, str]
) -> None:
    first = await create(client, command_owner)
    headers = {**command_owner, "Idempotency-Key": "create-1"}
    equivalent = (
        b'{"input":{"texts":["Synthetic dossier"]},"workflow":{"version":"1","name":"analysis"}}'
    )
    replay = await client.post("/v1/tasks", content=equivalent, headers=headers)
    assert replay.status_code == 202 and replay.json() == first
    for body in ({**BODY, "limits": None}, {**BODY, "input": {"texts": ["different"]}}):
        response = await client.post("/v1/tasks", json=body, headers=headers)
        assert response.status_code == 409
    numeric = {**BODY, "limits": {"deadline_seconds": 300}}
    headers["Idempotency-Key"] = "numeric"
    first_num = await client.post("/v1/tasks", json=numeric, headers=headers)
    assert first_num.status_code == 202
    encoded = (
        b'{"workflow":{"name":"analysis","version":"1"},"input":{"texts":["'
        b'Synthetic dossier"]},"limits":{"deadline_seconds":3e2}}'
    )
    replay_num = await client.post("/v1/tasks", content=encoded, headers=headers)
    assert replay_num.status_code == 202 and replay_num.json() == first_num.json()


@pytest.mark.parametrize(
    "body",
    [
        b'{"workflow":{},"workflow":{}}',
        b'{"input":NaN}',
        b'{"input":Infinity}',
        b'{"input":1e999999}',
        b'{"input":"\\ud800"}',
        b"[" * 100 + b"0" + b"]" * 100,
        b"not JSON",
        b"{}",
    ],
)
async def test_strict_json_rejects_invalid_before_mutation(
    client: AsyncClient, command_owner: dict[str, str], body: bytes
) -> None:
    response = await client.post(
        "/v1/tasks", content=body, headers={**command_owner, "Idempotency-Key": "invalid"}
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "Invalid request"}


@pytest.mark.parametrize(
    "patch",
    [
        {"unexpected": True},
        {"workflow": {"name": "missing", "version": "1"}},
        {"input": {"texts": []}},
        {"input": {"texts": ["x"] * 21}},
        {"input": {"texts": [1]}},
        {"input": {"texts": ["x"], "url": "http://localhost"}},
        {"limits": {"deadline_seconds": 901}},
        {"limits": {"cost_micro_usd": 1.5}},
    ],
)
async def test_schema_and_server_limits(
    client: AsyncClient, command_owner: dict[str, str], patch: dict[str, object]
) -> None:
    response = await client.post(
        "/v1/tasks", json={**BODY, **patch}, headers={**command_owner, "Idempotency-Key": "invalid"}
    )
    assert response.status_code in (404, 422)
    # A missing route is not a valid Red for schema checks: successful catalogue proves M2 boundary.
    catalogue = await client.get("/v1/workflows", headers=command_owner)
    assert catalogue.status_code == 200


async def test_epoch_key_and_payload_boundaries(
    client: AsyncClient, command_owner: dict[str, str]
) -> None:
    no_epoch = {"Authorization": command_owner["Authorization"], "Idempotency-Key": "one"}
    assert (await client.post("/v1/tasks", json=BODY, headers=no_epoch)).status_code == 428
    assert (
        await client.post(
            "/v1/tasks", json=BODY, headers={**no_epoch, "X-Execution-Epoch": str(uuid4())}
        )
    ).status_code == 409
    assert (await client.post("/v1/tasks", json=BODY, headers=command_owner)).status_code == 422
    huge = await client.post(
        "/v1/tasks",
        content=b"x" * (1024 * 1024 + 1),
        headers={**command_owner, "Idempotency-Key": "huge"},
    )
    assert huge.status_code == 413


async def test_owner_isolation_pagination_and_revoked_replay(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    first = await create(client, command_owner)
    await create(client, command_owner, "second")
    page = await client.get("/v1/tasks?limit=1", headers=command_owner)
    assert page.status_code == 200 and len(page.json()["items"]) == 1
    cursor = page.json()["next_cursor"]
    assert cursor
    next_page = await client.get(
        "/v1/tasks", params={"limit": 1, "cursor": cursor}, headers=command_owner
    )
    assert (
        next_page.status_code == 200
        and next_page.json()["items"][0]["task_id"] != page.json()["items"][0]["task_id"]
    )
    owner_b = await provision_principal(sessions(admin_engine), "Foreign")
    _, token_b = await issue_key(sessions(admin_engine), owner_b)
    other = {"Authorization": f"Bearer {token_b}"}
    for suffix in ("", "/steps"):
        assert (
            await client.get(f"/v1/tasks/{first['task_id']}{suffix}", headers=other)
        ).status_code == 404
    assert (
        await client.get("/v1/tasks", params={"cursor": cursor}, headers=other)
    ).status_code == 400
    denied = await client.post(
        f"/v1/tasks/{first['task_id']}/cancel",
        json={"expected_task_version": 1},
        headers={**other, "Idempotency-Key": "cancel"},
    )
    assert denied.status_code == 404
    identity = (await client.get("/v1/me", headers=command_owner)).json()
    await revoke_key(
        sessions(admin_engine), UUID(identity["principal_id"]), UUID(identity["key_id"])
    )
    assert (
        await client.post(
            "/v1/tasks", json=BODY, headers={**command_owner, "Idempotency-Key": "create-1"}
        )
    ).status_code == 401


async def test_cancel_idempotence_version_and_deadline(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    first = await create(client, command_owner)
    route = f"/v1/tasks/{first['task_id']}/cancel"
    headers = {**command_owner, "Idempotency-Key": "cancel"}
    stale = await client.post(route, json={"expected_task_version": 2}, headers=headers)
    assert stale.status_code == 409
    cancelled = await client.post(route, json={"expected_task_version": 1}, headers=headers)
    assert cancelled.status_code == 200 and cancelled.json()["state"] == "cancelled"
    assert cancelled.json()["task_version"] == 2
    replay = await client.post(route, json={"expected_task_version": 1}, headers=headers)
    assert replay.status_code == 200 and replay.json() == cancelled.json()
    steps = await client.get(f"/v1/tasks/{first['task_id']}/steps", headers=command_owner)
    assert steps.json()["items"][0]["state"] == "cancelled"
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT open_tasks FROM platform_control")) == 0
    second = await create(client, command_owner, "deadline")
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE tasks SET created_at=clock_timestamp()-interval '2 "
                    "hours', deadline_at=clock_timestamp()-interval '1 hour' WHERE "
                    "id=:id"
                ),
                {"id": second["task_id"]},
            )
    finally:
        await engine.dispose()
    expired = await client.post(
        f"/v1/tasks/{second['task_id']}/cancel", json={"expected_task_version": 1}, headers=headers
    )
    assert expired.status_code == 200 and expired.json()["state"] == "timed_out"


@pytest.mark.parametrize("same_key", [True, False])
async def test_f02_real_concurrent_admission(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine, same_key: bool
) -> None:
    for i in range(4):
        await create(client, command_owner, f"fill-{i}")
    observer = create_engine(settings_for("admin"))
    try:
        async with admin_engine.begin() as holder:
            await holder.execute(text("SELECT id FROM platform_control FOR UPDATE"))
            calls = [
                asyncio.create_task(
                    client.post(
                        "/v1/tasks",
                        json=BODY,
                        headers={
                            **command_owner,
                            "Idempotency-Key": "race" if same_key else f"race-{i}",
                        },
                    )
                )
                for i in range(2)
            ]
            async with asyncio.timeout(0.8), observer.connect() as c:
                await c.execution_options(isolation_level="AUTOCOMMIT")
                while (  # noqa: ASYNC110 -- observe actual PostgreSQL waits
                    await c.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE usename='atp_api' "
                            "AND cardinality(pg_blocking_pids(pid))>0"
                        )
                    )
                ) != 2:
                    await asyncio.sleep(0.005)
        results = await asyncio.gather(*calls)
        assert sorted(r.status_code for r in results) == ([202, 202] if same_key else [202, 429])
        if same_key:
            assert results[0].json() == results[1].json()
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT open_tasks FROM platform_control")) == 5
    finally:
        await observer.dispose()
