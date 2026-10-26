"""One bounded PostgreSQL poller per followed task in the single API process."""

import asyncio
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError

from task_platform.event_reads import Batch, read_events
from task_platform.identity import Identity
from task_platform.task_models import Task
from task_platform.tasks import Factory


@dataclass
class Followed:
    identity: Identity
    watermark: int
    oldest: int
    references: int = 0
    unavailable: bool = False
    changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    wake: asyncio.Event = field(default_factory=asyncio.Event)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    cache: OrderedDict[int, Batch] = field(default_factory=OrderedDict)
    poller: asyncio.Task[None] | None = None


class Hub:
    def __init__(self, factory: Factory) -> None:
        self.factory = factory
        self.tasks: dict[UUID, Followed] = {}
        self.stopping: set[asyncio.Task[None]] = set()

    @property
    def followed_tasks(self) -> int:
        return len(self.tasks)

    async def listen(self, redis: Redis) -> None:
        while True:
            try:
                async with redis.pubsub() as subscriber:
                    await subscriber.subscribe("atp.events")
                    while True:
                        message = await subscriber.get_message(
                            ignore_subscribe_messages=True, timeout=1
                        )
                        if message is None:
                            continue
                        value = message.get("data")
                        if not isinstance(value, bytes) or len(value) != 36:
                            continue
                        try:
                            task = UUID(value.decode("ascii"))
                        except ValueError, UnicodeError:
                            continue
                        state = self.tasks.get(task)
                        if state is not None:
                            state.wake.set()
            except RedisError, OSError, TimeoutError:
                # Lost hints never stop SQL polling or renew authentication.
                await asyncio.sleep(1)

    def subscribe(self, task: UUID, identity: Identity, batch: Batch) -> Callable[[], None]:
        state = self.tasks.get(task)
        if state is None:
            state = Followed(identity, batch.watermark, batch.oldest)
            state.cache[batch.position] = batch
            self.tasks[task] = state
            state.poller = asyncio.create_task(self._poll(task, state))
        elif state.identity.principal_id != identity.principal_id:
            raise ValueError("Invalid task owner")
        state.references += 1
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            state.references -= 1
            if state.references == 0:
                self.tasks.pop(task, None)
                if state.poller is not None:
                    state.poller.cancel()
                    self.stopping.add(state.poller)
                    state.poller.add_done_callback(self.stopping.discard)

        return release

    async def _poll(self, task: UUID, state: Followed) -> None:
        last_poll = 0.0
        while True:
            try:
                async with asyncio.timeout(1):
                    await state.wake.wait()
            except TimeoutError:
                pass
            state.wake.clear()
            await asyncio.sleep(max(0, last_poll + 0.1 - time.monotonic()))
            last_poll = time.monotonic()
            try:
                async with self.factory() as session:
                    row = (
                        await session.execute(
                            select(
                                Task.event_sequence,
                                Task.oldest_retained_sequence,
                            ).where(
                                Task.id == task, Task.principal_id == state.identity.principal_id
                            )
                        )
                    ).one_or_none()
                watermark, oldest = (row[0], row[1]) if row is not None else (-1, -1)
                async with state.changed:
                    if (watermark, oldest) != (state.watermark, state.oldest):
                        state.watermark, state.oldest = watermark, oldest
                        state.cache.clear()
                        state.changed.notify_all()
            except SQLAlchemyError, OSError, TimeoutError:
                async with state.changed:
                    state.unavailable = True
                    state.changed.notify_all()
                return

    async def batch(self, identity: Identity, task: UUID, position: int) -> Batch:
        state = self.tasks[task]
        if state.unavailable:
            raise OSError("Event store unavailable")
        async with state.lock:
            cached = state.cache.get(position)
            if cached is not None:
                return cached
            batch = await read_events(self.factory, identity, task, after=f"{task}:{position}")
            # A poll may invalidate this query's older MVCC snapshot while it
            # is in flight. Never publish that stale result back into the cache.
            if (
                state.watermark >= 0
                and batch.watermark >= state.watermark
                and batch.oldest >= state.oldest
            ):
                state.cache[position] = batch
                if len(state.cache) > 2:
                    state.cache.popitem(last=False)
            return batch

    async def wait(self, task: UUID, watermark: int, oldest: int, wait_seconds: float) -> None:
        state = self.tasks[task]
        try:
            async with asyncio.timeout(wait_seconds), state.changed:
                await state.changed.wait_for(
                    lambda: (
                        state.unavailable
                        or state.watermark < 0
                        or state.watermark > watermark
                        or state.oldest > oldest
                    )
                )
        except TimeoutError:
            pass
        if state.unavailable:
            raise OSError("Event store unavailable")

    async def close(self) -> None:
        pending = self.stopping | {s.poller for s in self.tasks.values() if s.poller is not None}
        self.tasks.clear()
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
