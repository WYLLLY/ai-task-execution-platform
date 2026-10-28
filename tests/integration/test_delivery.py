"""F22 manual close/drain/preflight/reopen; failures never reopen dispatch."""

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.delivery import delivery_phase
from task_platform.execution import Executor

from .conftest import settings_for
from .test_commands import create
from .test_execution import executor as executor

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("role", ["api", "worker"])
@pytest.mark.parametrize("grant", ["UPDATE(state) ON steps", "SELECT ON tasks"])
async def test_delivery_refuses_missing_runtime_privilege_even_with_current_revision(
    command_owner: dict[str, str], admin_engine: AsyncEngine, role: str, grant: str
) -> None:
    factory = sessions(admin_engine)
    settings = settings_for("admin")
    await delivery_phase(factory, settings, "close")
    migrator = create_engine(settings_for("migrator"))
    try:
        async with migrator.begin() as c:
            await c.execute(text(f"REVOKE {grant} FROM atp_{role}"))
        result = await delivery_phase(factory, settings, "preflight")
        assert result["schema_compatible"] is True
        assert result["ready"] is False
        with pytest.raises(ValueError, match="preflight"):
            await delivery_phase(factory, settings, "reopen")
        async with admin_engine.connect() as c:
            assert not await c.scalar(
                text("SELECT dispatch_open OR admission_open FROM platform_control")
            )
    finally:
        async with migrator.begin() as c:
            await c.execute(text(f"GRANT {grant} TO atp_{role}"))
        await migrator.dispose()


async def test_delivery_closes_b_before_draining_and_requires_compatible_preflight(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    await create(client, command_owner)
    lease = await executor.claim("drain-worker")
    assert lease
    factory = sessions(admin_engine)
    settings = settings_for("admin")
    async with admin_engine.begin() as c:
        await c.execute(text("UPDATE platform_control SET admission_open=true,dispatch_open=true"))
    assert (await delivery_phase(factory, settings, "close"))["ready"] is True
    async with admin_engine.connect() as c:
        assert not await c.scalar(
            text("SELECT admission_open OR dispatch_open FROM platform_control")
        )
    assert (await delivery_phase(factory, settings, "drain"))["ready"] is False
    assert (await delivery_phase(factory, settings, "preflight"))["ready"] is False
    with pytest.raises(ValueError):
        await delivery_phase(factory, settings, "reopen")
    await executor.maintenance()
    # No HTTP is sent; cancel is the operator's explicit local abandonment for this drill.
    snapshot = (await client.get(f"/v1/tasks/{lease.task_id}", headers=command_owner)).json()
    assert (
        await client.post(
            f"/v1/tasks/{lease.task_id}/cancel",
            headers={**command_owner, "Idempotency-Key": "delivery-cancel"},
            json={"expected_task_version": snapshot["task_version"]},
        )
    ).status_code == 200
    assert (await delivery_phase(factory, settings, "drain"))["ready"] is True
    assert (await delivery_phase(factory, settings, "preflight"))["ready"] is True
    assert (await delivery_phase(factory, settings, "reopen"))["ready"] is True


async def test_failed_schema_or_handler_preflight_leaves_delivery_closed(
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
) -> None:
    factory = sessions(admin_engine)
    settings = settings_for("admin")
    await delivery_phase(factory, settings, "close")
    async with admin_engine.connect() as c:
        original_revision = await c.scalar(text("SELECT version_num FROM alembic_version"))
    engine = create_engine(settings_for("migrator"))
    try:
        async with engine.begin() as c:
            await c.execute(text("UPDATE alembic_version SET version_num='failed-migration'"))
        assert (await delivery_phase(factory, settings, "preflight"))["ready"] is False
        with pytest.raises(ValueError):
            await delivery_phase(factory, settings, "reopen")
        async with admin_engine.connect() as c:
            assert not await c.scalar(
                text("SELECT dispatch_open OR admission_open FROM platform_control")
            )
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE alembic_version SET version_num=:revision"),
                {"revision": original_revision},
            )
            await c.execute(
                text(
                    "INSERT INTO workflow_versions(name,version,definition,available) "
                    "VALUES('missing','1','{}',true)"
                )
            )
        assert (await delivery_phase(factory, settings, "preflight"))["ready"] is False
    finally:
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE alembic_version SET version_num=:revision"),
                {"revision": original_revision},
            )
            await c.execute(text("DELETE FROM workflow_versions WHERE name='missing'"))
        await engine.dispose()


async def test_preflight_includes_withdrawn_versions_still_pinned_by_nonterminal_tasks(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import task_platform.delivery as delivery

    await create(client, command_owner)
    factory = sessions(admin_engine)
    await delivery_phase(factory, settings_for("admin"), "close")
    engine = create_engine(settings_for("migrator"))
    try:
        async with admin_engine.begin() as c:
            await c.execute(text("UPDATE workflow_versions SET available=false"))
            await c.execute(
                text(
                    "INSERT INTO workflow_versions(name,version,definition,available) "
                    "VALUES('preflight-control','1','{}',true)"
                )
            )
        monkeypatch.setattr(
            delivery, "supported_workflow", lambda workflow: workflow.name == "preflight-control"
        )
        assert (await delivery_phase(factory, settings_for("admin"), "preflight"))["ready"] is False
        with pytest.raises(ValueError):
            await delivery_phase(factory, settings_for("admin"), "reopen")
    finally:
        async with engine.begin() as c:
            await c.execute(text("DELETE FROM workflow_versions WHERE name='preflight-control'"))
        await engine.dispose()
