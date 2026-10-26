"""Generate ignored local/CI-only credentials. Never overwrite an existing setup."""

import json
import os
import secrets
from pathlib import Path
from uuid import uuid4


def main() -> None:
    root = Path(".secrets")
    if root.exists() or Path(".env").exists():
        raise SystemExit(".secrets or .env already exists; preserve it or choose a fresh checkout")
    root.mkdir(mode=0o700)
    os.umask(0o077)
    for role in ("api", "worker", "admin", "migrator", "postgres"):
        password = secrets.token_hex(32)
        (root / f"{role}_password").write_text(password)
        if role != "postgres":
            (root / f"{role}_database_url").write_text(
                f"postgresql+psycopg://atp_{role}:{password}@postgres:5432/atp"
            )
    redis_password = secrets.token_hex(32)
    (root / "redis_password").write_text(redis_password)
    (root / "redis_url").write_text(f"redis://:{redis_password}@redis:6379/0")
    # Redis password stays in a read-only secret config, not the process command line.
    (root / "redis.conf").write_text(
        f"requirepass {redis_password}\nbind 0.0.0.0\nprotected-mode yes\n"
        'save ""\nappendonly no\nmaxmemory 64mb\nmaxmemory-policy allkeys-lru\n'
    )
    epoch = str(uuid4())
    Path(".env").write_text(f"EXECUTION_EPOCH={epoch}\n")
    (root / "metadata.json").write_text(json.dumps({"execution_epoch": epoch}))
    # Compose file-backed secrets retain source permissions; local containers have distinct UIDs.
    # The directory remains owner-only on the host; mounted files are readable in their recipient.
    for path in root.iterdir():
        path.chmod(0o444)
    print("Created local secrets and execution epoch; no credential values displayed.")


if __name__ == "__main__":
    main()
