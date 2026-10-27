"""Operator commands must exist without accepting network destinations or secrets."""

import subprocess
import sys

import pytest


@pytest.mark.parametrize(
    "arguments,expected",
    [
        (["admin", "retention", "--help"], "--limit"),
        (["admin", "corpus", "delete", "--help"], "--version"),
        (["admin", "recovery", "begin", "--help"], "--file"),
        (["admin", "recovery", "reserve", "--help"], "--file"),
        (["admin", "recovery", "reopen", "--help"], "--file"),
        (["admin", "recovery", "release", "--help"], "--file"),
        (["admin", "delivery", "--help"], "--phase"),
    ],
)
def test_m7_operator_commands(arguments: list[str], expected: str) -> None:
    result = subprocess.run(  # noqa: S603 -- fixed command matrix
        [sys.executable, "-c", "from task_platform.cli import main; main()", *arguments],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0 and expected in result.stdout
