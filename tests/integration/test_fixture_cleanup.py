"""Disposable fixture cleanup survives a confirmed transient DDL lock failure."""

import asyncio
from collections.abc import AsyncIterator, Callable
from inspect import unwrap
from typing import cast

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.engine import Engine, ExceptionContext
from sqlalchemy.exc import OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine

from .conftest import command_owner, settings_for
from .test_commands import create

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("release", [True, False])
async def test_fixture_cleanup_retries_confirmed_lock_timeout(
    client: AsyncClient, admin_engine: AsyncEngine, release: bool
) -> None:
    fixture = cast(Callable[[AsyncEngine], AsyncIterator[dict[str, str]]], unwrap(command_owner))
    owner = fixture(admin_engine)
    headers = await anext(owner)
    task_id = (await create(client, headers))["task_id"]
    engine = create_engine(settings_for("migrator"))
    observer_engine = create_engine(settings_for("migrator"))
    timed_out = asyncio.Event()
    failures = 0

    async def finish() -> dict[str, str]:
        return await anext(owner)

    def observed_failure(context: ExceptionContext) -> None:
        nonlocal failures
        if getattr(context.original_exception, "sqlstate", None) == "55P03":
            failures += 1
            timed_out.set()

    event.listen(Engine, "handle_error", observed_failure)
    try:
        async with engine.connect() as blocker, observer_engine.connect() as observer:
            transaction = await blocker.begin()
            await blocker.execute(text("LOCK TABLE tasks IN ACCESS SHARE MODE"))
            # Prime the statistics snapshot before the cleanup backend starts waiting.
            await observer.execute(text("SELECT count(*) FROM pg_stat_activity"))
            cleanup = asyncio.create_task(finish())
            try:
                # Observe actual PostgreSQL lock contention, not merely task scheduling.
                async with asyncio.timeout(3):
                    for _ in range(300):
                        blocked = await observer.scalar(
                            text(
                                "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                                "WHERE cardinality(pg_blocking_pids(pid)) > 0 "
                                "AND query LIKE 'TRUNCATE accounting_decisions%')"
                            )
                        )
                        # pg_stat_activity is cached for the transaction, even at READ COMMITTED.
                        # Start a new observation transaction before the next sample.
                        await observer.commit()
                        if blocked:
                            break
                        await asyncio.sleep(0.01)
                    else:
                        raise AssertionError("Expected PostgreSQL lock contention")
                await asyncio.wait_for(timed_out.wait(), 3)
                if release:
                    await transaction.rollback()
                    with pytest.raises(StopAsyncIteration):
                        await asyncio.wait_for(cleanup, 5)
                    assert failures == 1
                else:
                    with pytest.raises(OperationalError) as error:
                        await asyncio.wait_for(cleanup, 5)
                    assert getattr(error.value.orig, "sqlstate", None) == "55P03"
                    assert failures == 3
            finally:
                if transaction.is_active:
                    await transaction.rollback()
                if not cleanup.done():
                    cleanup.cancel()
                await asyncio.gather(cleanup, return_exceptions=True)
        async with admin_engine.connect() as connection:
            assert await connection.scalar(
                text("SELECT count(*) FROM tasks WHERE id=:id"), {"id": task_id}
            ) == (0 if release else 1)
    finally:
        event.remove(Engine, "handle_error", observed_failure)
        await engine.dispose()
        await observer_engine.dispose()
        # Restore the disposable stack even during the pre-fix RED run.
        restore = fixture(admin_engine)
        await anext(restore)
        with pytest.raises(StopAsyncIteration):
            await anext(restore)
