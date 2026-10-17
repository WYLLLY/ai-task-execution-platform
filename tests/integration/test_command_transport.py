"""Transport ambiguity and real Redis limiter semantics around durable commands."""

import json

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy.exc import DBAPIError

from task_platform.command_schemas import CommandError, parse_command
from task_platform.db import create_engine, sessions
from task_platform.rate_limits import creation_limit
from task_platform.tasks import CommandResponse, commit_command, submit_task

from .conftest import settings_for
from .test_commands import BODY

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("committed", [True, False])
async def test_lost_commit_ack_reads_stable_identity_without_retry(
    client: AsyncClient, command_owner: dict[str, str], committed: bool
) -> None:
    settings = settings_for("api")
    assert settings.redis_url is not None
    engine = create_engine(settings)
    redis = Redis.from_url(settings.redis_url.get_secret_value())
    value, fingerprint = parse_command(json.dumps(BODY).encode())
    token = command_owner["Authorization"].removeprefix("Bearer ")
    invocations = 0
    captured: CommandResponse | None = None

    async def ambiguous() -> CommandResponse:
        nonlocal invocations, captured
        invocations += 1
        if committed:
            captured = await submit_task(
                sessions(engine),
                redis,
                settings,
                token,
                "lost-ack",
                value,
                fingerprint,
                command_owner["X-Execution-Epoch"],
            )
        # Only the missing acknowledgement is simulated; commit and reread use real PostgreSQL.
        raise DBAPIError(
            None, None, OSError("synthetic connection loss"), connection_invalidated=True
        )

    try:
        if committed:
            result = await commit_command(
                ambiguous, sessions(engine), token, "/v1/tasks", "lost-ack", fingerprint
            )
            assert result == captured and result.status == 202
        else:
            with pytest.raises(DBAPIError):
                await commit_command(
                    ambiguous, sessions(engine), token, "/v1/tasks", "lost-ack", fingerprint
                )
        assert invocations == 1
        items = (await client.get("/v1/tasks", headers=command_owner)).json()["items"]
        assert len(items) == int(committed)
    finally:
        await redis.aclose()
        await engine.dispose()


async def test_redis_limiter_ttl_rejection_and_durable_replay(
    client: AsyncClient, command_owner: dict[str, str]
) -> None:
    settings = settings_for("api")
    assert settings.redis_url is not None
    redis = Redis.from_url(settings.redis_url.get_secret_value())
    identity = (await client.get("/v1/me", headers=command_owner)).json()
    key = identity["key_id"]
    headers = {**command_owner, "Idempotency-Key": "limited"}
    try:
        first = await client.post("/v1/tasks", json=BODY, headers=headers)
        assert first.status_code == 202
        for _ in range(59):
            await creation_limit(redis, key)
        assert 0 < await redis.pttl(f"creation:{key}") <= 60000
        with pytest.raises(CommandError) as error:
            await creation_limit(redis, key)
        assert error.value.status == 429
        replay = await client.post("/v1/tasks", json=BODY, headers=headers)
        assert replay.status_code == 202 and replay.content == first.content
        rejected = await client.post(
            "/v1/tasks", json=BODY, headers={**command_owner, "Idempotency-Key": "new"}
        )
        assert rejected.status_code == 429
    finally:
        await redis.delete(f"creation:{key}")
        await redis.aclose()
