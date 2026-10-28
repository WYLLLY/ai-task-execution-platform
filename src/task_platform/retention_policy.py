"""Explicit retention clocks; this policy never performs deletion or releases exposure."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta


@dataclass(frozen=True)
class Due:
    content: bool = False
    response: bool = False
    tombstone: bool = False
    financial: bool = False


def retention_due(
    now: datetime,
    *,
    accepted_at: datetime,
    terminal_at: datetime | None = None,
    tombstoned_at: datetime | None = None,
    resolved_at: datetime | None = None,
    exposure: object = 0,
    reconciliation_open: bool = False,
) -> Due:
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("Current time must be timezone aware")
    if type(exposure) is not int or not 0 <= exposure <= 2**63 - 1:
        raise ValueError("Exposure must be a bounded nonnegative integer")
    if type(reconciliation_open) is not bool:
        raise ValueError("Reconciliation status must be explicit")
    now = now.astimezone(UTC)

    def normalized(stamp: datetime) -> datetime:
        if stamp.tzinfo is None or stamp.utcoffset() is None:
            raise ValueError("Retention timestamps must be timezone aware")
        value = stamp.astimezone(UTC)
        if value > now:
            raise ValueError("Retention timestamps cannot be future")
        return value

    accepted_at = normalized(accepted_at)
    terminal_at = normalized(terminal_at) if terminal_at is not None else None
    tombstoned_at = normalized(tombstoned_at) if tombstoned_at is not None else None
    resolved_at = normalized(resolved_at) if resolved_at is not None else None
    if any(
        stamp is not None and stamp < accepted_at
        for stamp in (terminal_at, tombstoned_at, resolved_at)
    ):
        raise ValueError("Retention clocks cannot precede acceptance")
    return Due(
        content=terminal_at is not None and now - terminal_at >= timedelta(days=30),
        response=now - accepted_at >= timedelta(days=30),
        tombstone=tombstoned_at is not None and now - tombstoned_at >= timedelta(days=90),
        financial=(
            resolved_at is not None
            and now - resolved_at >= timedelta(days=90)
            and exposure == 0
            and not reconciliation_open
        ),
    )
