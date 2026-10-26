"""Parsing/supervising a command must not initialize execution in the parent."""

import json
import subprocess
import sys

import pytest


@pytest.mark.parametrize("mode", ["--help", "--simulate", "--analysis", "--openai"])
def test_cli_parent_does_not_initialize_execution_runtime(mode: str) -> None:
    script = """
import importlib.abc
import json
import sys
initialized = []

class RuntimeGuard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname in {
            "task_platform.execution", "task_platform.analysis_worker",
            "task_platform.workflow", "task_platform.provider_commands", "uvicorn",
        }:
            initialized.append(fullname)

sys.meta_path.insert(0, RuntimeGuard())
import task_platform.supervisor
# The real child is covered by the unchanged five-second subprocess integration test.
task_platform.supervisor.supervise = lambda runner: 0
from task_platform.cli import main
try:
    main()
except SystemExit as exc:
    if exc.code != 0:
        raise
print(json.dumps(initialized))
"""
    arguments = [mode] if mode == "--help" else ["worker", mode, "--once"]
    process = subprocess.run(  # noqa: S603 -- fixed synthetic command, no external inputs
        [sys.executable, "-c", script, *arguments], capture_output=True, text=True, timeout=5
    )
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout.splitlines()[-1]) == []
