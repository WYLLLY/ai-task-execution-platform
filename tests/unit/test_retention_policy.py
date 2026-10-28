"""M7 retention clocks are explicit; uncertainty never expires financial records."""

from datetime import UTC, datetime, timedelta

import pytest

from task_platform.retention_policy import retention_due

NOW = datetime(2026, 10, 27, tzinfo=UTC)


@pytest.mark.parametrize("age,due", [(29, False), (30, True), (31, True)])
def test_sensitive_content_waits_30_days_after_termination(age: int, due: bool) -> None:
    value = retention_due(
        NOW, accepted_at=NOW - timedelta(days=90), terminal_at=NOW - timedelta(days=age)
    )
    assert value.content is due


def test_nonterminal_content_and_unresolved_finances_never_expire_by_age() -> None:
    value = retention_due(NOW, accepted_at=NOW - timedelta(days=365))
    assert not value.content and not value.financial
    assert value.response


@pytest.mark.parametrize("seconds,due", [(30 * 86400 - 1, False), (30 * 86400, True)])
def test_response_clock_starts_at_acceptance(seconds: int, due: bool) -> None:
    assert retention_due(NOW, accepted_at=NOW - timedelta(seconds=seconds)).response is due


@pytest.mark.parametrize("seconds,due", [(90 * 86400 - 1, False), (90 * 86400, True)])
def test_tombstone_has_its_own_90_day_minimum(seconds: int, due: bool) -> None:
    value = retention_due(
        NOW, accepted_at=NOW - timedelta(days=200), tombstoned_at=NOW - timedelta(seconds=seconds)
    )
    assert value.tombstone is due


@pytest.mark.parametrize("exposure", [1, 2**63 - 1])
def test_unresolved_exposure_blocks_financial_retention_even_after_resolution_date(
    exposure: int,
) -> None:
    value = retention_due(
        NOW,
        accepted_at=NOW - timedelta(days=365),
        resolved_at=NOW - timedelta(days=100),
        exposure=exposure,
    )
    assert not value.financial


@pytest.mark.parametrize("seconds,due", [(90 * 86400 - 1, False), (90 * 86400, True)])
def test_financial_clock_waits_for_resolution(seconds: int, due: bool) -> None:
    value = retention_due(
        NOW, accepted_at=NOW - timedelta(days=200), resolved_at=NOW - timedelta(seconds=seconds)
    )
    assert value.financial is due


@pytest.mark.parametrize("invalid", [True, -1, 2**63, 1.1])
def test_exposure_is_a_bounded_nonnegative_integer(invalid: object) -> None:
    with pytest.raises(ValueError):
        retention_due(NOW, accepted_at=NOW, exposure=invalid)


@pytest.mark.parametrize("stamp", [NOW.replace(tzinfo=None), NOW + timedelta(seconds=1)])
def test_naive_and_future_retention_clocks_are_rejected(stamp: datetime) -> None:
    with pytest.raises(ValueError):
        retention_due(NOW, accepted_at=stamp)


def test_termination_cannot_precede_acceptance() -> None:
    with pytest.raises(ValueError):
        retention_due(NOW, accepted_at=NOW, terminal_at=NOW - timedelta(seconds=1))


def test_boundary_is_instant_based_across_timezones() -> None:
    from datetime import timezone

    earlier = NOW - timedelta(days=30)
    assert retention_due(NOW, accepted_at=earlier.astimezone(timezone(timedelta(hours=9)))).response


def test_open_reconciliation_retains_finances_without_numeric_exposure() -> None:
    assert not retention_due(
        NOW,
        accepted_at=NOW - timedelta(days=200),
        resolved_at=NOW - timedelta(days=100),
        reconciliation_open=True,
    ).financial


@pytest.mark.parametrize("field", ["terminal_at", "resolved_at", "tombstoned_at"])
@pytest.mark.parametrize("bad", [NOW.replace(tzinfo=None), NOW + timedelta(seconds=1)])
def test_every_optional_clock_is_validated(field: str, bad: datetime) -> None:
    with pytest.raises(ValueError):
        retention_due(
            NOW,
            accepted_at=NOW - timedelta(days=365),
            terminal_at=bad if field == "terminal_at" else None,
            resolved_at=bad if field == "resolved_at" else None,
            tombstoned_at=bad if field == "tombstoned_at" else None,
        )


def test_naive_current_time_is_rejected() -> None:
    with pytest.raises(ValueError):
        retention_due(NOW.replace(tzinfo=None), accepted_at=NOW)


def test_dst_transition_cannot_shorten_the_30_day_retention_interval() -> None:
    from zoneinfo import ZoneInfo

    paris = ZoneInfo("Europe/Paris")
    accepted = datetime(2026, 3, 1, 12, tzinfo=paris)
    before = datetime(2026, 3, 31, 12, tzinfo=paris)  # Only29d23h elapsed across DST.
    assert not retention_due(before, accepted_at=accepted, terminal_at=accepted).content
    assert not retention_due(before, accepted_at=accepted).response


def test_maximum_valid_timestamp_has_no_overflow_or_premature_expiry() -> None:
    stamp = datetime.max.replace(tzinfo=UTC)
    value = retention_due(
        stamp, accepted_at=stamp, terminal_at=stamp, tombstoned_at=stamp, resolved_at=stamp
    )
    assert not any((value.content, value.response, value.tombstone, value.financial))
