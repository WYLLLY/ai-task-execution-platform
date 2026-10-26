"""Process-local counters contain only bounded aggregate operational signals."""

import logging
from uuid import UUID

event_invariants = 0
renewal_failures = 0


def invariant(task: UUID) -> None:
    global event_invariants
    event_invariants += 1
    logging.getLogger(__name__).error(
        "",
        extra={"event": "operations.alert", "alert_id": "events.invariant", "task_id": str(task)},
    )


def renewal_failed(task: UUID) -> None:
    global renewal_failures
    renewal_failures += 1
    if renewal_failures >= 3:
        logging.getLogger(__name__).error(
            "",
            extra={
                "event": "operations.alert",
                "alert_id": "lease.renewal_failed",
                "task_id": str(task),
            },
        )


def renewal_succeeded() -> None:
    global renewal_failures
    renewal_failures = 0
