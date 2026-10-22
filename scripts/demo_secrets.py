"""Add isolated demo credentials to an existing local setup, without overwrites."""

import os
import secrets
from pathlib import Path


def generate(root: Path) -> None:
    if not root.is_dir() or root.is_symlink():
        raise SystemExit("Existing private secrets directory required")
    names = ("demo_postgres_password", "demo_password", "demo_database_url", "demo_service_key")
    if any((root / name).exists() or (root / name).is_symlink() for name in names):
        raise SystemExit("Demo secrets already exist; refusing to overwrite")
    password = secrets.token_hex(32)
    values = (
        secrets.token_hex(32),
        password,
        f"postgresql+psycopg://atp_demo:{password}@demo-db:5432/demo",
        secrets.token_hex(32),
    )
    for name, value in zip(names, values, strict=True):
        descriptor = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o444)
        with os.fdopen(descriptor, "w") as output:
            output.write(value)


if __name__ == "__main__":
    generate(Path(".secrets"))
