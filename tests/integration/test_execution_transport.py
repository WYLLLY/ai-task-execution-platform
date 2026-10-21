"""Physical HTTP counts, effect journal and absolute deadlines with no live egress."""

import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

import pytest

from task_platform.execution import Permit
from task_platform.transport import SimulationTransport

pytestmark = pytest.mark.integration


@dataclass
class Simulator:
    url: str
    journal: Path

    def count(self, kind: str) -> int:
        if not self.journal.exists():
            return 0
        return sum(
            json.loads(line)["kind"] == kind for line in self.journal.read_text().splitlines()
        )


@pytest.fixture
async def simulator(tmp_path: Path) -> AsyncIterator[Simulator]:
    journal = tmp_path / "remote.jsonl"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.simulator",
        "--journal",
        str(journal),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert process.stdout
    try:
        port = int(await asyncio.wait_for(process.stdout.readline(), 5))
        yield Simulator(f"http://127.0.0.1:{port}", journal)
    finally:
        process.terminate()
        await asyncio.wait_for(process.wait(), 5)


@pytest.mark.parametrize(
    "scenario,outcome,effects",
    [
        ("success", "succeeded", 1),
        ("drop", "unknown", 1),
        ("slow", "unknown", 1),
        ("redirect", "unknown", 1),
        ("malformed", "unknown", 1),
        ("oversized", "unknown", 1),
        ("missing_usage", "succeeded", 1),
        ("retryable", "retryable", 0),
        ("rejected", "rejected", 0),
    ],
)
async def test_transport_bounds_and_independent_physical_counts(
    simulator: Simulator,
    scenario: str,
    outcome: str,
    effects: int,
) -> None:
    payload = json.dumps({"scenario": scenario}).encode()
    permit = Permit(uuid4(), payload, 0.2, time.monotonic())
    started = time.monotonic()
    async with SimulationTransport(simulator.url) as transport:
        result = await transport.send(permit)
    assert result.outcome == outcome
    assert simulator.count("request") == 1
    assert simulator.count("effect") == effects
    assert time.monotonic() - started < 1.5
    if scenario == "missing_usage":
        assert result.cost is None and result.tokens is None


async def test_expired_authorization_never_sends(simulator: Simulator) -> None:
    permit = Permit(uuid4(), b"{}", 0.1, time.monotonic() - 1)
    async with SimulationTransport(simulator.url) as transport:
        result = await transport.send(permit)
    assert result.outcome == "unknown"
    assert simulator.count("request") == 0


async def test_implicit_proxy_environment_cannot_redirect(
    simulator: Simulator, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    async with SimulationTransport(simulator.url) as transport:
        result = await transport.send(Permit(uuid4(), b"{}", 1, time.monotonic()))
    assert result.outcome == "succeeded"
    assert simulator.count("request") == 1
