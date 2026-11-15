"""Contract tests for the PostgreSQL cutover gate."""

import argparse

import pytest

from scripts import verify_cutover
from scripts.verify_cutover import Snapshot, service_name, verify

SERVICES = {
    f"{boundary}_{application}": f"{boundary}_{application}"
    for boundary in ("source", "restored")
    for application in ("platform", "demo")
}


@pytest.mark.parametrize("database", ["platform", "demo"])
def test_equal_row_counts_cannot_mask_divergent_data(
    monkeypatch: pytest.MonkeyPatch, database: str
) -> None:
    def capture(service: str, application: str) -> Snapshot:
        digest = "different" if service == f"restored_{database}" else "equal"
        return Snapshot(service, 17, {application: digest})

    monkeypatch.setattr(verify_cutover, "capture", capture)
    with pytest.raises(ValueError, match=database):
        verify(SERVICES)


@pytest.mark.parametrize("change", ["same_cluster", "wrong_major", "none"])
def test_requires_two_independent_source_and_restore_pairs(
    monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    def capture(service: str, application: str) -> Snapshot:
        cluster = "source_platform" if change == "same_cluster" else service
        major = 18 if change == "wrong_major" else 17
        return Snapshot(cluster, major, {application: "equal"})

    monkeypatch.setattr(verify_cutover, "capture", capture)
    if change == "none":
        assert verify(SERVICES) == {"status": "PASS", "platform_relations": 1, "demo_relations": 1}
    else:
        with pytest.raises(ValueError, match="distinct clusters|PostgreSQL 17"):
            verify(SERVICES)


def test_service_name_is_a_label_not_a_connection_string() -> None:
    assert service_name("atp-source_2026") == "atp-source_2026"
    with pytest.raises(argparse.ArgumentTypeError):
        service_name("host=remote")
