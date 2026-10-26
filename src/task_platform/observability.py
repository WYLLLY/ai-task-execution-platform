"""Allowlisted structured logging: arbitrary diagnostic text is never serialized."""

import json
import logging
import re
import sys
from datetime import UTC, datetime
from uuid import UUID

EVENTS = frozenset(
    {
        "request.completed",
        "request.failed",
        "command.failed",
        "worker.checked",
        "worker.lease_lost",
        "worker.database_unavailable",
        "trace.completed",
        "execution.progress",
        "stream.invariant",
        "operations.alert",
    }
)


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        event = getattr(record, "event", None)
        fields: dict[str, str | int] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "event": event if event in EVENTS else "diagnostic",
        }
        status = getattr(record, "status", None)
        if type(status) is int and 100 <= status <= 599:
            fields["status"] = status
        for key in ("task_id", "step_id", "attempt_id", "call_id"):
            value = getattr(record, key, None)
            if isinstance(value, str):
                try:
                    if str(UUID(value)) == value:
                        fields[key] = value
                except ValueError:
                    pass
        for key, length in (("trace_id", 32), ("span_id", 16), ("previous_span_id", 16)):
            value = getattr(record, key, None)
            if isinstance(value, str) and re.fullmatch(r"[0-9a-f]{" + str(length) + "}", value):
                fields[key] = value
        alert = getattr(record, "alert_id", None)
        sections = {
            "queue.delayed": "queue",
            "lease.expired": "leases",
            "lease.renewal_failed": "leases",
            "pool.saturated": "pools",
            "runtime.check_missing": "pools",
            "pool.acquire_slow": "pools",
            "accounting.unknown": "accounting",
            "accounting.overrun": "accounting",
            "handler.unavailable": "handlers",
            "events.invariant": "events",
            "storage.disk_full": "storage",
            "storage.check_missing": "storage",
            "backup.failed": "backups",
            "backup.stale": "backups",
            "backup.check_missing": "backups",
        }
        if isinstance(alert, str) and alert in sections:
            fields["alert_id"] = alert
            fields["runbook"] = "docs/operations.md#" + sections[alert]
        # Deliberately omit message, arguments, traceback and arbitrary extras.
        return json.dumps(fields)


def configure_logging() -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JSONFormatter())
    logging.basicConfig(handlers=[handler], level=logging.INFO, force=True)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access", "sqlalchemy", "httpx", "httpcore"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
