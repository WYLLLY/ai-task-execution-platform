"""Bounded single-process SSE sessions; no database connection across writes or waits."""

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable
from typing import Protocol
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import Response
from starlette.types import Receive, Scope, Send

from task_platform.command_schemas import CommandError
from task_platform.event_cursor import HistoryExpired
from task_platform.event_reads import Batch, EventInvariantError
from task_platform.identity import AuthenticationError, Identity, NotFoundError


class Connections:
    def __init__(self) -> None:
        self.active = 0
        self.owners: dict[UUID, int] = {}

    def acquire(self, owner: UUID) -> Callable[[], None]:
        if self.active >= 100 or self.owners.get(owner, 0) >= 5:
            raise CommandError(429, "Stream limit exceeded")
        self.active += 1
        self.owners[owner] = self.owners.get(owner, 0) + 1
        released = False

        def release() -> None:
            nonlocal released
            if released:
                return
            released = True
            self.active -= 1
            self.owners[owner] -= 1
            if not self.owners[owner]:
                del self.owners[owner]

        return release


class AuthLease:
    def __init__(
        self, check: Callable[[], Awaitable[None]], *, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self.check = check
        self.clock = clock
        self.checked_at: float | None = None
        self.valid_until = 0.0

    async def ensure(self) -> None:
        now = self.clock()
        if self.checked_at is not None and now < self.checked_at + 5:
            if now >= self.valid_until:
                raise TimeoutError
            return
        remaining = self.valid_until - now if self.checked_at is not None else 10
        if remaining <= 0:
            raise TimeoutError
        started = now
        async with asyncio.timeout(min(2, remaining * 0.9)):
            await self.check()
        if self.clock() >= started + 10:
            raise TimeoutError
        # Never extend from completion time, and never renew on a failed check.
        self.checked_at, self.valid_until = started, started + 10


class EventSource(Protocol):
    async def batch(self, identity: Identity, task: UUID, position: int) -> Batch: ...
    async def wait(self, task: UUID, watermark: int, oldest: int, wait_seconds: float) -> None: ...


class Stream(Response):
    def __init__(
        self,
        task: UUID,
        identity: Identity,
        batch: Batch,
        auth: AuthLease,
        hub: EventSource | None,
        release: Callable[[], None],
        *,
        write_timeout: float = 5,
        lifetime: float = 300,
    ) -> None:
        super().__init__(
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-store",
                "X-Accel-Buffering": "no",
            },
        )
        del self.headers["content-length"]
        self.task, self.identity, self.initial, self.auth = task, identity, batch, auth
        self.hub, self.release = hub, release
        self.position = batch.position
        self.write_timeout, self.lifetime = write_timeout, lifetime

    async def _run(self, send: Send) -> None:
        started = time.monotonic()
        deadline = started + self.lifetime
        last_write = started

        async def write(body: bytes | None) -> None:
            nonlocal last_write
            await self.auth.ensure()
            bound = min(
                self.write_timeout,
                self.auth.valid_until - time.monotonic(),
                deadline - time.monotonic(),
            )
            if bound <= 0:
                raise TimeoutError
            async with asyncio.timeout(bound):
                if body is None:
                    await send(
                        {"type": "http.response.start", "status": 200, "headers": self.raw_headers}
                    )
                else:
                    await send({"type": "http.response.body", "body": body, "more_body": True})
            last_write = time.monotonic()

        await write(None)
        batch = self.initial
        while time.monotonic() < deadline:
            for item in batch.events:
                await write(item.wire)
                # A failed or timed-out send must never advance the position.
                self.position = item.sequence
            await self.auth.ensure()
            if self.position < batch.watermark and self.hub is not None:
                batch = await self.hub.batch(self.identity, self.task, self.position)
                continue
            now = time.monotonic()
            if now - last_write >= 10:
                await write(b": heartbeat\n\n")
            checked_at = self.auth.checked_at
            if checked_at is None:
                raise AuthenticationError
            wait = max(
                0.001,
                min(
                    checked_at + 5 - time.monotonic(),
                    last_write + 10 - time.monotonic(),
                    deadline - time.monotonic(),
                ),
            )
            if self.hub is None:
                await asyncio.sleep(wait)
            else:
                await self.hub.wait(self.task, batch.watermark, batch.oldest, wait)
                try:
                    batch = await self.hub.batch(self.identity, self.task, self.position)
                except HistoryExpired, NotFoundError, EventInvariantError:
                    await write(
                        b'event: stream.reset\ndata: {"snapshot_url":"/v1/tasks/'
                        + str(self.task).encode()
                        + b'"}\n\n'
                    )
                    return

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        async def disconnected() -> None:
            while True:
                if (await receive())["type"] == "http.disconnect":
                    return

        producer = asyncio.create_task(self._run(send))
        watcher = asyncio.create_task(disconnected())
        try:
            done, _ = await asyncio.wait((producer, watcher), return_when=asyncio.FIRST_COMPLETED)
            for task in done:
                await task
        except TimeoutError, OSError, AuthenticationError, SQLAlchemyError, NotFoundError:
            # Close without emitting an error body or reusing stale authorization.
            pass
        finally:
            for task in (producer, watcher):
                task.cancel()
            for task in (producer, watcher):
                with contextlib.suppress(asyncio.CancelledError):
                    if not task.done():
                        await task
            self.release()
        with contextlib.suppress(OSError, TimeoutError):
            async with asyncio.timeout(self.write_timeout):
                await send({"type": "http.response.body", "body": b"", "more_body": False})
