"""Single-statement projections: metadata, costs and events share one MVCC snapshot."""

import json
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from task_platform.command_schemas import CommandError
from task_platform.event_cursor import MAX_SEQUENCE, HistoryExpired, event_id, resume_after
from task_platform.execution_models import CallCost, ExternalCall
from task_platform.identity import Identity, NotFoundError
from task_platform.task_models import Task
from task_platform.tasks import Factory, snapshot


class EventInvariantError(Exception):
    pass


@dataclass(frozen=True)
class Event:
    sequence: int
    wire: bytes


@dataclass(frozen=True)
class Batch:
    position: int
    watermark: int
    oldest: int
    events: tuple[Event, ...]


def cost_fields() -> ColumnElement[dict[str, int]]:
    return func.jsonb_build_object(
        "confirmed_micro_usd",
        func.coalesce(func.sum(CallCost.confirmed_cost).filter(~CallCost.estimated), 0),
        "confirmed_tokens",
        func.coalesce(func.sum(CallCost.confirmed_tokens).filter(~CallCost.estimated), 0),
        "estimated_micro_usd",
        func.coalesce(func.sum(CallCost.confirmed_cost).filter(CallCost.estimated), 0),
        "estimated_tokens",
        func.coalesce(func.sum(CallCost.confirmed_tokens).filter(CallCost.estimated), 0),
        "uncertain_micro_usd",
        func.coalesce(func.sum(CallCost.exposure_cost), 0),
        "uncertain_tokens",
        func.coalesce(func.sum(CallCost.exposure_tokens), 0),
        "overrun_calls",
        func.count().filter(CallCost.overrun),
    )


async def read_events(
    factory: Factory,
    identity: Identity,
    task_id: UUID,
    *,
    after: str | None = None,
    last_event_id: str | None = None,
) -> Batch:
    # Parse only a bounded hint. Report cursor errors after the owner-filtered SQL.
    try:
        hint = resume_after(task_id, MAX_SEQUENCE, 1, after=after, last_event_id=last_event_id)
    except CommandError:
        hint = 0
    async with factory() as session:
        row = (
            await session.execute(
                text("""
                SELECT t.event_sequence, t.oldest_retained_sequence,
                  coalesce((SELECT jsonb_agg(jsonb_build_object(
                    'sequence', e.sequence, 'kind', e.kind, 'data', e.data)
                    ORDER BY e.sequence) FROM (
                      SELECT *, sum(octet_length(data::text)+octet_length(kind)+100)
                        OVER (ORDER BY sequence) AS bytes
                      FROM (SELECT sequence,kind,data FROM task_events
                        WHERE task_id=t.id AND principal_id=t.principal_id
                          AND sequence>:position ORDER BY sequence LIMIT 100) limited
                    ) e WHERE e.bytes<=262144), '[]'::jsonb) AS events
                FROM tasks t WHERE t.id=:task AND t.principal_id=:owner
                """),
                {"task": task_id, "owner": identity.principal_id, "position": hint},
            )
        ).one_or_none()
        if row is None:
            await missing_task(session, identity, task_id)
    if row is None:
        raise NotFoundError
    position = resume_after(
        task_id,
        row.event_sequence,
        row.oldest_retained_sequence,
        after=after,
        last_event_id=last_event_id,
    )
    events: list[Event] = []
    expected = position + 1
    for value in row.events:
        if value["sequence"] != expected or expected > row.event_sequence:
            raise EventInvariantError
        kind, data = value["kind"], value["data"]
        if not re.fullmatch(r"(?:task|step|call|usage)\.[a-z_]{1,30}", kind):
            raise EventInvariantError
        # Progress never carries source bodies, protocol items or arbitrary extras.
        public = {key: data[key] for key in ("state", "task_version") if key in data}
        states = {
            "queued",
            "running",
            "suspended",
            "succeeded",
            "failed",
            "cancelled",
            "timed_out",
            "ready",
            "retry_wait",
            "unknown",
            "authorized",
            "rejected",
        }
        if "state" in public and (
            not isinstance(public["state"], str) or public["state"] not in states
        ):
            raise EventInvariantError
        if "task_version" in public and (
            type(public["task_version"]) is not int
            or not 1 <= public["task_version"] <= MAX_SEQUENCE
        ):
            raise EventInvariantError
        encoded = json.dumps(public, separators=(",", ":"), ensure_ascii=True)
        wire = f"id: {event_id(task_id, expected)}\nevent: {kind}\ndata: {encoded}\n\n".encode()
        if len(wire) > 16384:
            raise EventInvariantError
        events.append(Event(expected, wire))
        expected += 1
    if not events and position < row.event_sequence:
        raise EventInvariantError
    return Batch(position, row.event_sequence, row.oldest_retained_sequence, tuple(events))


async def task_snapshot(
    session: AsyncSession, identity: Identity, task_id: UUID
) -> dict[str, object]:
    costs = (
        select(cost_fields())
        .select_from(CallCost)
        .join(ExternalCall, ExternalCall.id == CallCost.call_id)
        .where(ExternalCall.task_id == Task.id, ExternalCall.principal_id == Task.principal_id)
        .correlate(Task)
        .scalar_subquery()
        .label("costs")
    )
    row = (
        await session.execute(
            select(Task, costs).where(
                Task.id == task_id, Task.principal_id == identity.principal_id
            )
        )
    ).one_or_none()
    if row is None:
        await missing_task(session, identity, task_id)
        raise NotFoundError
    result = snapshot(row[0])
    result["costs"] = row[1]
    return result


async def missing_task(session: AsyncSession, identity: Identity, task_id: UUID) -> None:
    if await session.scalar(
        text("SELECT 1 FROM task_tombstones WHERE task_id=:task AND principal_id=:owner"),
        {"task": task_id, "owner": identity.principal_id},
    ):
        raise HistoryExpired(task_id)
    raise NotFoundError


async def usage(session: AsyncSession, identity: Identity) -> dict[str, object]:
    costs = await session.scalar(
        select(cost_fields())
        .select_from(CallCost)
        .join(ExternalCall, ExternalCall.id == CallCost.call_id)
        .where(ExternalCall.principal_id == identity.principal_id)
    )
    return {"costs": costs}
