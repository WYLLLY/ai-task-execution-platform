"""Redis is a lossy hint; only committed PostgreSQL progress is authoritative."""

import asyncio
from uuid import UUID

import pytest
from httpx import AsyncClient
from redis.asyncio import Redis
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.event_hub import Hub
from task_platform.event_reads import read_events
from task_platform.execution import event
from task_platform.identity import Identity
from task_platform.notifications import Notifications
from task_platform.task_models import Task

from .conftest import settings_for
from .test_commands import create

pytestmark = pytest.mark.integration


async def test_only_committed_progress_publishes_bounded_ids(
    client: AsyncClient, command_owner: dict[str, str]
) -> None:
    item = await create(client, command_owner)
    settings = settings_for("api")
    assert settings.redis_url is not None
    redis = Redis.from_url(settings.redis_url.get_secret_value(), socket_timeout=1)
    engine = create_engine(settings_for("worker"))
    factory = sessions(engine)
    notifier = Notifications(redis)
    notifier.attach(factory)
    publisher = asyncio.create_task(notifier.run())
    try:
        async with redis.pubsub() as subscriber:
            await subscriber.subscribe("atp.events")
            await subscriber.get_message(timeout=1)
            async with factory.begin() as session:
                task = await session.get(Task, UUID(str(item["task_id"])))
                assert task
                event(session, task, "usage.updated")
            message = await subscriber.get_message(ignore_subscribe_messages=True, timeout=1)
            assert message is not None, "committed progress must publish a wake hint"
            assert message["data"] == str(item["task_id"]).encode()
            async with factory() as session:
                with pytest.raises(ValueError):
                    async with session.begin():
                        task = await session.get(Task, UUID(str(item["task_id"])))
                        assert task
                        event(session, task, "usage.updated")
                        await session.flush()
                        raise ValueError("rollback")
                await session.commit()
            assert await subscriber.get_message(ignore_subscribe_messages=True, timeout=0.2) is None
    finally:
        publisher.cancel()
        await asyncio.gather(publisher, return_exceptions=True)
        await redis.aclose()
        await engine.dispose()


async def test_real_redis_duplicate_hints_wake_without_becoming_events(
    client: AsyncClient, command_owner: dict[str, str], admin_engine: AsyncEngine
) -> None:
    item = await create(client, command_owner)
    task = UUID(str(item["task_id"]))
    async with admin_engine.connect() as c:
        owner, key = (await c.execute(text("SELECT principal_id,key_id FROM tasks"))).one()
    settings = settings_for("api")
    assert settings.redis_url is not None
    redis = Redis.from_url(settings.redis_url.get_secret_value(), socket_timeout=1)
    engine = create_engine(settings)
    hub = Hub(sessions(engine))
    initial = await read_events(sessions(engine), Identity(owner, key), task)
    release = hub.subscribe(task, Identity(owner, key), initial)
    listener = asyncio.create_task(hub.listen(redis))
    try:
        async with asyncio.timeout(1):
            for _ in range(50):
                counts = await redis.pubsub_numsub("atp.events")
                if counts[0][1]:
                    break
                await asyncio.sleep(0.01)
        assert counts[0][1] > 0, "SSE notification listener must subscribe"
        for _ in range(10):
            await redis.publish("atp.events", str(task))
        await asyncio.sleep(0.2)
        unchanged = await hub.batch(Identity(owner, key), task, 1)
        assert unchanged.watermark == 1 and not unchanged.events
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT count(*) FROM task_events")) == 1
    finally:
        listener.cancel()
        await asyncio.gather(listener, return_exceptions=True)
        release()
        await hub.close()
        await redis.aclose()
        await engine.dispose()


async def test_savepoint_commit_cannot_publish_before_outer_commit(
    client: AsyncClient, command_owner: dict[str, str]
) -> None:
    item = await create(client, command_owner)
    settings = settings_for("api")
    assert settings.redis_url is not None
    redis = Redis.from_url(settings.redis_url.get_secret_value())
    engine = create_engine(settings_for("worker"))
    factory = sessions(engine)
    notifier = Notifications(redis)
    notifier.attach(factory)
    try:
        async with factory() as session:
            await session.begin()
            task = await session.get(Task, UUID(str(item["task_id"])))
            assert task
            event(session, task, "usage.updated")
            await session.flush()
            nested = await session.begin_nested()
            await nested.commit()
            assert notifier.queue.empty(), "savepoint is not a durable commit"
            await session.rollback()
            assert notifier.queue.empty()
        # Nested rollback must retain earlier outer progress.
        async with factory.begin() as session:
            task = await session.get(Task, UUID(str(item["task_id"])))
            assert task
            event(session, task, "usage.updated")
            await session.flush()
            nested = await session.begin_nested()
            event(session, task, "usage.updated")
            await session.flush()
            await nested.rollback()
        assert notifier.queue.qsize() == 1
    finally:
        await engine.dispose()
        await redis.aclose()
