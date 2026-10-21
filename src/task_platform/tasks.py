"""Short PostgreSQL transactions own commands, counters, state and retained responses."""

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from pydantic import ValidationError
from redis.asyncio import Redis
from sqlalchemy import func, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from task_platform.command_schemas import CancelTask, CommandError, CreateTask, invalid
from task_platform.config import Settings
from task_platform.execution_state import abandon_calls
from task_platform.identity import Identity, NotFoundError, authenticate
from task_platform.models import PlatformControl, Principal
from task_platform.rate_limits import creation_limit
from task_platform.task_models import (
    Attempt,
    IdempotencyRecord,
    Step,
    Task,
    TaskEvent,
    WorkflowVersion,
)

Factory = async_sessionmaker[AsyncSession]
TERMINAL = frozenset({"succeeded", "failed", "cancelled", "timed_out"})
PINNED_VERSIONS: dict[str, object] = {
    "workflow": "1",
    "prompt": "1",
    "tools": "1",
    "context_builder": "1",
}


@dataclass(frozen=True)
class CommandResponse:
    status: int
    body: str


def response_for(task: Task, status: int = 200) -> CommandResponse:
    body = json.dumps(
        {"task_id": str(task.id), "state": task.state, "task_version": task.task_version},
        separators=(",", ":"),
    )
    return CommandResponse(status, body)


def validate_key(key: str | None) -> str:
    if key is None or re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", key) is None:
        raise invalid()
    return key


async def retry_transaction[T](operation: Callable[[], Awaitable[T]]) -> T:
    for attempt in range(3):
        try:
            return await operation()
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) not in {"40001", "40P01"} or attempt == 2:
                raise
            await asyncio.sleep(0.01 * (attempt + 1))
    raise RuntimeError("Unreachable retry bound")


async def retained(
    session: AsyncSession, owner: UUID, route: str, key: str, fingerprint: str
) -> CommandResponse | None:
    record = await session.get(IdempotencyRecord, (owner, route, key))
    if record is None:
        return None
    if record.canonical_version != "v1":
        raise CommandError(503, "Command version unavailable")
    if record.fingerprint != fingerprint:
        raise CommandError(409, "Idempotency conflict")
    return CommandResponse(record.response_status, record.response_body)


async def replay(
    factory: Factory, token: str, route: str, key: str, fingerprint: str
) -> tuple[Identity, CommandResponse | None]:
    async with factory() as session:
        identity = await authenticate(session, token)
        return identity, await retained(session, identity.principal_id, route, key, fingerprint)


async def locked_prefix(
    session: AsyncSession, identity: Identity, token: str
) -> tuple[PlatformControl, Principal]:
    platform = await session.scalar(
        select(PlatformControl).where(PlatformControl.id == 1).with_for_update()
    )
    if platform is None:
        raise CommandError(503, "Service unavailable")
    owner = await session.scalar(
        select(Principal).where(Principal.id == identity.principal_id).with_for_update()
    )
    if owner is None:
        raise NotFoundError
    # Every key mutation in the administrative service locks this principal first.
    # Holding it prevents a concurrent revocation/disable before this revalidation.
    await authenticate(session, token)
    return platform, owner


def retain(
    session: AsyncSession,
    identity: Identity,
    route: str,
    key: str,
    fingerprint: str,
    task: Task,
    response: CommandResponse,
) -> None:
    session.add(
        IdempotencyRecord(
            principal_id=identity.principal_id,
            route=route,
            key=key,
            canonical_version="v1",
            fingerprint=fingerprint,
            task_id=task.id,
            response_status=response.status,
            response_body=response.body,
        )
    )


async def commit_command(
    operation: Callable[[], Awaitable[CommandResponse]],
    factory: Factory,
    token: str,
    route: str,
    key: str,
    fingerprint: str,
) -> CommandResponse:
    try:
        return await retry_transaction(operation)
    except DBAPIError as exc:
        # A disconnected/lost commit acknowledgement is not permission to repeat a mutation.
        # Read the stable command identity through a fresh session, never blindly execute again.
        if getattr(exc.orig, "sqlstate", None) is None:
            _, result = await replay(factory, token, route, key, fingerprint)
            if result is not None:
                return result
        raise


async def submit_task(
    factory: Factory,
    redis: Redis,
    settings: Settings,
    token: str,
    key: str,
    value: object,
    fingerprint: str,
    epoch: str | None,
) -> CommandResponse:
    route = "/v1/tasks"
    identity, existing = await replay(factory, token, route, key, fingerprint)
    if existing is not None:
        return existing
    # No database transaction or connection spans this ephemeral network operation.
    await creation_limit(redis, str(identity.key_id))

    async def operation() -> CommandResponse:
        async with factory.begin() as session:
            platform, owner = await locked_prefix(session, identity, token)
            existing = await retained(session, owner.id, route, key, fingerprint)
            if existing is not None:
                return existing
            if epoch is None:
                raise CommandError(428, "Execution epoch required")
            if (
                epoch != str(platform.execution_epoch)
                or platform.execution_epoch != settings.execution_epoch
            ):
                raise CommandError(409, "Execution epoch mismatch")
            if not platform.admission_open:
                raise CommandError(503, "Submission unavailable")
            try:
                command = CreateTask.model_validate(value)
            except ValidationError:
                raise invalid() from None
            workflow = await session.get(
                WorkflowVersion, (command.workflow.name, command.workflow.version)
            )
            if workflow is None or not workflow.available:
                raise NotFoundError
            if (
                workflow.name != "analysis"
                or workflow.version != "1"
                or workflow.definition != PINNED_VERSIONS
            ):
                raise CommandError(503, "Workflow version unavailable")
            if (
                owner.open_tasks >= owner.max_open_tasks
                or platform.open_tasks >= platform.max_open_tasks
            ):
                raise CommandError(429, "Open task limit exceeded")
            # Charge canonical input/effective limits plus a bounded control/event/response reserve.
            stored = command.model_dump(mode="json")
            charge = (
                len(json.dumps(stored, ensure_ascii=True, separators=(",", ":")).encode()) + 8192
            )
            if (
                owner.storage_bytes + charge > owner.max_storage_bytes
                or platform.storage_bytes + charge > platform.max_storage_bytes
            ):
                raise CommandError(429, "Storage limit exceeded")
            now: datetime = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            task = Task(
                id=uuid4(),
                principal_id=owner.id,
                key_id=identity.key_id,
                workflow_name=workflow.name,
                workflow_version=workflow.version,
                versions=workflow.definition,
                input=command.input.model_dump(mode="json"),
                limits=command.limits.model_dump(mode="json"),
                execution_epoch=platform.execution_epoch,
                state="queued",
                reason=None,
                task_version=1,
                event_sequence=1,
                oldest_retained_sequence=1,
                created_at=now,
                deadline_at=now + timedelta(seconds=command.limits.deadline_seconds),
                result=None,
                storage_bytes=charge,
            )
            session.add(task)
            await session.flush()
            session.add(
                Step(
                    id=uuid4(),
                    task_id=task.id,
                    principal_id=owner.id,
                    path="analysis/turn-1/model",
                    state="ready",
                    generation=0,
                )
            )
            session.add(
                TaskEvent(
                    task_id=task.id,
                    principal_id=owner.id,
                    sequence=1,
                    kind="task.created",
                    data={"task_version": 1, "state": "queued"},
                )
            )
            owner.open_tasks += 1
            platform.open_tasks += 1
            owner.storage_bytes += charge
            platform.storage_bytes += charge
            response = response_for(task, 202)
            retain(session, identity, route, key, fingerprint, task, response)
        return response

    return await commit_command(operation, factory, token, route, key, fingerprint)


async def owned_task(
    session: AsyncSession, identity: Identity, task_id: UUID, *, lock: bool = False
) -> Task:
    query = select(Task).where(Task.id == task_id, Task.principal_id == identity.principal_id)
    task = await session.scalar(query.with_for_update() if lock else query)
    if task is None:
        raise NotFoundError
    return task


async def cancel_task(
    factory: Factory, token: str, task_id: UUID, key: str, value: object, fingerprint: str
) -> CommandResponse:
    route = f"/v1/tasks/{task_id}/cancel"
    identity, existing = await replay(factory, token, route, key, fingerprint)
    if existing is not None:
        return existing
    # Hide foreign task existence even when the supplied body is invalid.
    async with factory() as session:
        await owned_task(session, identity, task_id)
    try:
        command = CancelTask.model_validate(value)
    except ValidationError:
        raise invalid() from None

    async def operation() -> CommandResponse:
        async with factory.begin() as session:
            platform, owner = await locked_prefix(session, identity, token)
            existing = await retained(session, owner.id, route, key, fingerprint)
            if existing is not None:
                return existing
            task = await owned_task(session, identity, task_id, lock=True)
            if task.task_version != command.expected_task_version:
                raise CommandError(409, "Task version conflict")
            if task.state in TERMINAL:
                raise CommandError(409, "Task is terminal")
            children = (
                await session.scalars(
                    select(Step).where(Step.task_id == task.id).order_by(Step.id).with_for_update()
                )
            ).all()
            attempts = (
                await session.scalars(
                    select(Attempt)
                    .where(Attempt.task_id == task.id)
                    .order_by(Attempt.id)
                    .with_for_update()
                )
            ).all()
            await abandon_calls(session, task)
            # Lock waits can cross the deadline; sample database time only after all locks.
            now: datetime = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            for attempt in attempts:
                if attempt.state == "active":
                    attempt.state = "revoked"
                    attempt.ended_at = now
                    attempt.reason = "cancelled"
            for step in children:
                if step.state not in {"succeeded", "failed", "cancelled"}:
                    step.state = "cancelled"
                    step.generation += 1
                    step.holder = None
                    step.lease_until = None
            task.state = "timed_out" if now >= task.deadline_at else "cancelled"
            task.task_version += 1
            task.event_sequence += 1
            owner.open_tasks -= 1
            platform.open_tasks -= 1
            session.add(
                TaskEvent(
                    task_id=task.id,
                    principal_id=owner.id,
                    sequence=task.event_sequence,
                    kind=f"task.{task.state}",
                    data={"state": task.state, "task_version": task.task_version},
                )
            )
            response = response_for(task)
            retain(session, identity, route, key, fingerprint, task, response)
        return response

    return await commit_command(operation, factory, token, route, key, fingerprint)


def snapshot(task: Task) -> dict[str, object]:
    return {
        "task_id": str(task.id),
        "state": task.state,
        "reason": task.reason,
        "task_version": task.task_version,
        "event_sequence": task.event_sequence,
        "oldest_retained_sequence": task.oldest_retained_sequence,
        "versions": task.versions,
        "limits": task.limits,
        "created_at": task.created_at.isoformat(),
        "deadline_at": task.deadline_at.isoformat(),
        "result": task.result,
    }
