"""Compose smoke oracle invoked while the real Redis container is stopped."""

import asyncio
import json
import sys

from task_platform.db import create_engine, sessions
from task_platform.execution import Executor
from tests.integration.conftest import settings_for


async def check(task_id: str) -> None:
    settings = settings_for("worker")
    engine, control = create_engine(settings), create_engine(settings, control=True)
    try:
        executor = Executor(sessions(engine), sessions(control), settings.execution_epoch)
        lease = await executor.claim("redis-outage-worker")
        if lease is None or str(lease.task_id) != task_id or not await executor.heartbeat(lease):
            raise ValueError("PostgreSQL polling or heartbeat failed without Redis")
        print(json.dumps({"claimed": True, "heartbeat": True}))
    finally:
        await engine.dispose()
        await control.dispose()


if __name__ == "__main__":
    asyncio.run(check(sys.argv[1]))
