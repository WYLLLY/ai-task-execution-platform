"""Actual encrypted pg_dump/pg_restore drill, confined to one disposable project.

Run: AGE=/path/to/age COMPOSE='docker compose' python -m scripts.run_restore_drill
No live credentials or provider access. Private keys/client receipts remain in
an ignored owner-only directory; only sanitized results go to reviews/evidence.
"""

import hashlib
import json
import os
import re
import secrets
import shlex
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from scripts.backup_artifact import encrypted_backup, verify_backup

PROJECT = "atp-m7-restore"


def require(condition: bool, label: str) -> None:
    if not condition:
        raise RuntimeError("Restore drill failed: " + label)


class Drill:
    def __init__(self) -> None:
        self.root = Path(__file__).resolve().parent.parent
        self.private = self.root / ".secrets" / ("m7-restore-" + uuid4().hex)
        self.private.mkdir(mode=0o700)
        self.age = Path(os.environ.get("AGE", "age"))
        self.identity = self.private / "identity.txt"
        self.override = self.private / "compose.json"
        key = self.private / "demo-key"
        key.write_bytes((self.root / ".secrets/demo_service_key").read_bytes())
        key.chmod(0o444)
        self.override.write_text(
            json.dumps(
                {
                    "services": {
                        "test": {
                            "user": f"{os.getuid()}:{os.getgid()}",
                            "environment": {
                                "ATP_RESTORE_DRILL": "1",
                                "DISPATCH_ENABLED": "false",
                                "PYTHONPATH": "/app/src",
                            },
                            "volumes": [
                                f"{self.private}:/drill",
                                f"{self.root}/src:/app/src:ro",
                                f"{self.root}/tests:/app/tests:ro",
                                f"{self.root}/migrations:/app/migrations:ro",
                            ],
                        }
                    },
                    "secrets": {"demo_service_key": {"file": str(key)}},
                }
            )
        )
        self.prefix = [
            *shlex.split(os.environ.get("COMPOSE", "docker compose")),
            "-p",
            PROJECT,
            "-f",
            "compose.yaml",
            "-f",
            "compose.demo.yaml",
            "-f",
            str(self.override),
        ]

    def run(
        self, arguments: list[str], *, data: bytes | None = None, timeout: float = 120
    ) -> bytes:
        result = subprocess.run(  # noqa: S603 -- fixed operator-owned commands
            arguments, input=data, capture_output=True, timeout=timeout, cwd=self.root, check=False
        )
        if result.returncode:
            diagnostic = (result.stderr + result.stdout).decode(errors="replace")
            secret_files = list((self.root / ".secrets").glob("*password"))
            secret_files += [self.private / "demo-key"]
            for source in secret_files:
                if source.is_file():
                    value = source.read_text().strip()
                    if value:
                        diagnostic = diagnostic.replace(value, "[REDACTED]")
            state_file = self.private / "private-state.json"
            if state_file.exists():
                state = json.loads(state_file.read_text())
                for name in ("token", "worker_password", "old_demo_key"):
                    value = state.get(name)
                    if isinstance(value, str) and value:
                        diagnostic = diagnostic.replace(value, "[REDACTED]")
            diagnostic = re.sub(r"Bearer [A-Za-z0-9_-]+", "Bearer [REDACTED]", diagnostic)
            log = self.private / "diagnostic.txt"
            log.write_text(diagnostic)
            log.chmod(0o600)
            raise RuntimeError("Isolated restore command failed; sanitized diagnostic: " + str(log))
        return result.stdout

    def compose(self, *arguments: str, data: bytes | None = None) -> bytes:
        return self.run([*self.prefix, *arguments], data=data)

    def sql(self, value: str, database: str = "atp") -> str:
        return (
            self.compose(
                "exec",
                "-T",
                "postgres",
                "psql",
                "-U",
                "postgres",
                "-d",
                database,
                "-v",
                "ON_ERROR_STOP=1",
                "-At",
                data=value.encode(),
            )
            .decode()
            .strip()
        )

    def phase(
        self, name: str, *, epoch: str | None = None, arguments: list[str] | None = None
    ) -> dict[str, Any]:
        options = ["-e", "EXECUTION_EPOCH=" + epoch] if epoch else []
        output = self.compose(
            "run",
            "--rm",
            "--no-deps",
            *options,
            "test",
            "python",
            "-m",
            "tests.support.restore_drill",
            name,
            *(arguments or []),
        )
        return dict(json.loads(output)) if output.strip() else {}

    def restore(
        self, target: str, archive: Path, manifest: dict[str, object], incident: datetime
    ) -> dict[str, object]:
        require(
            target in {"atp_restore_before_b", "atp_restore_before_creation"}, "isolated target"
        )
        started = time.monotonic()
        verify_backup(archive, manifest, now=datetime.now(UTC))
        # Authenticate the entire ciphertext before feeding any SQL to pg_restore.
        self.run(
            [
                str(self.age),
                "--decrypt",
                "--identity",
                str(self.identity),
                "-o",
                os.devnull,
                str(archive),
            ]
        )
        self.sql(f"CREATE DATABASE {target} OWNER atp_migrator;")
        self.sql(
            f"REVOKE ALL ON DATABASE {target} FROM PUBLIC; "
            f"GRANT CONNECT ON DATABASE {target} TO atp_api,atp_worker,atp_admin,atp_migrator;"
        )
        decrypt = subprocess.Popen(  # noqa: S603 -- operator-owned age, fixed archive
            [str(self.age), "--decrypt", "--identity", str(self.identity), str(archive)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        try:
            require(decrypt.stdout is not None, "decrypt pipe")
            restored = subprocess.run(  # noqa: S603 -- fixed private PostgreSQL target
                [
                    *self.prefix,
                    "exec",
                    "-T",
                    "postgres",
                    "pg_restore",
                    "-U",
                    "postgres",
                    "--dbname",
                    target,
                    "--exit-on-error",
                    "--single-transaction",
                ],
                stdin=decrypt.stdout,
                capture_output=True,
                timeout=120,
                check=False,
            )
            if decrypt.stdout is not None:
                decrypt.stdout.close()
            require(
                decrypt.wait(timeout=10) == 0 and restored.returncode == 0,
                "authenticated SQL restore",
            )
        finally:
            if decrypt.poll() is None:
                decrypt.kill()
                decrypt.wait(timeout=5)
        epoch = str(uuid4())
        checked = self.phase(
            "check",
            epoch=epoch,
            arguments=[
                "--database",
                target,
                "--point",
                str(manifest["started_at"]),
                "--incident",
                incident.isoformat(),
            ],
        )
        checked["rto_safe_reopening_seconds"] = round(time.monotonic() - started, 3)
        point = datetime.fromisoformat(str(manifest["started_at"]))
        checked["rpo_potential_loss_seconds"] = round((incident - point).total_seconds(), 3)
        return checked

    def main(self) -> dict[str, object]:
        require(not self.compose("ps", "-aq").strip(), "project must initially be absent")
        try:
            self.run([str(self.age.with_name("age-keygen")), "-o", str(self.identity)])
            recipient = (
                self.run([str(self.age.with_name("age-keygen")), "-y", str(self.identity)])
                .decode()
                .strip()
            )
            self.compose("up", "-d", "--wait", "postgres", "redis", "demo-db", "demo")
            self.compose("run", "--rm", "--no-deps", "migrate", "upgrade", "head")
            self.phase("baseline")
            snapshots: dict[str, tuple[Path, dict[str, object]]] = {}
            for name, phase in (("before_creation", None), ("before_b", "prepare")):
                if phase:
                    self.phase(phase)
                archive = self.private / (name + ".age")
                manifest = encrypted_backup(
                    [
                        *self.prefix,
                        "exec",
                        "-T",
                        "postgres",
                        "pg_dump",
                        "-U",
                        "postgres",
                        "--format=custom",
                        "atp",
                    ],
                    recipient,
                    archive,
                    age_command=str(self.age),
                    timeout=120,
                )
                (self.private / (name + ".manifest.json")).write_text(json.dumps(manifest))
                snapshots[name] = (archive, manifest)
            self.phase("effect")
            incident = datetime.now(UTC)
            isolation_started = time.monotonic()
            state_path = self.private / "private-state.json"
            state = dict(json.loads(state_path.read_text()))
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
            require(effects == "1", "one independent remote effect before restore")
            # Operational isolation is outside both backups and is not undone by pg_restore.
            state["worker_password"] = secrets.token_urlsafe(36)
            state["isolation_proof"] = hashlib.sha256(
                b"synthetic-isolation-" + secrets.token_bytes(32)
            ).hexdigest()
            state_path.write_text(json.dumps(state))
            state_path.chmod(0o600)
            self.sql(
                "ALTER ROLE atp_worker NOLOGIN; SELECT pg_terminate_backend(pid) "
                "FROM pg_stat_activity WHERE usename='atp_worker' AND pid<>pg_backend_pid(); "
                "ALTER ROLE atp_worker PASSWORD '" + state["worker_password"] + "' LOGIN;"
            )
            demo_key = self.private / "demo-key"
            demo_key.chmod(0o600)
            demo_key.write_text(secrets.token_urlsafe(48))
            demo_key.chmod(0o444)
            self.compose("up", "-d", "--no-deps", "--force-recreate", "demo")
            isolation_seconds = round(time.monotonic() - isolation_started, 3)
            # Reject corrupt encrypted bytes independently of the manifest hash.
            corrupted = self.private / "corrupted.age"
            raw = bytearray(snapshots["before_b"][0].read_bytes())
            raw[-1] ^= 1
            corrupted.write_bytes(raw)
            result = subprocess.run(  # noqa: S603 -- fixed tamper oracle, no credential output
                [
                    str(self.age),
                    "--decrypt",
                    "--identity",
                    str(self.identity),
                    "-o",
                    os.devnull,
                    str(corrupted),
                ],
                capture_output=True,
                timeout=10,
                check=False,
            )
            require(result.returncode != 0, "age authenticated tamper rejection")
            results = [
                self.restore("atp_restore_" + name, *snapshots[name], incident)
                for name in ("before_b", "before_creation")
            ]
            require(
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
                == "1",
                "remote effect survives",
            )
            requests = [
                json.loads(line)
                for line in (self.private / "physical-model.jsonl").read_text().splitlines()
            ]
            require(
                sum(row["kind"] == "request" for row in requests) == 2,
                "no replacement model requests",
            )
            invocations = (
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
            require(invocations == "1", "no blind report resend")
            return {
                "status": "PASS",
                "credential_isolation_seconds": isolation_seconds,
                "incident_at": incident.isoformat(),
                "restores": results,
                "model_physical_requests": 2,
                "demo_physical_writes": 1,
                "remote_effects": 1,
                "age_tamper_rejected": True,
                "off_host_delivery": "SKIPPED: no external destination or credentials configured",
                "live_provider_credentials": "N/A: synthetic private destinations only",
                "backups": [manifest for _, manifest in snapshots.values()],
            }
        finally:
            logs = self.compose("logs", "--no-color", "--tail", "40", "demo").decode(
                errors="replace"
            )
            for source in (self.private / "demo-key", self.root / ".secrets/demo_service_key"):
                value = source.read_text().strip()
                if value:
                    logs = logs.replace(value, "[REDACTED]")
            diagnostic = self.private / "demo-diagnostic.txt"
            diagnostic.write_text(logs)
            diagnostic.chmod(0o600)
            self.compose("down", "--volumes", "--remove-orphans")


if __name__ == "__main__":
    print(json.dumps(Drill().main(), sort_keys=True))
