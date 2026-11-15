"""Pinned remediation for the confirmed certifi advisory, not a live audit substitute."""

import json
import tomllib
from pathlib import Path


def test_locked_certifi_excludes_ghsa_6q48_6c7w_9gw2() -> None:
    lock = tomllib.loads(Path("uv.lock").read_text())
    package = next(p for p in lock["package"] if p["name"] == "certifi")
    assert tuple(map(int, package["version"].split("."))) >= (2026, 10, 18)


def test_redis_image_uses_a_patched_multiarch_manifest() -> None:
    compose = Path("compose.yaml").read_text()
    assert "redis:8.2.11-alpine@sha256:" in compose
    manifest = json.loads(Path("docs/compatibility.json").read_text())
    assert manifest["redis_server"] == "8.2.11"
