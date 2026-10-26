"""Test-only subprocess crash and lost-ack oracle; never included in runtime image."""

import asyncio
import os
import sys
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from task_platform.config import load_settings
from task_platform.db import create_engine, sessions
from task_platform.execution import Executor
from task_platform.transport import SimulationTransport


def lose_ack(session: Session) -> None:
    # RELEASE SAVEPOINT also emits after_commit; only lose the durable outer COMMIT ack.
    if session.in_nested_transaction():
        return
    raise DBAPIError("COMMIT", None, ConnectionError("test lost acknowledgement"))


async def run(phase: str, url: str) -> None:
    settings = load_settings("worker")
    engine = create_engine(settings)
    control = create_engine(settings, control=True)
    executor = Executor(sessions(engine), sessions(control), settings.execution_epoch)
    lease = await executor.claim("crash-process")
    if lease is None:
        raise ValueError("Expected claim")
    if phase == "a":
        os._exit(77)
    if phase == "b_ack":
        event.listen(Session, "after_commit", lose_ack)
    try:
        permit = await executor.authorize(lease, uuid4(), b"{}")
    except DBAPIError:
        os._exit(77)
    if permit is None:
        raise ValueError("Expected permit")
    if phase == "b":
        os._exit(77)
    async with SimulationTransport(url) as transport:
        outcome = await transport.send(permit)
    if phase == "c_ack":
        event.listen(Session, "after_commit", lose_ack)
    try:
        await executor.complete(
            lease, permit.call_id, outcome.outcome, cost=outcome.cost, tokens=outcome.tokens
        )
    except DBAPIError:
        os._exit(77)
    os._exit(77)


if __name__ == "__main__":
    asyncio.run(run(sys.argv[1], sys.argv[2]))
