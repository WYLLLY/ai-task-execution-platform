"""Deterministic ASGI transport/authentication tests, separate from PostgreSQL proofs."""

import asyncio
from uuid import uuid4

import pytest
from starlette.types import Message

from task_platform.command_schemas import CommandError
from task_platform.event_reads import Batch, Event
from task_platform.identity import Identity
from task_platform.sse import AuthLease, Connections, Stream


def test_connections_bound_owner_and_global_and_release_idempotently() -> None:
    limits = Connections()
    owners = [uuid4() for _ in range(20)]
    tickets = [limits.acquire(owner) for owner in owners for _ in range(5)]
    assert limits.active == 100
    with pytest.raises(CommandError) as error:
        limits.acquire(uuid4())
    assert error.value.status == 429
    tickets.pop()()
    with pytest.raises(CommandError):
        limits.acquire(owners[0])
    release = tickets.pop()
    release()
    release()
    assert limits.active == 98
    for ticket in tickets:
        ticket()
    assert limits.active == 0


async def test_auth_validity_counts_from_check_start_and_failure_never_renews() -> None:
    now = [0.0]
    calls = []

    async def check() -> None:
        calls.append(now[0])
        now[0] += 9

    lease = AuthLease(check, clock=lambda: now[0])
    await lease.ensure()
    assert lease.valid_until == 10
    now[0] = 9.5

    async def failure() -> None:
        raise OSError("private-canary")

    lease.check = failure
    with pytest.raises(OSError):
        await lease.ensure()
    assert lease.valid_until == 10
    assert calls == [0]


async def test_expired_during_auth_check_cannot_write() -> None:
    now = [0.0]

    async def check() -> None:
        now[0] += 11

    lease = AuthLease(check, clock=lambda: now[0])
    with pytest.raises(TimeoutError):
        await lease.ensure()


async def test_transport_timeout_releases_ticket_without_advancing_cursor() -> None:
    task = uuid4()
    identity = Identity(uuid4(), uuid4())
    release = []
    sent = []

    async def check() -> None:
        return None

    async def send(message: Message) -> None:
        sent.append(message)
        if message["type"] == "http.response.body" and message.get("body"):
            await asyncio.Event().wait()

    async def receive() -> dict[str, object]:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    stream = Stream(
        task,
        identity,
        Batch(0, 1, 1, (Event(1, b"id: initial\n\n"),)),
        AuthLease(check),
        None,
        lambda: release.append(True),
        write_timeout=0.02,
    )
    async with asyncio.timeout(0.5):
        await stream({}, receive, send)
    assert release == [True]
    assert stream.position == 0
    assert sent[0]["type"] == "http.response.start"


async def test_disconnect_cancels_wait_and_releases_connection() -> None:
    release = []

    async def check() -> None:
        return None

    async def send(message: Message) -> None:
        return None

    async def receive() -> dict[str, object]:
        return {"type": "http.disconnect"}

    stream = Stream(
        uuid4(),
        Identity(uuid4(), uuid4()),
        Batch(0, 0, 1, ()),
        AuthLease(check),
        None,
        lambda: release.append(True),
    )
    await stream({}, receive, send)
    assert release == [True]
