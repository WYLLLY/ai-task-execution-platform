"""Permanent M1 audit regressions; real PostgreSQL and disposable test credentials only."""

import asyncio
import io
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.exc import TimeoutError as PoolTimeout
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.api import create_app
from task_platform.config import Role
from task_platform.db import create_engine, sessions
from task_platform.identity import (
    AuthenticationError,
    NotFoundError,
    authenticate,
    disable_principal,
    initialize_platform,
    issue_key,
    provision_principal,
    revoke_key,
)
from task_platform.models import APIKey, AuditEvent, PlatformControl, Principal
from task_platform.observability import JSONFormatter

from .conftest import settings_for

pytestmark = pytest.mark.integration


@asynccontextmanager
async def role_engine(role: Role) -> AsyncIterator[AsyncEngine]:
    engine = create_engine(settings_for(role))
    try:
        yield engine
    finally:
        await engine.dispose()


async def backend_pid(engine: AsyncEngine) -> int:
    async with engine.connect() as connection:
        value = await connection.scalar(text("SELECT pg_backend_pid()"))
        assert isinstance(value, int)
        return value


async def wait_for_blockers(observer: AsyncEngine, pids: list[int]) -> None:
    """Observe the specific contenders waiting in PostgreSQL, not an unrelated session."""
    async with asyncio.timeout(0.8), observer.connect() as connection:
        for pid in pids:
            while not await connection.scalar(  # noqa: ASYNC110 -- observe an external DB lock
                text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}
            ):
                await asyncio.sleep(0.005)


async def test_disable_committed_while_issuance_waits_prevents_key_creation(
    admin_engine: AsyncEngine,
) -> None:
    factory = sessions(admin_engine)
    principal_id = await provision_principal(factory, "M1 blocked issuance")
    async with role_engine("admin") as contender, role_engine("admin") as observer:
        pid = await backend_pid(contender)
        async with factory.begin() as holder:
            principal = await holder.scalar(
                select(Principal).where(Principal.id == principal_id).with_for_update()
            )
            assert principal is not None
            # A committed disable preceding the waiting issuer is the ordering under test.
            principal.active = False
            principal.permission_version += 1
            await holder.flush()
            issuance = asyncio.create_task(issue_key(sessions(contender), principal_id))
            try:
                await wait_for_blockers(observer, [pid])
                assert not issuance.done()
            except BaseException:
                issuance.cancel()
                await asyncio.gather(issuance, return_exceptions=True)
                raise
        with pytest.raises(NotFoundError):
            await issuance
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(APIKey).where(APIKey.principal_id == principal_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.principal_id == principal_id, AuditEvent.action == "key.created")
            )
            == 0
        )


async def test_issued_key_is_rejected_after_actual_disable_service(
    admin_engine: AsyncEngine,
) -> None:
    factory = sessions(admin_engine)
    principal_id = await provision_principal(factory, "M1 issue before disable")
    key_id, token = await issue_key(factory, principal_id)
    async with role_engine("api") as api:
        async with sessions(api)() as session:
            assert (await authenticate(session, token)).key_id == key_id
        await disable_principal(factory, principal_id)
        async with sessions(api)() as session:
            with pytest.raises(AuthenticationError):
                await authenticate(session, token)


async def test_two_waiting_revocations_commit_one_version_and_audit(
    admin_engine: AsyncEngine,
) -> None:
    factory = sessions(admin_engine)
    principal_id = await provision_principal(factory, "M1 simultaneous revocations")
    key_id, _ = await issue_key(factory, principal_id)
    async with (
        role_engine("admin") as first,
        role_engine("admin") as second,
        role_engine("admin") as observer,
    ):
        pids = [await backend_pid(engine) for engine in (first, second)]
        async with factory.begin() as holder:
            await holder.execute(
                select(Principal).where(Principal.id == principal_id).with_for_update()
            )
            contenders = [
                asyncio.create_task(revoke_key(sessions(engine), principal_id, key_id))
                for engine in (first, second)
            ]
            try:
                await wait_for_blockers(observer, pids)
                assert all(not task.done() for task in contenders)
            except BaseException:
                for task in contenders:
                    task.cancel()
                await asyncio.gather(*contenders, return_exceptions=True)
                raise
        await asyncio.gather(*contenders)
    async with factory() as session:
        key = await session.get(APIKey, key_id)
        assert key is not None and key.revoked_at is not None and key.permission_version == 2
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.subject_id == key_id, AuditEvent.action == "key.revoked")
            )
            == 1
        )


async def test_exhausted_read_pool_preserves_independent_control_connection() -> None:
    settings = settings_for("api")
    reads, control = create_engine(settings), create_engine(settings, control=True)
    started = asyncio.Event()

    async def exhausted_checkout() -> float:
        start = time.monotonic()
        started.set()
        with pytest.raises(PoolTimeout):
            async with reads.connect():
                pytest.fail("Read pool exceeded its configured capacity")
        return time.monotonic() - start

    try:
        async with AsyncExitStack() as stack:
            for _ in range(settings.read_pool_size):
                await stack.enter_async_context(reads.connect())
            waiting = asyncio.create_task(exhausted_checkout())
            try:
                await started.wait()
                assert not waiting.done()
                async with asyncio.timeout(1), control.connect() as connection:
                    assert await connection.scalar(text("SELECT 1")) == 1
                async with asyncio.timeout(4):
                    assert await waiting >= 1.8
            finally:
                if not waiting.done():
                    waiting.cancel()
                await asyncio.gather(waiting, return_exceptions=True)
    finally:
        await reads.dispose()
        await control.dispose()


@pytest.mark.parametrize("role", ["api", "worker", "admin"])
@pytest.mark.parametrize(
    "statement",
    [
        "TRUNCATE audit_events",
        "ALTER TABLE principals ADD COLUMN forbidden_review_column text",
        "DROP TABLE api_keys",
        "GRANT atp_admin TO atp_api",
        "CREATE ROLE forbidden_review_role LOGIN",
        "SELECT pg_read_file('/etc/passwd')",
        "COPY (SELECT 1) TO PROGRAM 'true'",
        "ALTER SYSTEM SET log_statement='all'",
        "SET ROLE postgres",
    ],
)
async def test_extended_runtime_privileges_are_denied_with_permission_sqlstate(
    role: Role, statement: str
) -> None:
    async with role_engine(role) as engine, engine.connect() as connection:
        if statement.startswith("ALTER SYSTEM"):
            connection = await connection.execution_options(isolation_level="AUTOCOMMIT")
        with pytest.raises(DBAPIError) as error:
            await connection.execute(text(statement))
        assert getattr(error.value.orig, "sqlstate", None) == "42501"


@asynccontextmanager
async def audit_fault(migrator: AsyncEngine) -> AsyncIterator[None]:
    """Inject a database rejection after real services have started their transaction."""
    async with migrator.begin() as connection:
        await connection.execute(
            text("""
            CREATE FUNCTION test_m1_reject_audit() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                RAISE EXCEPTION 'Synthetic M1 audit insert failure' USING ERRCODE = 'P0001';
            END $$;
            CREATE TRIGGER test_m1_reject_audit BEFORE INSERT ON audit_events
            FOR EACH ROW EXECUTE FUNCTION test_m1_reject_audit();
        """)
        )
    try:
        yield
    finally:
        async with migrator.begin() as connection:
            await connection.execute(
                text("""
                DROP TRIGGER test_m1_reject_audit ON audit_events;
                DROP FUNCTION test_m1_reject_audit();
            """)
            )


@pytest.mark.parametrize("action", ["principal-create", "key-create", "key-revoke", "disable"])
async def test_real_identity_service_rolls_back_when_audit_insert_fails(
    admin_engine: AsyncEngine, action: str
) -> None:
    factory = sessions(admin_engine)
    principal_id = await provision_principal(factory, "M1 rollback fixture")
    key_id, token = await issue_key(factory, principal_id)
    rejected_name = f"M1 rejected principal {uuid4()}"
    async with factory() as session:
        audit_count = await session.scalar(select(func.count()).select_from(AuditEvent))
        key_count = await session.scalar(
            select(func.count()).select_from(APIKey).where(APIKey.principal_id == principal_id)
        )
    async with role_engine("migrator") as migrator, audit_fault(migrator):
        with pytest.raises(DBAPIError) as error:
            match action:
                case "principal-create":
                    await provision_principal(factory, rejected_name)
                case "key-create":
                    await issue_key(factory, principal_id)
                case "key-revoke":
                    await revoke_key(factory, principal_id, key_id)
                case "disable":
                    await disable_principal(factory, principal_id)
                case _:
                    pytest.fail("Unknown fixed test scenario")
        assert getattr(error.value.orig, "sqlstate", None) == "P0001"
    async with factory() as session:
        principal = await session.get(Principal, principal_id)
        key = await session.get(APIKey, key_id)
        assert principal is not None and principal.active and principal.permission_version == 1
        assert key is not None and key.revoked_at is None and key.permission_version == 1
        assert await session.scalar(select(func.count()).select_from(AuditEvent)) == audit_count
        assert (
            await session.scalar(
                select(func.count()).select_from(APIKey).where(APIKey.principal_id == principal_id)
            )
            == key_count
        )
        assert (
            await session.scalar(
                select(func.count()).select_from(Principal).where(Principal.name == rejected_name)
            )
            == 0
        )
        assert (await authenticate(session, token)).key_id == key_id


async def test_committed_identity_and_revocation_survive_abrupt_process_exit(
    admin_engine: AsyncEngine,
) -> None:
    source = """
import asyncio, json, os
from task_platform.db import create_engine, sessions
from task_platform.identity import provision_principal, issue_key, revoke_key
from tests.integration.conftest import settings_for

async def main():
    engine = create_engine(settings_for('admin'))
    factory = sessions(engine)
    principal = await provision_principal(factory, 'M1 committed crash')
    key, token = await issue_key(factory, principal)
    await revoke_key(factory, principal, key)
    print(json.dumps({'principal': str(principal), 'key': str(key)}), flush=True)
    os._exit(17)

asyncio.run(main())
"""
    process = await asyncio.create_subprocess_exec(
        sys.executable, "-c", source, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    try:
        async with asyncio.timeout(15):
            stdout, _ = await process.communicate()
        assert process.returncode == 17
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()
    identities = json.loads(stdout)
    principal_id, key_id = UUID(identities["principal"]), UUID(identities["key"])
    async with sessions(admin_engine)() as session:
        principal = await session.get(Principal, principal_id)
        key = await session.get(APIKey, key_id)
        assert principal is not None and principal.active
        assert key is not None and key.revoked_at is not None and key.permission_version == 2
        assert set(
            (
                await session.execute(
                    select(AuditEvent.action).where(AuditEvent.principal_id == principal_id)
                )
            ).scalars()
        ) == {"principal.created", "key.created", "key.revoked"}


async def test_uncommitted_identity_rolls_back_after_process_is_killed(
    admin_engine: AsyncEngine,
) -> None:
    # The audit trigger waits on a parent-held DB lock after provision_principal has flushed.
    # Killing the child at this observed DB boundary tests real process/connection loss.
    application_name = f"m1_crash_{uuid4().hex}"
    principal_name = f"M1 uncommitted crash {uuid4()}"
    source = """
import asyncio, json, sys
from sqlalchemy import text
from task_platform.db import create_engine, sessions
from task_platform.identity import provision_principal
from tests.integration.conftest import settings_for

async def main():
    engine = create_engine(settings_for('admin'))
    async with engine.connect() as connection:
        await connection.execute(text("SELECT set_config('application_name', :name, false)"),
                                 {'name': sys.argv[1]})
        await connection.commit()
        print(json.dumps({'pid': await connection.scalar(text('SELECT pg_backend_pid()'))}),
              flush=True)
    await provision_principal(sessions(engine), sys.argv[2])
    raise AssertionError('Service completed before the crash boundary')

asyncio.run(main())
"""
    async with role_engine("migrator") as migrator:
        async with migrator.begin() as connection:
            await connection.execute(
                text("""
                CREATE FUNCTION test_m1_pause_audit() RETURNS trigger LANGUAGE plpgsql AS $$
                BEGIN
                    PERFORM pg_advisory_xact_lock(746190032);
                    RETURN NEW;
                END $$;
                CREATE TRIGGER test_m1_pause_audit BEFORE INSERT ON audit_events
                FOR EACH ROW EXECUTE FUNCTION test_m1_pause_audit();
            """)
            )
        try:
            async with admin_engine.connect() as holder:
                await holder.execute(text("SELECT pg_advisory_lock(746190032)"))
                try:
                    process = await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-c",
                        source,
                        application_name,
                        principal_name,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    try:
                        assert process.stdout is not None
                        async with asyncio.timeout(10):
                            pid = json.loads(await process.stdout.readline())["pid"]
                        await wait_for_blockers(migrator, [pid])
                        assert process.returncode is None
                        process.kill()
                        async with asyncio.timeout(5):
                            await process.wait()
                        assert process.returncode == -9
                    finally:
                        if process.returncode is None:
                            process.kill()
                            await process.wait()
                finally:
                    await holder.execute(text("SELECT pg_advisory_unlock(746190032)"))
            async with asyncio.timeout(5), migrator.connect() as observer:
                while await observer.scalar(  # noqa: ASYNC110 -- wait for dead backend cleanup
                    text("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE pid=:pid)"),
                    {"pid": pid},
                ):
                    await asyncio.sleep(0.01)
            async with sessions(admin_engine)() as session:
                assert (
                    await session.scalar(
                        select(func.count())
                        .select_from(Principal)
                        .where(Principal.name == principal_name)
                    )
                    == 0
                )
                # The FK and absent principal prevent an orphan audit after the crash.
                assert (
                    await session.scalar(
                        text(
                            "SELECT count(*) FROM audit_events a LEFT JOIN principals p "
                            "ON p.id=a.principal_id WHERE a.principal_id IS NOT NULL "
                            "AND p.id IS NULL"
                        )
                    )
                    == 0
                )
        finally:
            async with migrator.begin() as connection:
                await connection.execute(
                    text("""
                    DROP TRIGGER test_m1_pause_audit ON audit_events;
                    DROP FUNCTION test_m1_pause_audit();
                """)
                )


async def test_epoch_initialization_is_idempotent_and_rejects_mismatch(
    admin_engine: AsyncEngine,
) -> None:
    factory = sessions(admin_engine)
    expected_epoch = settings_for("admin").execution_epoch
    async with factory() as session:
        before = await session.scalar(
            select(func.count())
            .select_from(AuditEvent)
            .where(AuditEvent.action == "platform.initialized")
        )
        control = await session.get(PlatformControl, 1)
        assert control is not None
        original = (
            control.execution_epoch,
            control.version,
            control.dispatch_open,
            control.admission_open,
        )
    await initialize_platform(factory, expected_epoch)
    await initialize_platform(factory, expected_epoch)
    with pytest.raises(ValueError, match="Existing epoch differs"):
        await initialize_platform(factory, uuid4())
    async with factory() as session:
        control = await session.get(PlatformControl, 1)
        assert control is not None
        assert (
            control.execution_epoch,
            control.version,
            control.dispatch_open,
            control.admission_open,
        ) == original
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.action == "platform.initialized")
            )
            == before
        )


async def test_future_expiry_authenticates_and_database_expiry_boundary_rejects(
    admin_engine: AsyncEngine,
    client: AsyncClient,
) -> None:
    factory = sessions(admin_engine)
    principal_id = await provision_principal(factory, "M1 future expiry")
    async with factory() as session:
        database_now = await session.scalar(select(func.clock_timestamp()))
        assert isinstance(database_now, datetime)
    key_id, token = await issue_key(factory, principal_id, database_now + timedelta(hours=1))
    headers = {"Authorization": f"Bearer {token}"}
    assert (await client.get("/v1/me", headers=headers)).status_code == 200
    async with factory.begin() as session:
        await session.execute(
            text("UPDATE api_keys SET expires_at=clock_timestamp() WHERE id=:id"), {"id": key_id}
        )
    assert (await client.get("/v1/me", headers=headers)).status_code == 401


async def test_naive_expiry_is_rejected_without_key_or_audit(admin_engine: AsyncEngine) -> None:
    factory = sessions(admin_engine)
    principal_id = await provision_principal(factory, "M1 naive expiry")
    with pytest.raises(ValueError, match="timezone-aware"):
        await issue_key(
            factory, principal_id, datetime.now(UTC).replace(tzinfo=None) + timedelta(days=1)
        )
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(APIKey).where(APIKey.principal_id == principal_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.principal_id == principal_id, AuditEvent.action == "key.created")
            )
            == 0
        )


async def test_expiry_is_checked_against_fresh_db_time_after_owner_lock(
    admin_engine: AsyncEngine,
) -> None:
    factory = sessions(admin_engine)
    principal_id = await provision_principal(factory, "M1 expiry during lock wait")
    async with role_engine("admin") as contender, role_engine("admin") as observer:
        pid = await backend_pid(contender)
        async with factory.begin() as holder:
            await holder.execute(
                select(Principal).where(Principal.id == principal_id).with_for_update()
            )
            now = await holder.scalar(select(func.clock_timestamp()))
            assert isinstance(now, datetime)
            expiry = now + timedelta(milliseconds=200)
            issuance = asyncio.create_task(issue_key(sessions(contender), principal_id, expiry))
            try:
                await wait_for_blockers(observer, [pid])
                async with asyncio.timeout(0.8), observer.connect() as connection:
                    while not await connection.scalar(  # noqa: ASYNC110 -- use DB clock as oracle
                        text("SELECT clock_timestamp() >= :expiry"), {"expiry": expiry}
                    ):
                        await asyncio.sleep(0.005)
                assert not issuance.done()
            except BaseException:
                issuance.cancel()
                await asyncio.gather(issuance, return_exceptions=True)
                raise
        with pytest.raises(ValueError, match="future"):
            await issuance
    async with factory() as session:
        assert (
            await session.scalar(
                select(func.count()).select_from(APIKey).where(APIKey.principal_id == principal_id)
            )
            == 0
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(AuditEvent)
                .where(AuditEvent.principal_id == principal_id, AuditEvent.action == "key.created")
            )
            == 0
        )


async def test_unhandled_http_errors_do_not_disclose_exception_or_request_canaries() -> None:
    canary = "M1_ADVERSARIAL_ERROR_CANARY"
    app = create_app(settings_for("api"))

    @app.get("/_review-only-error")
    async def injected_error() -> None:
        raise RuntimeError(canary)

    captured = io.StringIO()
    handler = logging.StreamHandler(captured)
    handler.setFormatter(JSONFormatter())
    logger = logging.getLogger("task_platform.api")
    original_handlers, original_level, original_propagate = (
        logger.handlers[:],
        logger.level,
        logger.propagate,
    )
    logger.handlers, logger.propagate = [handler], False
    logger.setLevel(logging.INFO)
    try:
        async with (
            app.router.lifespan_context(app),
            AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client,
        ):
            result = await client.get(
                "/_review-only-error?secret=" + canary,
                headers={"Authorization": "Bearer " + canary},
            )
        assert result.status_code == 500 and result.json() == {"detail": "Internal error"}
        assert canary not in result.text + captured.getvalue()
        assert "request.failed" in captured.getvalue()
    finally:
        logger.handlers, logger.propagate = original_handlers, original_propagate
        logger.setLevel(original_level)
        handler.close()


def test_cli_invalid_identifier_does_not_echo_canary() -> None:
    canary = "M1_ADVERSARIAL_CLI_CANARY"
    environment = os.environ.copy()
    environment.pop("DATABASE_URL", None)
    environment["DATABASE_URL_FILE"] = str(
        Path(environment["TEST_SECRETS_DIR"]) / "admin_database_url"
    )
    completed = subprocess.run(  # noqa: S603 -- fixed local CLI and synthetic test input
        [
            sys.executable,
            "-c",
            "from task_platform.cli import main; main()",
            "admin",
            "key-create",
            "--principal",
            canary,
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
        timeout=10,
    )
    assert completed.returncode == 1
    assert canary not in completed.stdout + completed.stderr
    assert "command.failed" in completed.stderr


@pytest.mark.parametrize("table", ["principals", "api_keys"])
async def test_readiness_rejects_missing_identity_privilege(
    client: AsyncClient,
    admin_engine: AsyncEngine,
    table: str,
) -> None:
    principal_id = await provision_principal(sessions(admin_engine), "M1 readiness privilege")
    _, token = await issue_key(sessions(admin_engine), principal_id)
    assert (await client.get("/health/ready")).status_code == 200
    async with role_engine("migrator") as migrator:
        async with migrator.begin() as connection:
            await connection.execute(text(f"REVOKE SELECT ON {table} FROM atp_api"))  # noqa: S608 -- fixed parametrized table names
        try:
            # Verify the injected failure actually makes the M1 workload unavailable.
            assert (
                await client.get("/v1/me", headers={"Authorization": f"Bearer {token}"})
            ).status_code == 503
            readiness = await client.get("/health/ready")
            assert readiness.status_code == 503
            assert readiness.json()["schema_compatible"] is False
        finally:
            async with migrator.begin() as connection:
                await connection.execute(text(f"GRANT SELECT ON {table} TO atp_api"))  # noqa: S608 -- fixed parametrized table names
    assert (await client.get("/health/ready")).status_code == 200
