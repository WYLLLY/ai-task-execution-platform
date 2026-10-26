import asyncio
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.identity import (
    NotFoundError,
    disable_principal,
    issue_key,
    key_digest,
    provision_principal,
    revoke_key,
)
from task_platform.models import APIKey, AuditEvent, Principal

from .conftest import settings_for

pytestmark = pytest.mark.integration


async def test_identity_owner_isolation_and_revocation(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    factory = sessions(admin_engine)
    a = await provision_principal(factory, "Owner A")
    b = await provision_principal(factory, "Owner B")
    key_a, token_a = await issue_key(factory, a)
    key_b, token_b = await issue_key(factory, b)
    headers = {"Authorization": f"Bearer {token_a}"}
    me = await client.get("/v1/me", headers=headers)
    assert me.status_code == 200
    assert me.json() == {"principal_id": str(a), "key_id": str(key_a)}
    own = await client.get(f"/v1/api-keys/{key_a}", headers=headers)
    assert own.status_code == 200
    assert "digest" not in own.text and token_a not in own.text
    foreign = await client.get(f"/v1/api-keys/{key_b}", headers=headers)
    absent = await client.get(f"/v1/api-keys/{uuid4()}", headers=headers)
    assert foreign.status_code == absent.status_code == 404
    assert foreign.json() == absent.json()
    with pytest.raises(NotFoundError):
        await revoke_key(factory, a, key_b)
    assert (
        await client.get("/v1/me", headers={"Authorization": f"Bearer {token_b}"})
    ).status_code == 200
    await revoke_key(factory, a, key_a)
    assert (await client.get("/v1/me", headers=headers)).status_code == 401
    async with factory() as session:
        assert await session.scalar(select(APIKey.digest).where(APIKey.id == key_a)) == key_digest(
            token_a
        )
        assert (
            await session.scalar(select(APIKey.permission_version).where(APIKey.id == key_a)) == 2
        )


async def test_disable_principal_blocks_all_keys(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    factory = sessions(admin_engine)
    principal = await provision_principal(factory, "Disable")
    tokens = [(await issue_key(factory, principal))[1] for _ in range(2)]
    await disable_principal(factory, principal)
    for token in tokens:
        assert (
            await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
        ).status_code == 401
    with pytest.raises(NotFoundError):
        await issue_key(factory, principal)


async def test_expired_key_denied_using_db_clock(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    factory = sessions(admin_engine)
    principal = await provision_principal(factory, "Expiry")
    key_id, token = await issue_key(factory, principal)
    async with factory.begin() as session:
        await session.execute(
            text(
                "UPDATE api_keys SET created_at=now()-interval '2 days', "
                "expires_at=now()-interval '1 day' WHERE id=:id"
            ),
            {"id": key_id},
        )
    assert (
        await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
    ).status_code == 401
    with pytest.raises(ValueError):
        await issue_key(factory, principal, datetime.now(UTC) - timedelta(days=1))


@pytest.mark.parametrize(
    "authorization",
    ["", "Basic xxx", "Bearer invalid", "Bearer atp_' OR 1=1", "Bearer " + "x" * 10000],
)
async def test_invalid_auth_has_generic_response(client: AsyncClient, authorization: str) -> None:
    response = await client.get("/v1/me", headers={"Authorization": authorization})
    assert response.status_code == 401
    assert response.json() == {"detail": "Invalid credentials"}
    assert response.headers["cache-control"] == "no-store"


async def test_validation_does_not_echo_input(
    client: AsyncClient, admin_engine: AsyncEngine
) -> None:
    principal = await provision_principal(sessions(admin_engine), "Validation")
    _, token = await issue_key(sessions(admin_engine), principal)
    response = await client.get(
        "/v1/api-keys/SENSITIVE_CANARY", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 422
    assert "SENSITIVE_CANARY" not in response.text


async def test_revocations_wait_on_owner_lock_and_are_idempotent(admin_engine: AsyncEngine) -> None:
    factory = sessions(admin_engine)
    principal = await provision_principal(factory, "Contention")
    key_id, _ = await issue_key(factory, principal)
    # Observe actual PostgreSQL waiters before releasing the owner lock.
    async with factory.begin() as holder:
        await holder.execute(select(Principal).where(Principal.id == principal).with_for_update())
        first = asyncio.create_task(revoke_key(factory, principal, key_id))
        # Use a separate engine so observation does not consume the blocked service's pool.
        observer = create_engine(settings_for("admin"))
        try:
            async with asyncio.timeout(0.8), observer.connect() as connection:
                # Poll an external PostgreSQL lock; an in-process Event cannot observe it.
                while not await connection.scalar(  # noqa: ASYNC110
                    text(
                        "SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE usename='atp_admin' "
                        "AND cardinality(pg_blocking_pids(pid)) > 0)"
                    )
                ):
                    await asyncio.sleep(0.01)
            assert not first.done()
        finally:
            await observer.dispose()
    await first
    await revoke_key(factory, principal, key_id)
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.subject_id == key_id, AuditEvent.action == "key.revoked")
            )
            == 1
        )
        assert (
            await session.scalar(select(APIKey.permission_version).where(APIKey.id == key_id)) == 2
        )


async def test_failed_key_insert_rolls_back_audit(admin_engine: AsyncEngine) -> None:
    factory = sessions(admin_engine)
    principal = await provision_principal(factory, "Rollback")
    subject = uuid4()
    with pytest.raises(DBAPIError):
        async with factory.begin() as session:
            session.add(
                AuditEvent(
                    id=uuid4(), action="key.created", principal_id=principal, subject_id=subject
                )
            )
            session.add(APIKey(id=subject, principal_id=principal, digest="malformed"))
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(AuditEvent).where(AuditEvent.subject_id == subject)
            )
            == 0
        )


def test_admin_cli_provisions_and_revokes_without_logging_secret() -> None:
    import json

    environment = os.environ.copy()
    environment["DATABASE_URL_FILE"] = str(
        Path(environment["TEST_SECRETS_DIR"]) / "admin_database_url"
    )

    def run(*args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(  # noqa: S603 -- fixed local CLI and synthetic test arguments
            [sys.executable, "-c", "from task_platform.cli import main; main()", "admin", *args],
            env=environment,
            capture_output=True,
            text=True,
            check=True,
        )

    principal = UUID(
        json.loads(run("principal-create", "--name", "CLI owner").stdout)["principal_id"]
    )
    issued = run("key-create", "--principal", str(principal))
    result = json.loads(issued.stdout)
    assert result["api_key"] not in issued.stderr
    revoked = run("key-revoke", "--principal", str(principal), "--key", result["key_id"])
    assert json.loads(revoked.stdout) == {"revoked": True}
    assert result["api_key"] not in revoked.stderr
