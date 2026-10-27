"""Host coordinator for the fixed isolated reference benchmark, no live egress."""

import json
import os
import subprocess
import time
from pathlib import Path

from scripts.load_metrics import reference_targets
from scripts.run_restore_drill import Drill, require


class LoadDrill(Drill):
    def __init__(self) -> None:
        super().__init__()
        self.prefix[self.prefix.index("atp-m7-restore")] = "atp-m7-load"
        override = json.loads(self.override.read_text())
        override["services"]["test"]["environment"]["ATP_LOAD_PROFILE"] = "1"
        override["services"]["test"]["volumes"].append(f"{self.root}/scripts:/app/scripts:ro")
        override["services"]["test"]["cpus"] = 1.5
        override["services"]["test"]["mem_limit"] = "1536m"
        override["services"]["demo"] = {
            "image": "ai-task-platform:m1-test",
            "entrypoint": ["python", "-m", "tests.support.load_http"],
            "command": [],
            "environment": {"ATP_LOAD_DEMO": "1", "PYTHONPATH": "/app/src"},
            "volumes": [
                f"{self.root}/src:/app/src:ro",
                f"{self.root}/tests:/app/tests:ro",
                f"{self.root}/scripts:/app/scripts:ro",
            ],
        }
        override["services"]["postgres"] = {
            "command": [
                "postgres",
                "-c",
                "max_connections=50",
                "-c",
                "shared_buffers=128MB",
                "-c",
                "track_commit_timestamp=on",
                "-c",
                "log_statement=none",
                "-c",
                "log_min_error_statement=panic",
                "-c",
                "log_error_verbosity=terse",
            ]
        }
        self.override.write_text(json.dumps(override))
        self.prefix.extend(["-f", "compose.proxy.yaml"])

    def main(self) -> dict[str, object]:
        require(not self.compose("ps", "-aq").strip(), "load project must initially be absent")
        try:
            self.compose("up", "-d", "--wait", "api", "demo", "proxy")
            started = time.monotonic()
            log = self.private / "campaign.log"
            with log.open("wb") as output:
                result = subprocess.run(  # noqa: S603 -- fixed isolated fixture command
                    [
                        *self.prefix,
                        "run",
                        "--rm",
                        "--no-deps",
                        "test",
                        "python",
                        "-m",
                        "tests.support.load_profile",
                    ],
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    timeout=2100,
                    check=False,
                )
            require(result.returncode == 0, "campaign; inspect private campaign.log")
            values = json.loads((self.private / "load-result.json").read_text())
            effects = (
                self.compose(
                    "exec",
                    "-T",
                    "demo-db",
                    "psql",
                    "-U",
                    "postgres",
                    "-d",
                    "demo",
                    "-Atc",
                    "SELECT count(*) FROM report_effects",
                )
                .decode()
                .strip()
            )
            writes = (
                self.compose(
                    "exec",
                    "-T",
                    "demo-db",
                    "psql",
                    "-U",
                    "postgres",
                    "-d",
                    "demo",
                    "-Atc",
                    "SELECT count(*) FROM invocation_journal",
                )
                .decode()
                .strip()
            )
            values.update(
                remote_effects=int(effects),
                demo_physical_writes=int(writes),
                host_elapsed_seconds=round(time.monotonic() - started, 3),
            )
            values["status"] = "FAIL"
            Path("reviews/evidence/m7-reference-load.json").write_text(
                json.dumps(values, indent=2, sort_keys=True) + "\n"
            )
            overload = self.compose(
                "run",
                "--rm",
                "--no-deps",
                "test",
                "python",
                "-m",
                "tests.support.load_profile",
                "overload",
            )
            values["overload"] = json.loads(overload)
            values["targets"] = reference_targets(values)
            values["targets"]["overload_bounded"] = values["overload"]["bounded"]
            values["status"] = "PASS" if all(values["targets"].values()) else "FAIL"
            return dict(values)
        finally:
            self.compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    os.environ["API_PORT"] = "0"
    result = LoadDrill().main()
    output = Path("reviews/evidence/m7-reference-load.json")
    output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))
    raise SystemExit(0 if result["status"] == "PASS" else 1)
