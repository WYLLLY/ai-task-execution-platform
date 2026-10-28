"""Deterministic ASGI transport/authentication tests, separate from PostgreSQL proofs."""

import asyncio
from uuid import UUID, uuid4

import pytest
from starlette.types import Message

from task_platform.command_schemas import CommandError
from task_platform.event_cursor import HistoryExpired
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


async def test_retention_gap_while_draining_backlog_emits_reset_without_id() -> None:
    task = uuid4()
    sent: list[bytes] = []

    class Purged:
        async def batch(self, identity: Identity, task: UUID, position: int) -> Batch:
            raise HistoryExpired(task)

        async def wait(self, task: UUID, watermark: int, oldest: int, wait_seconds: float) -> None:
            return None

    async def check() -> None:
        return None

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body":
            sent.append(message["body"])

    async def receive() -> Message:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    stream = Stream(
        task,
        Identity(uuid4(), uuid4()),
        Batch(0, 2, 1, (Event(1, b"first"),)),
        AuthLease(check),
        Purged(),
        lambda: None,
    )
    await stream({}, receive, send)
    assert b"event: stream.reset\n" in sent[1]
    assert b"id:" not in sent[1]
    assert stream.position == 1


@pytest.mark.parametrize("stalled", [False, True])
async def test_lifetime_bounds_a_stalled_source_and_does_not_release_before_final_send(
    stalled: bool,
) -> None:
    task = uuid4()
    released: list[bool] = []
    final_send = asyncio.Event()

    class Stalled:
        async def batch(self, identity: Identity, task: UUID, position: int) -> Batch:
            if not stalled:
                raise HistoryExpired(task)
            await asyncio.Event().wait()
            return Batch(0, 0, 1, ())

        async def wait(self, task: UUID, watermark: int, oldest: int, wait_seconds: float) -> None:
            if stalled:
                await asyncio.Event().wait()

    async def check() -> None:
        return None

    async def send(message: Message) -> None:
        if message["type"] == "http.response.body" and not message.get("more_body", False):
            final_send.set()
            await asyncio.sleep(0.02)
            assert not released, "connection ticket released before final transport write"

    async def receive() -> Message:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    stream = Stream(
        task,
        Identity(uuid4(), uuid4()),
        Batch(0, 0, 1, ()),
        AuthLease(check),
        Stalled(),
        lambda: released.append(True),
        lifetime=0.03,
    )
    running = asyncio.create_task(stream({}, receive, send))
    try:
        await asyncio.sleep(0.12)
        assert running.done(), "absolute connection lifetime must cancel a stalled source"
        await running
        assert final_send.is_set() is (not stalled)
        assert released == [True]
    finally:
        running.cancel()
        await asyncio.gather(running, return_exceptions=True)


@pytest.mark.parametrize("write_timeout", [0.01, 0.2])
async def test_normal_lifetime_finishes_asgi_body_within_hard_bound(write_timeout: float) -> None:
    sent: list[Message] = []
    released: list[bool] = []

    async def check() -> None:
        return None

    async def send(message: Message) -> None:
        assert not released
        sent.append(message)

    async def receive() -> Message:
        await asyncio.Event().wait()
        return {"type": "http.disconnect"}

    stream = Stream(
        uuid4(),
        Identity(uuid4(), uuid4()),
        Batch(0, 1, 1, (Event(1, b"id: 1\n\n"),)),
        AuthLease(check),
        None,
        lambda: released.append(True),
        lifetime=0.08,
        write_timeout=write_timeout,
    )
    async with asyncio.timeout(0.3):
        await stream({}, receive, send)
    assert sent[-1] == {"type": "http.response.body", "body": b"", "more_body": False}
    assert stream.position == 1
    assert released == [True]
