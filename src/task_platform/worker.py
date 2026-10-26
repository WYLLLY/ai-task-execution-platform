"""Bounded simulated worker with independent heartbeat and PostgreSQL polling."""

import asyncio
import json
import logging
from contextlib import suppress
from uuid import uuid4

from sqlalchemy.exc import SQLAlchemyError

from task_platform.execution import Executor, Lease
from task_platform.task_models import Task
from task_platform.transport import SimulationTransport

logger = logging.getLogger(__name__)


class Worker:
    def __init__(
        self,
        executor: Executor,
        transport: SimulationTransport,
        *,
        holder: str,
        heartbeat_seconds: float = 10,
    ) -> None:
        self.executor = executor
        self.transport = transport
        self.holder = holder
        self.heartbeat_seconds = heartbeat_seconds

    async def _heartbeat(self, lease: Lease) -> bool:
        while True:
            await asyncio.sleep(self.heartbeat_seconds)
            try:
                if not await self.executor.heartbeat(lease):
                    return False
            except SQLAlchemyError, OSError, TimeoutError:
                logger.error("", extra={"event": "worker.lease_lost"})
                return False

    async def run_once(self) -> bool:
        lease = await self.executor.claim(self.holder)
        if lease is None:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(lease))
        sending: asyncio.Task[object] | None = None
        try:
            async with self.executor.factory() as session:
                task = await session.get(Task, lease.task_id)
                if task is None:
                    return True
                payload = json.dumps(task.input, separators=(",", ":"), ensure_ascii=True).encode()
            permit = await self.executor.authorize(lease, uuid4(), payload)
            if permit is None:
                return True
            request = asyncio.create_task(self.transport.send(permit))
            sending = request
            done, _ = await asyncio.wait({request, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
            if heartbeat in done:
                return True
            outcome = request.result()
            await self.executor.complete(
                lease,
                permit.call_id,
                outcome.outcome,
                cost=outcome.cost,
                tokens=outcome.tokens,
                retry_after_seconds=outcome.retry_after_seconds,
            )
            return True
        finally:
            heartbeat.cancel()
            if sending is not None:
                sending.cancel()
            pending = [heartbeat] + ([sending] if sending is not None else [])
            _, unresponsive = await asyncio.wait(pending, timeout=2)
            if unresponsive:
                # The CLI supervisor treats this as fatal; never claim another unit.
                raise RuntimeError("Worker cleanup exceeded grace period")
            for child in pending:
                with suppress(asyncio.CancelledError):
                    child.result()

    def slot_worker(self, holder: str) -> Worker:
        return Worker(self.executor, self.transport, holder=holder)

    async def maintenance(self) -> None:
        await self.executor.maintenance()

    async def run(self, stop: asyncio.Event) -> None:
        async def slot(index: int) -> None:
            worker = self.slot_worker(f"{self.holder}/{index}")
            while not stop.is_set():
                try:
                    worked = await worker.run_once()
                except SQLAlchemyError, OSError, TimeoutError:
                    logger.error("", extra={"event": "worker.database_unavailable"})
                    worked = False
                if not worked:
                    try:
                        await asyncio.wait_for(stop.wait(), 0.25)
                    except TimeoutError:
                        pass

        async def maintenance() -> None:
            while not stop.is_set():
                try:
                    await self.maintenance()
                except SQLAlchemyError, OSError, TimeoutError:
                    logger.error("", extra={"event": "worker.database_unavailable"})
                try:
                    await asyncio.wait_for(stop.wait(), 5)
                except TimeoutError:
                    pass

        async with asyncio.TaskGroup() as group:
            for index in range(4):
                group.create_task(slot(index))
            group.create_task(maintenance())
