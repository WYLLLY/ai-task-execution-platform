"""Test-only hard process death immediately after a durable M4 unit commit."""

import asyncio
import os

from task_platform.cli import worker_run


async def crash() -> None:
    await worker_run(True, True)
    os._exit(91)


if __name__ == "__main__":
    asyncio.run(crash())
