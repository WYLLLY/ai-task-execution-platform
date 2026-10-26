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
from task_platform.models import PlatformControl

SCHEMA_REVISION = "0001_foundation"


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
            compatible = revisions == [SCHEMA_REVISION]
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
    return Health(
        ready=postgres and schema and matches,
        postgres=postgres,
        redis=redis_up,
        schema_compatible=schema,
        epoch_matches=matches,
        execution_epoch=epoch,
        # M1 provides identity only. Task submission and execution do not yet exist.
        submission_available=False,
        dispatch_available=False,
    )
