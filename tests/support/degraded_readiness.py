"""Read-only recovery barrier for the isolated outage campaign."""

import asyncio
from collections.abc import Awaitable, Callable

import httpx
from redis.exceptions import RedisError


async def wait_for_recovery(
    ping: Callable[[], Awaitable[bool]], network: httpx.AsyncClient, *, seconds: float = 5
) -> None:
    async with asyncio.timeout(seconds):
        while True:
            try:
                if await ping():
                    response = await network.get("/health/ready")
                    if response.status_code == 200:
                        value = response.json()
                        if (
                            isinstance(value, dict)
                            and value.get("redis") is True
                            and value.get("submission_available") is True
                        ):
                            return
            except RedisError, OSError, httpx.HTTPError:
                pass
            await asyncio.sleep(0.1)
