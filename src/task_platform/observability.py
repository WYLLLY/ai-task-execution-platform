"""Allowlisted structured logging: arbitrary diagnostic text is never serialized."""

import json
import logging
import sys
from datetime import UTC, datetime

EVENTS = frozenset({"request.completed", "request.failed", "command.failed", "worker.checked"})


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
