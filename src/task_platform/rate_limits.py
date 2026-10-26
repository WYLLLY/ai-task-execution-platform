"""Bounded single-process read/control limits and ephemeral Redis creation limiting."""

import asyncio
import time
from collections import OrderedDict

from redis.asyncio import Redis
from redis.exceptions import RedisError

from task_platform.command_schemas import CommandError


class LocalLimits:
    def __init__(self) -> None:
        self.windows: OrderedDict[str, tuple[float, int]] = OrderedDict()

    def check(self, key: str, limit: int) -> None:
        now = time.monotonic()
        while self.windows and next(iter(self.windows.values()))[0] + 60 <= now:
            self.windows.popitem(last=False)
        start, count = self.windows.get(key, (now, 0))
        if count >= limit or (key not in self.windows and len(self.windows) >= 10000):
            raise CommandError(429, "Rate limit exceeded")
        self.windows[key] = (start, count + 1)


async def creation_limit(redis: Redis, key: str) -> None:
    script = (
        "local n=redis.call('INCR',KEYS[1]); if n==1 then "
        "redis.call('PEXPIRE',KEYS[1],60000) end; return n"
    )
    try:
        async with asyncio.timeout(2):
            result = await redis.eval(script, 1, f"creation:{key}")
            if int(result) > 60:
                raise CommandError(429, "Rate limit exceeded")
    except RedisError, OSError, TimeoutError:
        raise CommandError(503, "Submission unavailable") from None
