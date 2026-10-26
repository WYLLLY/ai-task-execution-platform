"""Administrative F14/F15 evidence, revisions and overrun acknowledgement."""

import asyncio
from typing import cast
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import Session
from sqlalchemy.pool import QueuePool

from task_platform.db import sessions
from task_platform.execution import Executor
from task_platform.provider_admin import reconcile

from .test_commands import create
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch

pytestmark = pytest.mark.integration


def evidence(**changes: object) -> dict[str, object]:
    return {
        "kind": "settlement",
        "evidence_id": "invoice-1",
        "revision": 0,
        "source": "synthetic invoice",
        "proof_sha256": "a" * 64,
        "reason": "Reconcile synthetic observed usage",
        "cost": 20,
        "tokens": 10,
        "final": True,
        "estimated": False,
        **changes,
    }


async def pending(client: AsyncClient, headers: dict[str, str], executor: Executor) -> str:
    await create(client, headers)
    lease = await executor.claim("unknown-worker")
    assert lease
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    assert await executor.complete(lease, call, "unknown")
    return str(call)


async def test_concurrent_repeated_evidence_settles_once_and_audits(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    call = await pending(client, command_owner, executor)
    factory = sessions(admin_engine)
    assert all(await asyncio.gather(*(reconcile(factory, call, evidence()) for _ in range(4))))
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM accounting_decisions")) == 1
        assert await c.scalar(text("SELECT count(*) FROM cost_evidence")) == 1
        assert (
            await c.execute(text("SELECT confirmed_cost,exposure_cost,revision FROM call_costs"))
        ).one() == (20, 0, 1)
        assert await c.scalar(text("SELECT state FROM tasks")) == "suspended"
    for changed in ({"cost": 21}, {"reason": "different"}, {"proof_sha256": "b" * 64}):
        with pytest.raises(ValueError, match="conflict"):
            await reconcile(factory, call, evidence(**changed))
    with pytest.raises(ValueError, match="Revision"):
        await reconcile(factory, call, evidence(evidence_id="new"))


async def test_partial_and_estimated_final_evidence_remain_distinct(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    call = await pending(client, command_owner, executor)
    factory = sessions(admin_engine)
    assert await reconcile(factory, call, evidence(final=False))
    async with admin_engine.connect() as c:
        assert (
            await c.execute(text("SELECT exposure_cost,confirmed_cost,revision FROM call_costs"))
        ).one() == (4004, 0, 0)
    assert await reconcile(factory, call, evidence(evidence_id="estimate-final", estimated=True))
    async with admin_engine.connect() as c:
        assert (
            await c.scalar(
                text(
                    "SELECT details->>'estimated' FROM accounting_decisions "
                    "WHERE evidence_id='estimate-final'"
                )
            )
            == "true"
        )
        assert await c.scalar(text("SELECT count(*) FROM accounting_decisions")) == 2


async def test_overrun_requires_explicit_current_revision_review(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    call = await pending(client, command_owner, executor)
    factory = sessions(admin_engine)
    assert await reconcile(factory, call, evidence(cost=9999, tokens=9999))
    await create(client, command_owner, key="blocked")
    lease = await executor.claim("blocked")
    assert lease and await executor.authorize(lease, uuid4(), b"{}") is None
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT overrun FROM call_costs")) is True
        assert await c.scalar(text("SELECT count(*) FROM tasks WHERE reason='overrun_review'")) == 1
    ack = {
        "kind": "acknowledgement",
        "evidence_id": "review-1",
        "revision": 1,
        "source": "operator",
        "proof_sha256": "b" * 64,
        "reason": "Reviewed invoice",
        "tariff_review": "Pinned tariff checked",
        "estimator_review": "Margin reviewed",
    }
    assert await reconcile(factory, call, ack)
    assert await reconcile(factory, call, ack)
    await create(client, command_owner, key="after-ack")
    lease = await executor.claim("after-ack")
    assert lease and await executor.authorize(lease, uuid4(), b"{}")
    assert await reconcile(factory, call, evidence(evidence_id="corrected", revision=1, cost=12000))
    await create(client, command_owner, key="blocked-again")
    lease = await executor.claim("blocked-again")
    assert lease and await executor.authorize(lease, uuid4(), b"{}") is None


async def test_lost_commit_ack_replay_is_stable(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
) -> None:
    call = await pending(client, command_owner, executor)

    def lose_ack(session: Session) -> None:
        raise RuntimeError("lost acknowledgement after commit")

    event.listen(Session, "after_commit", lose_ack)
    try:
        with pytest.raises(RuntimeError, match="lost acknowledgement"):
            await reconcile(sessions(admin_engine), call, evidence())
    finally:
        event.remove(Session, "after_commit", lose_ack)
    assert cast(QueuePool, admin_engine.sync_engine.pool).checkedout() == 0
    assert await reconcile(sessions(admin_engine), call, evidence())
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM accounting_decisions")) == 1
        assert await c.scalar(text("SELECT revision FROM call_costs")) == 1
