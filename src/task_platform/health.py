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
from task_platform.corpus import Document
from task_platform.execution_models import CallCost, CostEvidence, ExternalCall, ProviderAdmission
from task_platform.models import APIKey, PlatformControl, Principal
from task_platform.task_models import (
    Attempt,
    IdempotencyRecord,
    Step,
    Task,
    TaskEvent,
    WorkflowVersion,
)

SCHEMA_REVISION = "0005_provider"


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
                PlatformControl,
                Principal,
                APIKey,
                Task,
                Step,
                Attempt,
                TaskEvent,
                IdempotencyRecord,
                WorkflowVersion,
                ExternalCall,
                CallCost,
                CostEvidence,
                ProviderAdmission,
                Document,
            ):
                await connection.execute(select(model).limit(0))
            await connection.execute(
                text("SELECT call_id,kind,revision FROM accounting_decisions LIMIT 0")
            )
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
            if settings.role in {"api", "worker"}:
                inserts = (
                    ("tasks", "steps", "task_events", "idempotency_records")
                    if settings.role == "api"
                    else (
                        "attempts",
                        "steps",
                        "task_events",
                        "external_calls",
                        "call_costs",
                        "cost_evidence",
                    )
                )
                updates: tuple[tuple[str, tuple[str, ...]], ...] = (
                    ("tasks", ("state", "reason", "task_version", "event_sequence")),
                    ("steps", ("state", "generation", "holder", "lease_until")),
                    ("attempts", ("state",)),
                    ("principals", ("open_tasks", "storage_bytes")),
                    ("platform_control", ("open_tasks", "storage_bytes")),
                    ("external_calls", ("state", "local_active")),
                    ("attempts", ("ended_at", "reason")),
                    ("steps", ("available_at", "selected_call_id")),
                )
                if settings.role == "worker":
                    updates += (
                        ("external_calls", ("response_observed_at", "provider_observation")),
                        ("tasks", ("result", "storage_bytes", "tool_calls")),
                        (
                            "steps",
                            (
                                "result",
                                "failures",
                                "payload",
                                "operation_key",
                                "first_authorized_at",
                                "probe_count",
                                "protocol_items",
                            ),
                        ),
                        (
                            "call_costs",
                            (
                                "confirmed_cost",
                                "confirmed_tokens",
                                "exposure_cost",
                                "exposure_tokens",
                                "revision",
                                "overrun",
                            ),
                        ),
                        ("provider_admission", ("next_allowed_at",)),
                    )
                # Audit every exact grant in two round trips. A per-column query loop
                # unnecessarily keeps relation locks and the control connection busy.
                writable = bool(
                    await connection.scalar(
                        text(
                            "SELECT bool_and(has_table_privilege(current_user, t, 'INSERT')) "
                            "FROM unnest(CAST(:tables AS text[])) AS t"
                        ),
                        {"tables": list(inserts)},
                    )
                )
                columns_to_check = [(table, column) for table, cols in updates for column in cols]
                writable = (
                    bool(
                        await connection.scalar(
                            text(
                                "SELECT bool_and(has_column_privilege("
                                "current_user, t, c, 'UPDATE')) "
                                "FROM unnest(CAST(:tables AS text[]), CAST(:columns AS text[])) "
                                "AS required(t, c)"
                            ),
                            {
                                "tables": [table for table, column in columns_to_check],
                                "columns": [column for table, column in columns_to_check],
                            },
                        )
                    )
                    and writable
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
