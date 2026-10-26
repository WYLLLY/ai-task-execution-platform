"""Deterministic race schedule for the ephemeral shared cache, not SQL guarantees."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker

from task_platform import event_hub
from task_platform.event_hub import Followed, Hub
from task_platform.event_reads import Batch
from task_platform.identity import Identity, NotFoundError
from task_platform.tasks import Factory


async def test_poll_invalidation_during_read_does_not_republish_stale_cache(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = Identity(uuid4(), uuid4())
    task = uuid4()
    hub = Hub(async_sessionmaker())
    state = Followed(identity, 0, 1)
    hub.tasks[task] = state
    entered, resume = asyncio.Event(), asyncio.Event()
    reads = 0

    async def read(factory: Factory, owner: Identity, task_id: object, **kwargs: object) -> Batch:
        nonlocal reads
        reads += 1
        if reads == 1:
            entered.set()
            await resume.wait()
            return Batch(0, 0, 1, ())
        return Batch(0, 1, 1, ())

    monkeypatch.setattr(event_hub, "read_events", read)
    pending = asyncio.create_task(hub.batch(identity, task, 0))
    await entered.wait()
    async with state.changed:
        state.watermark = 1
        state.cache.clear()
        state.changed.notify_all()
    resume.set()
    await pending
    fresh = await hub.batch(identity, task, 0)
    assert fresh.watermark == 1
    assert reads == 2


async def test_read_ahead_of_poller_does_not_create_busy_wait() -> None:
    identity = Identity(uuid4(), uuid4())
    task = uuid4()
    hub = Hub(async_sessionmaker())
    hub.tasks[task] = Followed(identity, 1, 1)
    waiting = asyncio.create_task(hub.wait(task, 2, 1, 0.1))
    await asyncio.sleep(0)
    assert not waiting.done(), "older metadata is not a new event"
    await waiting


async def test_deletion_during_read_cannot_resurrect_cached_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    identity = Identity(uuid4(), uuid4())
    task = uuid4()
    hub = Hub(async_sessionmaker())
    state = Followed(identity, 0, 1)
    hub.tasks[task] = state
    entered, resume = asyncio.Event(), asyncio.Event()
    reads = 0

    async def read(factory: Factory, owner: Identity, task_id: object, **kwargs: object) -> Batch:
        nonlocal reads
        reads += 1
        if reads == 1:
            entered.set()
            await resume.wait()
            return Batch(0, 0, 1, ())
        raise NotFoundError

    monkeypatch.setattr(event_hub, "read_events", read)
    pending = asyncio.create_task(hub.batch(identity, task, 0))
    await entered.wait()
    state.watermark, state.oldest = -1, -1
    state.cache.clear()
    resume.set()
    await pending
    with pytest.raises(NotFoundError):
        await hub.batch(identity, task, 0)
    assert reads == 2
