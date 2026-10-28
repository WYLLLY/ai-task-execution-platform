"""Real TCP/proxy replay, revocation and post-header retention. Synthetic data only."""

import asyncio
import time
from collections.abc import AsyncIterator
from uuid import UUID

import pytest
from httpx import AsyncClient, RemoteProtocolError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.identity import revoke_key

from .conftest import settings_for
from .test_commands import create

pytestmark = pytest.mark.integration


async def publish_event(engine: AsyncEngine, task: UUID) -> int:
    async with engine.begin() as c:
        row = (
            await c.execute(
                text(
                    "UPDATE tasks SET event_sequence=event_sequence+1 "
                    "WHERE id=:id RETURNING event_sequence,principal_id,task_version,state"
                ),
                {"id": task},
            )
        ).one()
        await c.execute(
            text(
                "INSERT INTO task_events(task_id,sequence,principal_id,kind,data) "
                "VALUES(:task,:sequence,:owner,'usage.updated',"
                "jsonb_build_object('state',CAST(:state AS text),"
                "'task_version',CAST(:version AS bigint)))"
            ),
            {"task": task, "sequence": row[0], "owner": row[1], "version": row[2], "state": row[3]},
        )
    return int(row[0])


async def frame(lines: AsyncIterator[str]) -> list[str]:
    result: list[str] = []
    async for line in lines:
        if line == "":
            if result:
                return result
        else:
            result.append(line)
    return result


async def test_real_proxy_flushes_contiguous_replay_and_reconnect_without_redis_hint(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    item = await create(client, command_owner)
    task = UUID(str(item["task_id"]))
    async with AsyncClient(base_url="http://proxy:8080", trust_env=False, timeout=4) as network:
        assert (await network.get("/metrics")).status_code == 404
        async with network.stream(
            "GET", f"/v1/tasks/{task}/events", headers=command_owner
        ) as response:
            assert response.status_code == 200
            assert response.headers["content-type"].startswith("text/event-stream")
            lines = response.aiter_lines()
            assert f"id: {task}:1" in await frame(lines)
            started = time.monotonic()
            assert await publish_event(admin_engine, task) == 2
            event = await frame(lines)
            assert f"id: {task}:2" in event
            assert time.monotonic() - started < 2, "proxy buffered a committed progress event"
        assert await publish_event(admin_engine, task) == 3
        async with network.stream(
            "GET",
            f"/v1/tasks/{task}/events",
            headers={**command_owner, "Last-Event-ID": f"{task}:2"},
        ) as response:
            assert f"id: {task}:3" in await frame(response.aiter_lines())


async def test_real_proxy_retention_during_stream_resets_without_new_id(
    client: AsyncClient, command_owner: dict[str, str]
) -> None:
    item = await create(client, command_owner)
    task = UUID(str(item["task_id"]))
    engine = create_engine(settings_for("migrator"))
    try:
        async with AsyncClient(base_url="http://proxy:8080", trust_env=False, timeout=4) as network:
            async with network.stream(
                "GET", f"/v1/tasks/{task}/events", headers=command_owner
            ) as response:
                lines = response.aiter_lines()
                assert f"id: {task}:1" in await frame(lines)
                async with engine.begin() as c:
                    await c.execute(
                        text(
                            "UPDATE tasks SET event_sequence=2,oldest_retained_sequence=3 "
                            "WHERE id=:id"
                        ),
                        {"id": task},
                    )
                    await c.execute(text("DELETE FROM task_events WHERE task_id=:id"), {"id": task})
                reset = await frame(lines)
                assert "event: stream.reset" in reset
                assert not any(line.startswith("id:") for line in reset)
                assert await frame(lines) == []
    finally:
        await engine.dispose()


@pytest.mark.parametrize("failure", ["revoked", "authorization_db_blocked"])
async def test_stream_closes_within_auth_bound_under_real_revocation_or_db_failure(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine, failure: str
) -> None:
    item = await create(client, command_owner)
    task = UUID(str(item["task_id"]))
    engine = create_engine(settings_for("migrator"))
    async with admin_engine.connect() as c:
        owner, key = (
            await c.execute(
                text("SELECT principal_id,key_id FROM tasks WHERE id=:id"), {"id": task}
            )
        ).one()
    try:
        async with AsyncClient(base_url="http://api:8000", trust_env=False, timeout=11) as network:
            async with network.stream(
                "GET", f"/v1/tasks/{task}/events", headers=command_owner
            ) as response:
                lines = response.aiter_lines()
                assert f"id: {task}:1" in await frame(lines)
                async with engine.begin() as locked:
                    if failure == "revoked":
                        await revoke_key(sessions(admin_engine), owner, key)
                    else:
                        await locked.execute(
                            text("SET LOCAL idle_in_transaction_session_timeout='15s'")
                        )
                        await locked.execute(text("LOCK TABLE api_keys IN ACCESS EXCLUSIVE MODE"))
                    started = time.monotonic()
                    try:
                        async with asyncio.timeout(10):
                            async for _line in lines:
                                assert time.monotonic() - started < 10
                    except RemoteProtocolError:
                        pass  # Closing an invalidated stream may abort a chunked response.
                    assert time.monotonic() - started < 10
    finally:
        await engine.dispose()


async def test_proxy_keeps_the_current_api_target_after_container_replacement() -> None:
    async with AsyncClient(trust_env=False, timeout=4) as network:
        direct = await network.get("http://api:8000/v1/system")
        proxied = await network.get("http://proxy:8080/v1/system")
        assert "schema_compatible" in direct.json()
        assert proxied.status_code == direct.status_code
        assert "schema_compatible" in proxied.json(), "proxy retained a replaced container address"
