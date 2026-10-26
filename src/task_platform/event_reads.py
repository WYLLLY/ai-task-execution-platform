"""Single-statement projections: metadata, costs and events share one MVCC snapshot."""

import json
import re
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from task_platform.command_schemas import CommandError
from task_platform.event_cursor import MAX_SEQUENCE, event_id, resume_after
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
    costs = text("""(SELECT jsonb_build_object(
        'confirmed_micro_usd', coalesce(sum(c.confirmed_cost),0),
        'confirmed_tokens', coalesce(sum(c.confirmed_tokens),0),
        'uncertain_micro_usd', coalesce(sum(c.exposure_cost),0),
        'uncertain_tokens', coalesce(sum(c.exposure_tokens),0),
        'overrun_calls', count(*) FILTER (WHERE c.overrun))
        FROM call_costs c JOIN external_calls x ON x.id=c.call_id
        WHERE x.task_id=tasks.id AND x.principal_id=tasks.principal_id) AS costs""")
    row = (
        await session.execute(
            select(Task, costs).where(
                Task.id == task_id, Task.principal_id == identity.principal_id
            )
        )
    ).one_or_none()
    if row is None:
        raise NotFoundError
    result = snapshot(row[0])
    result["costs"] = row[1]
    return result
