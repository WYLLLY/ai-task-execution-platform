"""Run the disposable suite with an external, bounded Redis fault coordinator.

The test container receives only a temporary marker directory, never a Docker socket.
The independent controller stops/restarts only this repository's test Redis service.
"""

import os
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def main() -> int:
    compose = [
        *shlex.split(os.environ.get("COMPOSE", "docker compose")),
        "-p",
        "atp-m1-test",
        "-f",
        "compose.yaml",
        "-f",
        "compose.demo.yaml",
        "-f",
        "compose.proxy.yaml",
    ]
    repository = Path(__file__).resolve().parent.parent
    with tempfile.TemporaryDirectory(prefix=".m6-fault-", dir=repository) as directory:
        marker = Path(directory)
        development: list[str] = []
        if os.environ.get("ATP_VERIFY_SOURCE") == "1":
            root = Path(__file__).resolve().parent.parent
            for name in ("src", "tests", "migrations"):
                development.extend(["-v", f"{root / name}:/app/{name}:ro"])
            development.extend(["-e", "PYTHONPATH=/app/src"])
        command = [
            *compose,
            "run",
            "--rm",
            "--no-deps",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "-v",
            f"{directory}:/fault-coordinator",
            "-e",
            "ATP_FAULT_COORDINATOR=/fault-coordinator",
            *development,
            "test",
            "pytest",
            "-p",
            "no:cacheprovider",
            *(sys.argv[1:] or ["-q", "--tb=short"]),
        ]
        process = subprocess.Popen(command)  # noqa: S603 -- fixed Compose project, pytest arguments
        stopped = False
        fault_started = 0.0
        try:
            while process.poll() is None:
                if (marker / "ready").exists() and not (marker / "stopped").exists():
                    subprocess.run([*compose, "stop", "-t", "2", "redis"], check=True, timeout=15)  # noqa: S603
                    stopped = True
                    fault_started = time.monotonic()
                    (marker / "stopped").touch()
                if stopped and (
                    (marker / "resume").exists() or time.monotonic() - fault_started > 45
                ):
                    subprocess.run([*compose, "start", "redis"], check=True, timeout=20)  # noqa: S603
                    stopped = False
                    (marker / "recovered").touch()
                time.sleep(0.05)
            report = marker / "results.json"
            if report.exists():
                print("M6_DEGRADED_PROFILE " + report.read_text(), flush=True)
            return int(process.returncode or 0)
        finally:
            if stopped:
                subprocess.run([*compose, "start", "redis"], check=True, timeout=20)  # noqa: S603
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)


if __name__ == "__main__":
    raise SystemExit(main())
