import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.config import Role
from task_platform.db import create_engine

from .conftest import settings_for

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("role", ["api", "worker", "admin"])
@pytest.mark.parametrize(
    "statement",
    [
        "CREATE TABLE forbidden(id int)",
        "CREATE TEMP TABLE forbidden(id int)",
        "CREATE SCHEMA forbidden",
        "SET ROLE atp_migrator",
        "SELECT * FROM pg_authid",
        "DELETE FROM audit_events",
        "UPDATE audit_events SET action='key.revoked'",
        "UPDATE alembic_version SET version_num='forged'",
    ],
)
async def test_runtime_forbidden_operations(role: Role, statement: str) -> None:
    engine = create_engine(settings_for(role))
    try:
        async with engine.connect() as connection:
            with pytest.raises(DBAPIError):
                await connection.execute(text(statement))
    finally:
        await engine.dispose()


@pytest.mark.parametrize("role", ["api", "worker"])
async def test_runtime_cannot_provision_or_change_epoch(role: Role) -> None:
    engine = create_engine(settings_for(role))
    try:
        for statement in (
            "UPDATE principals SET active=false",
            "UPDATE api_keys SET revoked_at=now()",
            "UPDATE platform_control SET dispatch_open=true",
            "SELECT * FROM audit_events",
        ):
            async with engine.connect() as connection:
                with pytest.raises(DBAPIError):
                    await connection.execute(text(statement))
        async with engine.connect() as connection:
            for table in ("principals", "api_keys", "platform_control", "alembic_version"):
                await connection.execute(text(f"SELECT * FROM {table} LIMIT 1"))  # noqa: S608
            assert await connection.scalar(text("SHOW transaction_isolation")) == "read committed"
            assert await connection.scalar(text("SHOW statement_timeout")) == "3s"
            assert await connection.scalar(text("SHOW lock_timeout")) == "1s"
    finally:
        await engine.dispose()


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO platform_control(id,execution_epoch) VALUES(2,gen_random_uuid())",
        "INSERT INTO principals(id,name,permission_version) VALUES(gen_random_uuid(),'bad',0)",
        "INSERT INTO principals(id,name) VALUES(gen_random_uuid(),' ')",
        "INSERT INTO api_keys(id,principal_id,digest) "
        "VALUES(gen_random_uuid(),gen_random_uuid(),repeat('a',64))",
    ],
)
async def test_database_constraints(admin_engine: AsyncEngine, statement: str) -> None:
    async with admin_engine.begin() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(text(statement))


async def test_exact_foundation_tables_and_no_role_escalation(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as connection:
        tables = set(
            (
                await connection.execute(
                    text("SELECT tablename FROM pg_tables WHERE schemaname='public'")
                )
            ).scalars()
        )
        assert tables == {
            "alembic_version",
            "principals",
            "api_keys",
            "platform_control",
            "audit_events",
            "workflow_versions",
            "tasks",
            "steps",
            "attempts",
            "task_events",
            "task_tombstones",
            "idempotency_records",
            "external_calls",
            "call_costs",
            "cost_evidence",
            "accounting_decisions",
            "restore_decisions",
            "provider_admission",
            "documents",
        }
        rows = (
            await connection.execute(
                text(
                    "SELECT rolname,rolsuper,rolcreaterole,rolcreatedb,rolbypassrls "
                    "FROM pg_roles WHERE rolname LIKE 'atp_%'"
                )
            )
        ).all()
        assert len(rows) == 4
        assert all(not any(row[1:]) for row in rows)
