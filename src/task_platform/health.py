"""Dependency health separates PostgreSQL readiness from Redis degradation."""

import asyncio
from uuid import UUID

from pydantic import BaseModel
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.config import Settings
from task_platform.models import APIKey, PlatformControl, Principal
from task_platform.task_models import (
    Attempt,
    IdempotencyRecord,
    Step,
    Task,
    TaskEvent,
    WorkflowVersion,
)

SCHEMA_REVISION = "0002_commands"


class Health(BaseModel):
    ready: bool
    postgres: bool
    redis: bool
    schema_compatible: bool
    epoch_matches: bool
    execution_epoch: UUID | None
    submission_available: bool
    dispatch_available: bool


async def check_database(engine: AsyncEngine, settings: Settings) -> tuple[bool, bool, UUID | None]:
    try:
        async with asyncio.timeout(4), engine.connect() as connection:
            # Validate usable required columns/privileges, not only the revision marker.
            for model in (
                Principal,
                APIKey,
                Task,
                Step,
                Attempt,
                TaskEvent,
                IdempotencyRecord,
                WorkflowVersion,
            ):
                await connection.execute(select(model).limit(0))
            role = await connection.scalar(text("SELECT current_user"))
            capacity = int(str(await connection.scalar(text("SHOW max_connections"))))
            revisions = (
                (await connection.execute(text("SELECT version_num FROM alembic_version")))
                .scalars()
                .all()
            )
            epoch = await connection.scalar(
                select(PlatformControl.execution_epoch).where(PlatformControl.id == 1)
            )
            writable = True
            if settings.role == "api":
                for table in ("tasks", "steps", "task_events", "idempotency_records"):
                    writable = writable and bool(
                        await connection.scalar(
                            text("SELECT has_table_privilege(current_user, :table, 'INSERT')"),
                            {"table": table},
                        )
                    )
                for table, columns in (
                    ("tasks", ("state", "reason", "task_version", "event_sequence")),
                    ("steps", ("state", "generation", "holder", "lease_until")),
                    ("attempts", ("state",)),
                    ("principals", ("open_tasks", "storage_bytes")),
                    ("platform_control", ("open_tasks", "storage_bytes")),
                ):
                    for column in columns:
                        writable = writable and bool(
                            await connection.scalar(
                                text(
                                    "SELECT has_column_privilege("
                                    "current_user, :table, :column, 'UPDATE')"
                                ),
                                {"table": table, "column": column},
                            )
                        )
            compatible = revisions == [SCHEMA_REVISION] and writable
            compatible = compatible and role == f"atp_{settings.role}"
            compatible = compatible and capacity >= settings.db_max_connections
            return True, compatible, epoch
    except SQLAlchemyError, OSError, TimeoutError:
        return False, False, None


async def check_redis(redis: Redis) -> bool:
    try:
        async with asyncio.timeout(2):
            return bool(await redis.ping())
    except RedisError, OSError, TimeoutError:
        return False


async def health(engine: AsyncEngine, redis: Redis, settings: Settings) -> Health:
    (postgres, schema, epoch), redis_up = await asyncio.gather(
        check_database(engine, settings), check_redis(redis)
    )
    matches = epoch == settings.execution_epoch
    admission_open = False
    if postgres and schema and matches:
        try:
            async with asyncio.timeout(4), engine.connect() as connection:
                admission_open = bool(
                    await connection.scalar(
                        select(PlatformControl.admission_open).where(PlatformControl.id == 1)
                    )
                )
        except SQLAlchemyError, OSError, TimeoutError:
            postgres = False
    return Health(
        ready=postgres and schema and matches,
        postgres=postgres,
        redis=redis_up,
        schema_compatible=schema,
        epoch_matches=matches,
        execution_epoch=epoch,
        submission_available=postgres and schema and matches and redis_up and admission_open,
        dispatch_available=False,
    )
