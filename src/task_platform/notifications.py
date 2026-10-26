"""Bounded, lossy post-commit hints; PostgreSQL polling always remains authoritative."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import event
from sqlalchemy.orm import Session, SessionTransaction

from task_platform.config import Settings
from task_platform.task_models import TaskEvent
from task_platform.tasks import Factory


class Notifications:
    def __init__(self, redis: Redis) -> None:
        self.redis = redis
        self.queue: asyncio.Queue[UUID] = asyncio.Queue(maxsize=100)
        self.dropped = 0

    def attach(self, factory: Factory) -> None:
        info = dict(factory.kw.get("info", {}))
        info["notifications"] = self
        factory.configure(info=info)

    def hint(self, task: UUID) -> None:
        try:
            self.queue.put_nowait(task)
        except asyncio.QueueFull:
            self.dropped += 1

    async def run(self) -> None:
        while True:
            task = await self.queue.get()
            try:
                async with asyncio.timeout(1):
                    await self.redis.publish("atp.events", str(task))
            except RedisError, OSError, TimeoutError:
                self.dropped += 1
            finally:
                self.queue.task_done()


@event.listens_for(Session, "after_flush")
def capture(session: Session, context: object) -> None:
    if isinstance(session.info.get("notifications"), Notifications):
        transaction = session.get_nested_transaction() or session.get_transaction()
        if transaction is None:
            return
        frames: dict[SessionTransaction, set[UUID]] = session.info.setdefault("event_wakes", {})
        pending = frames.setdefault(transaction, set())
        pending.update(item.task_id for item in session.new if isinstance(item, TaskEvent))


@event.listens_for(Session, "after_commit")
def committed(session: Session) -> None:
    notifier = session.info.get("notifications")
    transaction = session.get_nested_transaction() or session.get_transaction()
    frames: dict[SessionTransaction, set[UUID]] = session.info.get("event_wakes", {})
    pending = frames.pop(transaction, set()) if transaction is not None else set()
    if transaction is not None and transaction.nested:
        parent = transaction.parent
        if parent is not None:
            frames.setdefault(parent, set()).update(pending)
        return
    session.info.pop("event_wakes", None)
    if isinstance(notifier, Notifications):
        for task in pending:
            notifier.hint(task)


@event.listens_for(Session, "after_rollback")
def rolled_back(session: Session) -> None:
    transaction = session.get_nested_transaction() or session.get_transaction()
    frames: dict[SessionTransaction, set[UUID]] = session.info.get("event_wakes", {})
    if transaction is not None:
        frames.pop(transaction, None)


@event.listens_for(Session, "after_transaction_end")
def transaction_ended(session: Session, transaction: SessionTransaction) -> None:
    if transaction.parent is None:
        session.info.pop("event_wakes", None)


@asynccontextmanager
async def publishing(settings: Settings, *factories: Factory) -> AsyncIterator[None]:
    if settings.redis_url is None:
        yield
        return
    redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=1,
        socket_timeout=1,
        max_connections=1,
    )
    notifier = Notifications(redis)
    for factory in factories:
        notifier.attach(factory)
    publisher = asyncio.create_task(notifier.run())
    try:
        yield
    finally:
        publisher.cancel()
        await asyncio.gather(publisher, return_exceptions=True)
        await redis.aclose()
