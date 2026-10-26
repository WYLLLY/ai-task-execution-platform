"""Bounded SSE resume positions; caller must authorize the target task first."""

import re
from uuid import UUID

from task_platform.command_schemas import CommandError

MAX_SEQUENCE = 2**63 - 1
CURSOR = re.compile(
    r"([0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}):([0-9]{1,19})"
)


class HistoryExpired(CommandError):
    def __init__(self, task_id: UUID) -> None:
        super().__init__(410, "Event history expired")
        self.snapshot_url = f"/v1/tasks/{task_id}"


def _valid_sequence(value: int) -> bool:
    return type(value) is int and 0 <= value <= MAX_SEQUENCE


def event_id(task_id: UUID, sequence: int) -> str:
    if not _valid_sequence(sequence):
        raise ValueError("Invalid event sequence")
    return f"{task_id}:{sequence}"


def resume_after(
    task_id: UUID,
    watermark: int,
    oldest: int,
    *,
    after: str | None = None,
    last_event_id: str | None = None,
) -> int:
    if (
        not _valid_sequence(watermark)
        or type(oldest) is not int
        or not 1 <= oldest <= watermark + 1
    ):
        raise ValueError("Invalid event metadata")
    if after is not None and last_event_id is not None and after != last_event_id:
        raise CommandError(400, "Invalid event cursor")
    cursor = after if after is not None else last_event_id
    position = 0
    if cursor is not None:
        # Bound work before integer parsing; HTTP access checks precede this helper.
        match = CURSOR.fullmatch(cursor) if len(cursor) <= 56 else None
        if match is None or UUID(match[1]) != task_id:
            raise CommandError(400, "Invalid event cursor")
        position = int(match[2])
        if position > MAX_SEQUENCE or position > watermark:
            raise CommandError(400, "Invalid event cursor")
    if position < oldest - 1:
        raise HistoryExpired(task_id)
    return position
