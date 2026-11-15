"""Exercise near-limit admission against a retained, predominantly settled ledger."""

import asyncio
from typing import Any
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.execution import Executor
from task_platform.identity import issue_key, provision_principal

from .conftest import settings_for
from .test_commands import create
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch

pytestmark = pytest.mark.integration


def call_rows_examined(node: dict[str, Any]) -> float:
    count = 0.0
    if node.get("Relation Name") in {"external_calls", "call_costs"}:
        count = (
            node["Actual Rows"]
            + node.get("Rows Removed by Filter", 0)
            + node.get("Rows Removed by Index Recheck", 0)
        ) * node["Actual Loops"]
    return count + sum(call_rows_examined(child) for child in node.get("Plans", []))


async def test_concurrent_global_admission_avoids_resolved_old_history(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    historical = await create(client, command_owner, "historical")
    history_id = UUID(str(historical["task_id"]))
    cancelled = await client.post(
        f"/v1/tasks/{history_id}/cancel",
        json={"expected_task_version": 1},
        headers={**command_owner, "Idempotency-Key": "historical-cancel"},
    )
    assert cancelled.status_code == 200

    migrator = create_engine(settings_for("migrator"))
    worker = create_engine(settings_for("worker"))
    selects: list[tuple[str, Any]] = []

    @event.listens_for(worker.sync_engine, "before_cursor_execute")
    def collect(
        _conn: Any, _cursor: Any, statement: str, params: Any, _context: Any, _many: bool
    ) -> None:
        accounting_sum = "sum(" in statement and "exposure_cost" in statement
        active_count = "count(" in statement and "external_calls.local_active" in statement
        if statement.startswith("SELECT") and (accounting_sum or active_count):
            selects.append((statement, params))

    try:
        async with migrator.begin() as connection:
            await connection.execute(
                text("""
                INSERT INTO attempts(id,task_id,step_id,attempt_number,generation,state)
                SELECT gen_random_uuid(),s.task_id,s.id,n,n,'succeeded'
                FROM steps s CROSS JOIN generate_series(1,12000) n
                WHERE s.task_id=:task
                """),
                {"task": history_id},
            )
            await connection.execute(
                text("""
                INSERT INTO external_calls(
                    id,task_id,principal_id,step_id,attempt_id,generation,execution_epoch,
                    principal_version,key_version,state,payload,payload_hash,provider,
                    authorized_at,local_active)
                SELECT gen_random_uuid(),t.id,t.principal_id,a.step_id,a.id,a.generation,
                    t.execution_epoch,1,1,'succeeded',convert_to('{}','UTF8'),repeat('a',64),'simulator-v1',
                    clock_timestamp()-interval '2 days',false
                FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE t.id=:task
                """),
                {"task": history_id},
            )
            await connection.execute(
                text("""
                INSERT INTO call_costs(
                    call_id,reserved_cost,reserved_tokens,confirmed_cost,confirmed_tokens,
                    exposure_cost,exposure_tokens,tariff,tokenizer)
                SELECT id,20,20,20,20,0,0,'simulated-micro-usd-v1','simulated-bytes-v1'
                FROM external_calls WHERE task_id=:task
                """),
                {"task": history_id},
            )
            await connection.execute(
                text("""
                UPDATE call_costs SET exposure_cost=10,exposure_tokens=1
                WHERE call_id=(SELECT id FROM external_calls WHERE task_id=:task LIMIT 1)
                """),
                {"task": history_id},
            )
        async with migrator.begin() as connection:
            for statement in ("ANALYZE external_calls", "ANALYZE call_costs", "ANALYZE principals"):
                await connection.execute(text(statement))

        async with admin_engine.begin() as connection:
            await connection.execute(
                text(
                    "UPDATE provider_admission SET global_daily_cost=4014, global_daily_tokens=2003"
                )
            )
        for index in range(2):
            principal = await provision_principal(
                sessions(admin_engine), f"History contender {index}"
            )
            _, token = await issue_key(sessions(admin_engine), principal)
            await create(
                client,
                {**command_owner, "Authorization": f"Bearer {token}"},
                f"contender-{index}",
            )
        first = await executor.claim("contender-a")
        second = await executor.claim("contender-b")
        assert first and second
        contender = Executor(sessions(worker), executor.control, executor.epoch)
        permits = await asyncio.gather(
            contender.authorize(first, UUID(int=1), b"{}"),
            contender.authorize(second, UUID(int=2), b"{}"),
        )
        assert sum(permit is not None for permit in permits) == 1
        async with worker.connect() as connection:
            assert any("sum(" in statement for statement, _ in selects)
            assert any("count(" in statement for statement, _ in selects)
            await connection.execute(text("SET LOCAL plan_cache_mode = force_generic_plan"))
            for statement, params in selects:
                plan = await connection.exec_driver_sql(
                    f"EXPLAIN (ANALYZE, FORMAT JSON) {statement}", params
                )
                examined = call_rows_examined(plan.scalar_one()[0]["Plan"])
                assert examined < 1000, (statement, examined)
        async with admin_engine.connect() as connection:
            assert (
                await connection.scalar(text("SELECT sum(exposure_cost) FROM call_costs")) == 4014
            )
    finally:
        await worker.dispose()
        await migrator.dispose()
