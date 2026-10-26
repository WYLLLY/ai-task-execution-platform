"""M6 cursor behavior; HTTP authorization must precede this pure validation."""

from uuid import UUID

import pytest

from task_platform.command_schemas import CommandError
from task_platform.event_cursor import HistoryExpired, event_id, resume_after

TASK = UUID("a1111111-1111-4111-8111-111111111111")
OTHER = UUID("b2222222-2222-4222-8222-222222222222")
MAX_SEQUENCE = 2**63 - 1


@pytest.mark.parametrize("sequence", [0, 1, 7, MAX_SEQUENCE])
def test_server_id_round_trips_without_changing_resume_position(sequence: int) -> None:
    cursor = event_id(TASK, sequence)
    assert cursor == f"{TASK}:{sequence}"
    assert resume_after(TASK, sequence, 1, after=cursor) == sequence


def test_missing_cursor_replays_from_zero() -> None:
    assert resume_after(TASK, 7, 1) == 0


def test_header_query_and_matching_pair_are_equivalent() -> None:
    cursor = f"{TASK}:7"
    assert resume_after(TASK, 9, 1, after=cursor) == 7
    assert resume_after(TASK, 9, 1, last_event_id=cursor) == 7
    assert resume_after(TASK, 9, 1, after=cursor, last_event_id=cursor) == 7


@pytest.mark.parametrize(
    "cursor",
    [
        "",
        "untrusted-secret-not-a-cursor",
        f"{TASK}:-1",
        f"{TASK}:+1",
        f"{TASK}:1.0",
        f"{TASK}:١",
        f"{TASK}: 1",
        f"{TASK}:1:2",
        f"{TASK}:1\n",
        f"{TASK}:1\r",
        f"{TASK}:1\x00",
        f"{TASK}:{MAX_SEQUENCE + 1}",
        f"{TASK}:" + "9" * 1000,
        f"{OTHER}:1",
    ],
)
def test_untrusted_invalid_cursor_is_bounded_and_not_reflected(cursor: str) -> None:
    with pytest.raises(CommandError) as failure:
        resume_after(TASK, MAX_SEQUENCE, 1, after=cursor)
    assert failure.value.status == 400
    assert failure.value.detail == "Invalid event cursor"


def test_conflicting_header_and_query_are_rejected() -> None:
    with pytest.raises(CommandError) as failure:
        resume_after(TASK, 9, 1, after=f"{TASK}:1", last_event_id=f"{TASK}:2")
    assert failure.value.status == 400


def test_future_cursor_is_rejected() -> None:
    with pytest.raises(CommandError) as failure:
        resume_after(TASK, 7, 1, after=f"{TASK}:8")
    assert failure.value.status == 400


@pytest.mark.parametrize("watermark,oldest,position", [(7, 7, 6), (7, 8, 7), (0, 1, 0)])
def test_position_immediately_before_retained_history_is_valid(
    watermark: int, oldest: int, position: int
) -> None:
    assert resume_after(TASK, watermark, oldest, after=f"{TASK}:{position}") == position


@pytest.mark.parametrize("cursor", [None, f"{TASK}:5"])
def test_expired_position_requires_the_authorized_task_snapshot(cursor: str | None) -> None:
    with pytest.raises(HistoryExpired) as failure:
        resume_after(TASK, 9, 7, after=cursor)
    assert failure.value.status == 410
    assert failure.value.snapshot_url == f"/v1/tasks/{TASK}"


@pytest.mark.parametrize("sequence", [-1, MAX_SEQUENCE + 1, True])
def test_server_cannot_issue_an_invalid_sequence(sequence: int) -> None:
    with pytest.raises(ValueError):
        event_id(TASK, sequence)


@pytest.mark.parametrize("watermark,oldest", [(-1, 1), (7, 0), (7, 9), (MAX_SEQUENCE + 1, 1)])
def test_invalid_server_metadata_is_an_invariant_error(watermark: int, oldest: int) -> None:
    with pytest.raises(ValueError):
        resume_after(TASK, watermark, oldest)
