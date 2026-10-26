"""Bounded actionable alerts: timers and fixed metadata never echo source data."""

import pytest

from task_platform.operations import Alerts, prometheus


def test_queue_and_pool_alerts_require_the_full_hold_and_reset() -> None:
    alerts = Alerts()
    values = {"oldest_ready_seconds": 31, "pool_utilization_percent": 81}
    assert alerts.evaluate(values, 0) == []
    assert alerts.evaluate(values, 59) == []
    assert {a["id"] for a in alerts.evaluate(values, 60)} == {"pool.saturated"}
    assert {a["id"] for a in alerts.evaluate(values, 120)} == {"pool.saturated", "queue.delayed"}
    assert alerts.evaluate({}, 121) == []
    assert alerts.evaluate(values, 122) == []


@pytest.mark.parametrize(
    "name,value,expected",
    [
        ("expired_lease_seconds", 16, "lease.expired"),
        ("renewal_failures", 3, "lease.renewal_failed"),
        ("pool_acquire_milliseconds", 1001, "pool.acquire_slow"),
        ("oldest_unknown_seconds", 901, "accounting.unknown"),
        ("overrun_calls", 1, "accounting.overrun"),
        ("handler_unavailable_tasks", 1, "handler.unavailable"),
        ("event_invariants", 1, "events.invariant"),
        ("disk_used_percent", 81, "storage.disk_full"),
        ("backup_failed", 1, "backup.failed"),
        ("backup_age_seconds", 86401, "backup.stale"),
    ],
)
def test_injected_failure_has_fixed_actionable_alert(name: str, value: int, expected: str) -> None:
    result = Alerts().evaluate({name: value, "PRIVATE_CANARY": 999}, 0)
    assert len(result) == 1 and result[0]["id"] == expected
    assert result[0]["action"] and result[0]["runbook"].startswith("docs/operations.md#")
    assert "PRIVATE_CANARY" not in str(result)


def test_prometheus_uses_only_fixed_metric_names_and_no_user_labels() -> None:
    value = prometheus({"unknown_calls": 2, "exposure_micro_usd": 4004, "PRIVATE_CANARY": 100})
    assert "atp_unknown_calls 2.0" in value
    assert "atp_exposure_micro_usd 4004.0" in value
    assert "PRIVATE_CANARY" not in value and "task_id" not in value
