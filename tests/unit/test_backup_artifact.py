"""Backup publication is atomic; failed encryption cannot look like a valid copy."""

import hashlib
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from scripts.backup_artifact import encrypted_backup, verify_backup


@pytest.fixture
def fake_age(tmp_path: Path) -> Callable[[str], list[str]]:
    # Execute the trusted interpreter, not a file from the noexec data directory.
    def command(code: str) -> list[str]:
        source = tmp_path / "age-fixture.py"
        source.write_text(code)
        return [sys.executable, str(source)]

    return command


def test_backup_streams_to_exclusive_ciphertext_and_manifest(
    tmp_path: Path, fake_age: Callable[[str], list[str]]
) -> None:
    age = fake_age("import sys\nsys.stdout.buffer.write(b'cipher:'+sys.stdin.buffer.read())\n")
    target = tmp_path / "backup.age"
    manifest = encrypted_backup(
        [sys.executable, "-c", "print('synthetic-secret')"],
        "age1" + "q" * 58,
        target,
        age_command=age,
    )
    assert target.exists(), "successful encrypted stream must publish its artifact"
    assert target.read_bytes() == b"cipher:synthetic-secret\n"
    assert target.stat().st_mode & 0o777 == 0o600
    assert manifest["sha256"] == hashlib.sha256(target.read_bytes()).hexdigest()
    assert verify_backup(target, manifest, now=datetime.now(UTC))
    assert "synthetic-secret" not in str(manifest)
    with pytest.raises(ValueError):
        encrypted_backup(
            [sys.executable, "-c", "print('different')"], "age1" + "q" * 58, target, age_command=age
        )
    assert target.read_bytes() == b"cipher:synthetic-secret\n"


@pytest.mark.parametrize("failure", ["dump", "encrypt", "timeout"])
def test_backup_never_publishes_failed_or_timed_out_stream(
    tmp_path: Path, failure: str, fake_age: Callable[[str], list[str]]
) -> None:
    age = fake_age(
        "import sys,time\n"
        + (
            "time.sleep(30)"
            if failure == "timeout"
            else "sys.exit(9)"
            if failure == "encrypt"
            else "sys.stdout.buffer.write(sys.stdin.buffer.read())"
        ),
    )
    target = tmp_path / "backup.age"
    dump = [sys.executable, "-c", "raise SystemExit(7)" if failure == "dump" else "print('secret')"]
    with pytest.raises(ValueError):
        encrypted_backup(dump, "age1" + "q" * 58, target, age_command=age, timeout=0.1)
    assert not target.exists()
    assert not list(tmp_path.glob(".backup-*"))


@pytest.mark.parametrize("case", ["old", "future", "size", "hash", "naive"])
def test_invalid_or_expired_backup_manifest_is_rejected(tmp_path: Path, case: str) -> None:
    target = tmp_path / "backup.age"
    target.write_bytes(b"encrypted")
    now = datetime.now(UTC)
    manifest: dict[str, object] = {
        "format": "postgres-custom+age-v1",
        "started_at": now.isoformat(),
        "completed_at": now.isoformat(),
        "bytes": target.stat().st_size,
        "sha256": hashlib.sha256(target.read_bytes()).hexdigest(),
    }
    if case == "old":
        manifest["started_at"] = (now - timedelta(days=7, seconds=1)).isoformat()
    elif case == "future":
        manifest["started_at"] = (now + timedelta(seconds=1)).isoformat()
    elif case == "size":
        manifest["bytes"] = True
    elif case == "naive":
        manifest["started_at"] = now.replace(tzinfo=None).isoformat()
    else:
        manifest["sha256"] = "0" * 64
    with pytest.raises(ValueError):
        verify_backup(target, manifest, now=now)
