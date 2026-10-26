"""Bounded per-process fallback limits use a monotonic fixed window."""

import pytest

from task_platform.command_schemas import CommandError
from task_platform.rate_limits import LocalLimits


@pytest.mark.parametrize("limit", [30, 120])
def test_limit_boundary_reset_and_principal_isolation(
    monkeypatch: pytest.MonkeyPatch, limit: int
) -> None:
    monkeypatch.setattr("task_platform.rate_limits.time.monotonic", lambda: 10.0)
    limiter = LocalLimits()
    for _ in range(limit):
        limiter.check("first", limit)
    with pytest.raises(CommandError) as error:
        limiter.check("first", limit)
    assert error.value.status == 429
    limiter.check("second", limit)
    monkeypatch.setattr("task_platform.rate_limits.time.monotonic", lambda: 70.0)
    limiter.check("first", limit)
    assert limiter.windows["first"] == (70.0, 1)


def test_window_cardinality_is_bounded_and_recovers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("task_platform.rate_limits.time.monotonic", lambda: 10.0)
    limiter = LocalLimits()
    for i in range(10000):
        limiter.check(str(i), 120)
    with pytest.raises(CommandError):
        limiter.check("overflow", 120)
    limiter.check("0", 120)
    assert len(limiter.windows) == 10000
    monkeypatch.setattr("task_platform.rate_limits.time.monotonic", lambda: 70.0)
    limiter.check("overflow", 120)
    assert len(limiter.windows) == 1
