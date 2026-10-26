"""Provider CLI entrypoints and live-smoke authorization gates."""

import os
import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (["admin", "provider-configure", "--help"], "--file"),
        (["admin", "accounting", "--help"], "--call"),
        (["worker", "--help"], "--openai"),
    ],
)
def test_provider_commands_are_available(arguments: list[str], expected: str) -> None:
    result = subprocess.run(  # noqa: S603 -- fixed parametrized CLI arguments
        [sys.executable, "-c", "from task_platform.cli import main; main()", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and expected in result.stdout


@pytest.mark.parametrize("authorized", [False, True])
def test_smoke_without_distinct_authorization_and_cap_is_skipped(authorized: bool) -> None:
    env = {k: v for k, v in os.environ.items() if not k.startswith("OPENAI_")}
    env["OPENAI_API_KEY"] = "secret-canary-never-send"
    if authorized:
        env["OPENAI_SMOKE_AUTHORIZED"] = "yes"
    result = subprocess.run(
        [sys.executable, "-c", "from task_platform.cli import main; main()", "smoke-openai"],
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and "SKIPPED" in result.stdout
    assert "secret-canary" not in result.stdout + result.stderr
