"""Correlation is allowlisted; source bodies and exception strings never become telemetry."""

import json
import logging
from uuid import uuid4

from task_platform.observability import JSONFormatter


def test_safe_correlation_ids_are_retained_without_arbitrary_attributes() -> None:
    task, step, attempt, call = (uuid4() for _ in range(4))
    record = logging.makeLogRecord(
        {
            "event": "trace.completed",
            "msg": "PRIVATE_CANARY",
            "task_id": str(task),
            "step_id": str(step),
            "attempt_id": str(attempt),
            "call_id": str(call),
            "trace_id": "a" * 32,
            "span_id": "b" * 16,
            "previous_span_id": "c" * 16,
            "body": "PRIVATE_CANARY",
            "url": "PRIVATE_CANARY",
        }
    )
    value = json.loads(JSONFormatter().format(record))
    assert value["event"] == "trace.completed"
    assert value["task_id"] == str(task) and value["call_id"] == str(call)
    assert value["trace_id"] == "a" * 32 and value["previous_span_id"] == "c" * 16
    assert "PRIVATE_CANARY" not in json.dumps(value)
    record.task_id, record.trace_id, record.span_id = (
        "PRIVATE_CANARY",
        "PRIVATE_CANARY",
        "PRIVATE_CANARY",
    )
    assert "PRIVATE_CANARY" not in JSONFormatter().format(record)


def test_every_operational_rule_retains_its_fixed_alert_and_runbook_in_logs() -> None:
    from task_platform.operations import RULES

    for rule in RULES:
        record = logging.makeLogRecord({"event": "operations.alert", "alert_id": rule.identity})
        value = json.loads(JSONFormatter().format(record))
        assert value.get("alert_id") == rule.identity
        assert value.get("runbook") == "docs/operations.md#" + rule.section
