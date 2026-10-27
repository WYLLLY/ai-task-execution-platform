"""Streaming encrypted archives; plaintext is never written to an intermediate file.

The caller is an operator-owned script, never a model/API request. Dump commands
and the age binary are trusted deployment configuration. Production uses the
fixed pg_dump command; unit tests use synthetic subprocesses.
"""

import hashlib
import os
import re
import subprocess
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1048576), b""):
            digest.update(block)
    return digest.hexdigest()


def stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)


def encrypted_backup(
    dump_command: list[str],
    recipient: str,
    target: Path,
    *,
    age_command: str | list[str] = "age",
    timeout: float = 900,
) -> dict[str, object]:
    if (
        not dump_command
        or not re.fullmatch(r"age1[0-9a-z]{58}", recipient)
        or not target.name.endswith(".age")
        or target.exists()
        or timeout <= 0
    ):
        raise ValueError("Invalid backup destination, recipient or deadline")
    encryption_command = [age_command] if isinstance(age_command, str) else list(age_command)
    if not encryption_command:
        raise ValueError("Explicit encryption executable required")
    started = datetime.now(UTC)
    deadline = time.monotonic() + timeout
    descriptor, temporary = tempfile.mkstemp(prefix=".backup-", dir=target.parent)
    partial = Path(temporary)
    dump: subprocess.Popen[bytes] | None = None
    encrypt: subprocess.Popen[bytes] | None = None
    try:
        with os.fdopen(descriptor, "wb") as output, tempfile.TemporaryFile() as errors:
            dump = subprocess.Popen(dump_command, stdout=subprocess.PIPE, stderr=errors)  # noqa: S603
            if dump.stdout is None:
                raise ValueError("Backup pipe unavailable")
            encrypt = subprocess.Popen(  # noqa: S603 -- operator-owned binary, explicit recipient
                [*encryption_command, "--encrypt", "--recipient", recipient],
                stdin=dump.stdout,
                stdout=output,
                stderr=errors,
            )
            dump.stdout.close()
            encrypted = encrypt.wait(timeout=max(0.001, deadline - time.monotonic()))
            dumped = dump.wait(timeout=max(0.001, deadline - time.monotonic()))
            if dumped != 0 or encrypted != 0:
                raise ValueError("Backup or encryption failed; no artifact published")
            output.flush()
            os.fsync(output.fileno())
        manifest: dict[str, object] = {
            "format": "postgres-custom+age-v1",
            "started_at": started.isoformat(),
            "completed_at": datetime.now(UTC).isoformat(),
            "bytes": partial.stat().st_size,
            "sha256": checksum(partial),
        }
        # Atomic publication, refusing races with an existing destination.
        os.link(partial, target)
        directory = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
        return manifest
    except OSError, subprocess.TimeoutExpired:
        raise ValueError("Backup failed or exceeded its absolute deadline") from None
    finally:
        stop(encrypt)
        stop(dump)
        partial.unlink(missing_ok=True)


def verify_backup(target: Path, manifest: dict[str, object], *, now: datetime) -> bool:
    try:
        start = datetime.fromisoformat(str(manifest["started_at"]))
        complete = datetime.fromisoformat(str(manifest["completed_at"]))
        if now.tzinfo is None or start.tzinfo is None or complete.tzinfo is None:
            raise ValueError("Aware backup clocks required")
        if not start <= complete <= now or now - start > timedelta(days=7):
            raise ValueError("Expired or future backup point")
        if (
            manifest["format"] != "postgres-custom+age-v1"
            or type(manifest["bytes"]) is not int
            or not target.is_file()
            or manifest["bytes"] != target.stat().st_size
            or target.stat().st_size == 0
            or manifest["sha256"] != checksum(target)
        ):
            raise ValueError("Backup integrity mismatch")
    except KeyError, OSError, TypeError:
        raise ValueError("Invalid backup manifest") from None
    return True
