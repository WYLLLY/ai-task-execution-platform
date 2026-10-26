"""Bounded M4 worker: one durable unit per lease, fresh checkpoint on every claim."""

import asyncio
from contextlib import suppress
from uuid import uuid4

from task_platform.execution import Lease
from task_platform.report_transport import ReportTransport
from task_platform.task_models import Step
from task_platform.transport import ModelTransport
from task_platform.worker import Worker
from task_platform.workflow import AnalysisExecutor


class AnalysisWorker(Worker):
    executor: AnalysisExecutor

    def __init__(
        self,
        executor: AnalysisExecutor,
        model: ModelTransport,
        reports: ReportTransport,
        *,
        holder: str,
        heartbeat_seconds: float = 10,
    ) -> None:
        super().__init__(executor, model, holder=holder, heartbeat_seconds=heartbeat_seconds)
        self.reports = reports

    def slot_worker(self, holder: str) -> Worker:
        return AnalysisWorker(self.executor, self.transport, self.reports, holder=holder)

    async def execute(self, lease: Lease) -> None:
        async with self.executor.factory() as session:
            step = await session.get(Step, lease.step_id)
            if step is None:
                return
            path = step.path
        if path.endswith("/tool"):
            await self.executor.run_tool(lease)
            return
        saving = path == "analysis/save-report"
        payload = await (
            self.executor.report_payload(lease) if saving else self.executor.context(lease)
        )
        if payload is None:
            return
        permit = await self.executor.authorize(lease, uuid4(), payload)
        if permit is None:
            return
        if saving:
            proof = await self.reports.send(permit)
            await self.executor.complete(
                lease,
                permit.call_id,
                "succeeded" if proof else "unknown",
                cost=0,
                tokens=0,
                result=proof,
            )
        else:
            outcome = await self.transport.send(permit)
            await self.executor.complete(
                lease,
                permit.call_id,
                outcome.outcome,
                cost=outcome.cost,
                tokens=outcome.tokens,
                result=outcome.response,
                retry_after_seconds=outcome.retry_after_seconds,
                observation=outcome.observation,
            )

    async def run_once(self) -> bool:
        lease = await self.executor.claim(self.holder)
        if lease is None:
            return False
        heartbeat = asyncio.create_task(self._heartbeat(lease))
        executing = asyncio.create_task(self.execute(lease))
        try:
            done, _ = await asyncio.wait(
                {heartbeat, executing}, return_when=asyncio.FIRST_COMPLETED
            )
            if executing in done:
                executing.result()
            return True
        finally:
            heartbeat.cancel()
            executing.cancel()
            _, pending = await asyncio.wait({heartbeat, executing}, timeout=2)
            if pending:
                raise RuntimeError("Worker cleanup exceeded grace period")
            for child in (heartbeat, executing):
                with suppress(asyncio.CancelledError):
                    child.result()

    async def maintenance(self) -> None:
        await self.executor.maintenance()
        await self.executor.recover_reports(self.reports)
