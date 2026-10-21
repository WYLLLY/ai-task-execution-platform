"""M3 schema acceptance, exercised through actual PostgreSQL roles."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

pytestmark = pytest.mark.integration


@pytest.mark.parametrize(
    "table", ["external_calls", "call_costs", "cost_evidence", "provider_admission"]
)
async def test_execution_schema_exists(admin_engine: AsyncEngine, table: str) -> None:
    async with admin_engine.connect() as connection:
        assert await connection.scalar(text("SELECT to_regclass(:name)"), {"name": table}) == table


@pytest.mark.parametrize("column", ["available_at", "selected_call_id", "result", "failures"])
async def test_execution_checkpoint_columns(admin_engine: AsyncEngine, column: str) -> None:
    async with admin_engine.connect() as connection:
        assert (
            await connection.scalar(
                text(
                    "SELECT count(*) FROM information_schema.columns "
                    "WHERE table_name='steps' AND column_name=:name"
                ),
                {"name": column},
            )
            == 1
        )


@pytest.mark.parametrize(
    "name", ["call_arcs", "immutable_cost_identity", "immutable_cost_evidence"]
)
async def test_execution_immutability_triggers(admin_engine: AsyncEngine, name: str) -> None:
    async with admin_engine.connect() as connection:
        assert (
            await connection.scalar(
                text("SELECT count(*) FROM pg_trigger WHERE tgname=:name"), {"name": name}
            )
            == 1
        )


@pytest.mark.parametrize("role,allowed", [("atp_worker", True), ("atp_api", False)])
async def test_only_worker_can_insert_external_call(
    admin_engine: AsyncEngine, role: str, allowed: bool
) -> None:
    async with admin_engine.connect() as connection:
        table = await connection.scalar(text("SELECT to_regclass('external_calls')"))
        assert table is not None
        assert (
            await connection.scalar(
                text("SELECT has_table_privilege(:role, 'external_calls', 'INSERT')"),
                {"role": role},
            )
            is allowed
        )
