"""M5 PostgreSQL schema/role contracts, before administrative implementation."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


async def test_private_accounting_decisions_exist(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as c:
        assert (
            await c.scalar(text("SELECT to_regclass('accounting_decisions')"))
            == "accounting_decisions"
        )


async def test_private_protocol_checkpoint_exists(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as c:
        assert (
            await c.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name='steps' AND column_name='protocol_items'"
                )
            )
            == 1
        )


@pytest.mark.parametrize(
    "role,insert,read",
    [("atp_admin", True, True), ("atp_worker", False, True), ("atp_api", False, False)],
)
async def test_accounting_decisions_privileges(
    admin_engine: AsyncEngine, role: str, insert: bool, read: bool
) -> None:
    async with admin_engine.connect() as c:
        exists = await c.scalar(text("SELECT to_regclass('accounting_decisions')"))
        assert exists is not None, "M5 audit relation is missing"
        assert (
            await c.scalar(
                text("SELECT has_table_privilege(:role, 'accounting_decisions','INSERT')"),
                {"role": role},
            )
            is insert
        )
        assert (
            await c.scalar(
                text("SELECT has_table_privilege(:role, 'accounting_decisions','SELECT')"),
                {"role": role},
            )
            is read
        )


async def test_admin_has_settlement_but_no_task_progression(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as c:
        assert (
            await c.scalar(
                text(
                    "SELECT has_column_privilege('atp_admin','call_costs',"
                    "'confirmed_cost','UPDATE')"
                )
            )
            is True
        )
        assert (
            await c.scalar(
                text("SELECT has_column_privilege('atp_admin','tasks','state','UPDATE')")
            )
            is False
        )


async def test_partial_observation_column_exists(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as c:
        assert (
            await c.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name='external_calls' AND column_name='provider_observation'"
                )
            )
            == 1
        )


@pytest.mark.parametrize(
    "privilege,table",
    [
        ("UPDATE(protocol_items)", "steps"),
        ("UPDATE(provider_observation)", "external_calls"),
        ("SELECT", "accounting_decisions"),
    ],
)
async def test_worker_readiness_checks_m5_required_privileges(privilege: str, table: str) -> None:
    from task_platform.db import create_engine
    from task_platform.health import check_database

    from .conftest import settings_for

    migrator = create_engine(settings_for("migrator"))
    worker = create_engine(settings_for("worker"))
    try:
        async with migrator.begin() as c:
            await c.execute(text(f"REVOKE {privilege} ON {table} FROM atp_worker"))  # noqa: S608 -- fixed parametrization
        ready, compatible, _ = await check_database(worker, settings_for("worker"))
        assert not ready or not compatible
    finally:
        async with migrator.begin() as c:
            await c.execute(text(f"GRANT {privilege} ON {table} TO atp_worker"))  # noqa: S608 -- fixed parametrization
        await worker.dispose()
        await migrator.dispose()


async def test_worker_readiness_tolerates_bounded_database_round_trip_latency() -> None:
    """Real queries with 80ms injected latency must fit the existing 4s health budget."""
    from sqlalchemy import event
    from sqlalchemy.engine import Connection, ExecutionContext
    from sqlalchemy.engine.interfaces import DBAPICursor

    from task_platform.db import create_engine
    from task_platform.health import check_database

    from .conftest import settings_for

    settings = settings_for("worker")
    worker = create_engine(settings)

    def latency(
        connection: Connection,
        cursor: DBAPICursor,
        statement: str,
        parameters: object,
        context: ExecutionContext,
        executemany: bool,
    ) -> None:
        # The actual role/schema queries still execute against PostgreSQL.
        cursor.execute("SELECT pg_sleep(0.08)")

    event.listen(worker.sync_engine, "before_cursor_execute", latency)
    try:
        assert await check_database(worker, settings) == (True, True, settings.execution_epoch)
    finally:
        event.remove(worker.sync_engine, "before_cursor_execute", latency)
        await worker.dispose()
