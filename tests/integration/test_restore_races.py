"""Independent connection contention proves disaster reserves serialize revisions."""

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import create_engine, sessions
from task_platform.recovery import begin_restore, revise_reserve

from .conftest import settings_for
from .test_restore import proof, reset_recovery

pytestmark = pytest.mark.integration


async def test_concurrent_reserve_revision_and_lost_ack_replay_are_one_decision(
    command_owner: dict[str, str], admin_engine: AsyncEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("DISPATCH_ENABLED", "false")
    epoch = uuid4()
    first, second = create_engine(settings_for("admin")), create_engine(settings_for("admin"))
    try:
        await begin_restore(sessions(admin_engine), epoch, proof())
        decisions = [
            {
                "evidence_id": f"concurrent-{i}",
                "proof_sha256": str(i) * 64,
                "revision": 0,
                "cost": 100 + i,
                "tokens": 200 + i,
                "owners": [],
                "reason": "Independent synthetic invoice bound",
            }
            for i in (1, 2)
        ]
        async with admin_engine.begin() as blocker:
            await blocker.execute(text("SELECT id FROM platform_control FOR UPDATE"))
            competing = [
                asyncio.create_task(revise_reserve(sessions(engine), epoch, decision))
                for engine, decision in zip((first, second), decisions, strict=True)
            ]
            async with asyncio.timeout(0.9), admin_engine.connect() as observer:
                await observer.execution_options(isolation_level="AUTOCOMMIT")
                for _ in range(100):
                    waiting = await observer.scalar(
                        text(
                            "SELECT count(*) FROM pg_stat_activity WHERE usename='atp_admin' "
                            "AND cardinality(pg_blocking_pids(pid))>0"
                        )
                    )
                    if waiting == 2:
                        break
                    await asyncio.sleep(0.005)
                else:
                    pytest.fail("two independent admin reserve transactions must contend")
        results = await asyncio.gather(*competing, return_exceptions=True)
        assert results.count(True) == 1
        assert sum(isinstance(value, ValueError) for value in results) == 1
        winner = decisions[results.index(True)]
        # This is the stable reread after a client loses the first commit ACK:
        # identical evidence returns the retained decision without another revision.
        assert await revise_reserve(sessions(first), epoch, winner)
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT reserve_revision FROM platform_control")) == 1
            assert (
                await c.scalar(text("SELECT count(*) FROM restore_decisions WHERE kind='reserve'"))
                == 1
            )
            assert (
                await c.scalar(text("SELECT reserve_cost FROM platform_control")) == winner["cost"]
            )
    finally:
        await reset_recovery(admin_engine, str(UUID(command_owner["X-Execution-Epoch"])))
        await first.dispose()
        await second.dispose()
