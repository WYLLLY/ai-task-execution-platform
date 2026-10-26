"""Actual HTTP requests to an independent synthetic process, never OpenAI."""

import asyncio
import json
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import uuid4

import pytest
from pydantic import SecretStr

from task_platform.execution import Permit
from task_platform.openai_transport import ResponsesTransport
from task_platform.responses import validate_profile
from tests.unit.test_responses import profile_data

from .test_execution_transport import Simulator

pytestmark = pytest.mark.integration


@pytest.fixture
async def responses_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> AsyncIterator[Simulator]:
    monkeypatch.setenv("ATP_TEST_PROFILE", "1")
    journal = tmp_path / "physical.jsonl"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.responses_server",
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
    "scenario,expected",
    [
        ("success", "succeeded"),
        ("missing_usage", "succeeded"),
        ("redirect", "unknown"),
        ("429", "unknown"),
        ("malformed", "unknown"),
        ("oversized", "unknown"),
        ("slow", "unknown"),
    ],
)
async def test_physical_requests_and_absolute_deadlines(
    responses_server: Simulator, scenario: str, expected: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:1")
    monkeypatch.setenv("NO_PROXY", "")
    call = uuid4()
    permit = Permit(
        call, json.dumps({"metadata": {"scenario": scenario}}).encode(), 0.4, time.monotonic()
    )
    started = time.monotonic()
    async with ResponsesTransport(
        validate_profile(profile_data()),
        SecretStr("synthetic-test-key"),
        test_url=responses_server.url,
    ) as transport:
        result = await transport.send(permit)
    assert result.outcome == expected
    assert time.monotonic() - started < 1.5
    assert responses_server.count("request") == 1
    journal = json.loads(responses_server.journal.read_text().splitlines()[0])
    assert journal["path"] == "/v1/responses"
    assert journal["project"] == "proj_synthetic" and journal["transmission"] == str(call)
    if scenario == "429":
        assert result.retry_after_seconds == 3
    if scenario == "missing_usage":
        assert result.cost is None


async def test_expired_permit_sends_nothing(responses_server: Simulator) -> None:
    async with ResponsesTransport(
        validate_profile(profile_data()),
        SecretStr("synthetic-test-key"),
        test_url=responses_server.url,
    ) as transport:
        result = await transport.send(Permit(uuid4(), b"{}", 0.1, time.monotonic() - 1))
    assert result.outcome == "unknown" and responses_server.count("request") == 0
