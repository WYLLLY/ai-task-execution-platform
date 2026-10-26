"""Short A/heartbeat transactions. No HTTP or in-memory-only durable ownership."""

import hashlib
import random
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from task_platform.accounting import admission_reason
from task_platform.execution_models import CallCost, CostEvidence, ExternalCall, ProviderAdmission
from task_platform.execution_state import valid_usage
from task_platform.models import APIKey, PlatformControl, Principal
from task_platform.task_models import Attempt, Step, Task, TaskEvent, WorkflowVersion
from task_platform.tasks import PINNED_VERSIONS, TERMINAL, Factory, retry_transaction


@dataclass(frozen=True)
class Lease:
    task_id: UUID
    step_id: UUID
    attempt_id: UUID
    holder: str
    generation: int
    epoch: UUID


@dataclass(frozen=True)
class Permit:
    call_id: UUID
    payload: bytes
    remaining_seconds: float
    measured_at: float


async def prefix(
    session: AsyncSession, task_id: UUID
) -> tuple[PlatformControl, Principal, ProviderAdmission]:
    owner_id = await session.scalar(select(Task.principal_id).where(Task.id == task_id))
    if owner_id is None:
        raise ValueError("Unknown task")
    platform = (
        await session.scalars(
            select(PlatformControl).where(PlatformControl.id == 1).with_for_update()
        )
    ).one()
    owner = (
        await session.scalars(select(Principal).where(Principal.id == owner_id).with_for_update())
    ).one()
    # Key revocation takes owner lock; workflow availability takes platform lock.
    # These prefixes serialize both before we read their current versions at B.
    provider = (
        await session.scalars(
            select(ProviderAdmission).where(ProviderAdmission.id == 1).with_for_update()
        )
    ).one()
    return platform, owner, provider


def close_attempt(step: Step, attempt: Attempt, now: datetime, state: str, reason: str) -> None:
    attempt.state = state
    attempt.ended_at = now
    attempt.reason = reason
    step.holder = None
    step.lease_until = None


def stop_before_call(
    session: AsyncSession,
    rows: tuple[Task, Step, Attempt],
    platform: PlatformControl,
    owner: Principal,
    now: datetime,
    reason: str,
) -> None:
    task, step, attempt = rows
    close_attempt(
        step,
        attempt,
        now,
        "failed" if reason in {"task_limit", "capacity_wait"} else "revoked",
        reason,
    )
    if reason == "task_limit":
        step.state = "failed"
        task.state = "failed"
        platform.open_tasks -= 1
        owner.open_tasks -= 1
    elif reason == "capacity_wait":
        step.state = "retry_wait"
        step.available_at = now + timedelta(seconds=5)
    else:
        step.state = "ready"
        task.state = "suspended"
    task.reason = reason
    task.task_version += 1
    event(
        session, task, "step.retry_scheduled" if reason == "capacity_wait" else f"task.{task.state}"
    )


async def db_now(session: AsyncSession) -> datetime:
    value: datetime = (await session.execute(select(func.clock_timestamp()))).scalar_one()
    return value


def event(session: AsyncSession, task: Task, kind: str) -> None:
    task.event_sequence += 1
    session.add(
        TaskEvent(
            task_id=task.id,
            principal_id=task.principal_id,
            sequence=task.event_sequence,
            kind=kind,
            data={"state": task.state, "task_version": task.task_version},
        )
    )


async def locked_lease(session: AsyncSession, lease: Lease) -> tuple[Task, Step, Attempt] | None:
    task = await session.scalar(select(Task).where(Task.id == lease.task_id).with_for_update())
    step = await session.scalar(
        select(Step)
        .where(Step.id == lease.step_id, Step.task_id == lease.task_id)
        .with_for_update()
    )
    attempt = await session.scalar(
        select(Attempt)
        .where(Attempt.id == lease.attempt_id, Attempt.step_id == lease.step_id)
        .with_for_update()
    )
    if task is None or step is None or attempt is None:
        return None
    return task, step, attempt


async def owns(session: AsyncSession, lease: Lease, rows: tuple[Task, Step, Attempt]) -> bool:
    task, step, attempt = rows
    now = await db_now(session)
    epoch = await session.scalar(
        select(PlatformControl.execution_epoch).where(PlatformControl.id == 1)
    )
    return (
        task.state == "running"
        and step.state == "running"
        and attempt.state == "active"
        and step.holder == lease.holder
        and step.generation == lease.generation
        and attempt.generation == lease.generation
        and attempt.worker == lease.holder
        and task.execution_epoch == lease.epoch == epoch == attempt.execution_epoch
        and step.lease_until is not None
        and step.lease_until > now
        and task.deadline_at > now
    )


class Executor:
    def __init__(self, factory: Factory, control: Factory, epoch: UUID) -> None:
        self.factory = factory
        self.control = control
        self.epoch = epoch

    def handler_available(self, task: Task, step: Step | None = None) -> bool:
        # The retained M3 contract fixture cannot execute M4 corpus/continuation work.
        return (
            task.workflow_name == "analysis"
            and task.workflow_version == "1"
            and task.versions == PINNED_VERSIONS
            and not task.input.get("documents")
            and (step is None or step.path == "analysis/turn-1/model")
        )

    async def claim(self, holder: str) -> Lease | None:
        if not holder or len(holder) > 100:
            raise ValueError("Invalid worker identity")

        async def operation() -> Lease | None:
            async with self.factory.begin() as session:
                task = await session.scalar(
                    select(Task)
                    .join(Step, Step.task_id == Task.id)
                    .where(
                        Task.state.in_(("queued", "running")),
                        Task.execution_epoch == self.epoch,
                        Task.deadline_at > func.clock_timestamp(),
                        Step.state == "ready",
                        Step.available_at <= func.clock_timestamp(),
                    )
                    .order_by(Step.available_at, Task.created_at, Task.id)
                    .limit(1)
                    .with_for_update(of=Task, skip_locked=True)
                )
                if task is None:
                    return None
                step = (
                    await session.scalars(
                        select(Step)
                        .where(Step.task_id == task.id, Step.state == "ready")
                        .with_for_update()
                    )
                ).one()
                now = await db_now(session)
                if now >= task.deadline_at:
                    return None
                if not self.handler_available(task, step):
                    task.state = "suspended"
                    task.reason = "handler_unavailable"
                    task.task_version += 1
                    event(session, task, "task.suspended")
                    return None
                number = await session.scalar(
                    select(func.coalesce(func.max(Attempt.attempt_number), 0)).where(
                        Attempt.step_id == step.id
                    )
                )
                step.state = "running"
                step.generation += 1
                step.holder = holder
                step.lease_until = now + timedelta(seconds=60)
                attempt = Attempt(
                    id=uuid4(),
                    task_id=task.id,
                    step_id=step.id,
                    attempt_number=int(number or 0) + 1,
                    generation=step.generation,
                    state="active",
                    worker=holder,
                    execution_epoch=self.epoch,
                    started_at=now,
                )
                session.add(attempt)
                task.state = "running"
                task.task_version += 1
                event(session, task, "step.started")
                return Lease(task.id, step.id, attempt.id, holder, step.generation, self.epoch)

        return await retry_transaction(operation)

    async def heartbeat(self, lease: Lease) -> bool:
        async with self.control.begin() as session:
            rows = await locked_lease(session, lease)
            if rows is None or not await owns(session, lease, rows):
                return False
            rows[1].lease_until = await db_now(session) + timedelta(seconds=60)
            return True

    async def authorization_reason(
        self, session: AsyncSession, task: Task, step: Step, payload: bytes
    ) -> str | None:
        return None

    def payload_storage_charge(self, step: Step, payload: bytes) -> int:
        return len(payload) + 2048

    def call_kind(self, step: Step) -> str:
        return "simulator-v1"

    def reservation(self, step: Step, payload: bytes) -> tuple[int, int, str, str]:
        tokens = len(payload) + 2000 if self.call_kind(step) == "simulator-v1" else 0
        return tokens * 2, tokens, "simulated-micro-usd-v1", "simulated-bytes-v1"

    async def record_authorization(
        self, task: Task, step: Step, payload: bytes, now: datetime
    ) -> None:
        return None

    async def advance(
        self, session: AsyncSession, task: Task, step: Step, call: ExternalCall, result: object
    ) -> None:
        step.result = {"simulated": True, "call_id": str(call.id)}
        task.result = step.result

    async def authorize(self, lease: Lease, call_id: UUID, payload: bytes) -> Permit | None:
        if not payload:
            raise ValueError("Simulated context limit")

        async def operation() -> Permit | None:
            async with self.factory.begin() as session:
                platform, owner, provider = await prefix(session, lease.task_id)
                rows = await locked_lease(session, lease)
                if rows is None or not await owns(session, lease, rows):
                    return None
                task, step, attempt = rows
                kind = self.call_kind(step)
                reserved_cost, reserved_tokens, tariff, tokenizer = self.reservation(step, payload)
                existing = await session.get(ExternalCall, call_id)
                if step.selected_call_id is not None or existing is not None:
                    # Re-reading a committed B is never a new permission to send.
                    return None
                key = await session.get(APIKey, task.key_id)
                workflow = await session.get(
                    WorkflowVersion, (task.workflow_name, task.workflow_version)
                )
                measured_at = time.monotonic()
                now = await db_now(session)
                if not platform.dispatch_open or platform.execution_epoch != self.epoch:
                    reason: str | None = "dispatch_closed"
                elif (
                    not owner.active
                    or key is None
                    or key.revoked_at is not None
                    or (key.expires_at is not None and key.expires_at <= now)
                ):
                    reason = "permission_revoked"
                elif workflow is None or not workflow.available:
                    reason = "handler_unavailable"
                elif kind == "simulator-v1":
                    reason = await admission_reason(
                        session, task, provider, now, reserved_cost, reserved_tokens
                    )
                else:
                    reason = await admission_reason(
                        session, task, provider, now, reserved_cost, reserved_tokens, kind=kind
                    )
                if reason is None:
                    reason = await self.authorization_reason(session, task, step, payload)
                storage_charge = self.payload_storage_charge(step, payload)
                if (
                    owner.storage_bytes + storage_charge > owner.max_storage_bytes
                    or platform.storage_bytes + storage_charge > platform.max_storage_bytes
                ):
                    reason = "capacity_wait"
                if len(payload) > (12000 if kind == "simulator-v1" else 65536):
                    stop_before_call(session, rows, platform, owner, now, "task_limit")
                    task.reason = "context_limit"
                    return None
                if reason:
                    stop_before_call(session, rows, platform, owner, now, reason)
                    return None
                if key is None:
                    raise ValueError("Missing authorizing key")
                await self.record_authorization(task, step, payload, now)
                call = ExternalCall(
                    id=call_id,
                    task_id=task.id,
                    principal_id=owner.id,
                    step_id=step.id,
                    attempt_id=attempt.id,
                    generation=step.generation,
                    execution_epoch=self.epoch,
                    principal_version=owner.permission_version,
                    key_version=key.permission_version,
                    state="authorized",
                    payload=payload,
                    payload_hash=hashlib.sha256(payload).hexdigest(),
                    provider=kind,
                    authorized_at=now,
                    local_active=True,
                )
                session.add(call)
                await session.flush()
                session.add(
                    CallCost(
                        call_id=call_id,
                        reserved_cost=reserved_cost,
                        reserved_tokens=reserved_tokens,
                        exposure_cost=reserved_cost,
                        exposure_tokens=reserved_tokens,
                        tariff=tariff,
                        tokenizer=tokenizer,
                    )
                )
                owner.storage_bytes += storage_charge
                platform.storage_bytes += storage_charge
                task.storage_bytes += storage_charge
                step.selected_call_id = call_id
                task.task_version += 1
                event(session, task, "call.authorized")
                return Permit(
                    call_id, payload, (task.deadline_at - now).total_seconds(), measured_at
                )

        # Only confirmed transaction rollbacks can retry. Lost acknowledgements propagate:
        # the caller must not send, and lease recovery classifies the durable call as unknown.
        return await retry_transaction(operation)

    async def complete(
        self,
        lease: Lease,
        call_id: UUID,
        outcome: str,
        *,
        cost: int | None = None,
        tokens: int | None = None,
        retry_after_seconds: int = 0,
        result: object = None,
        observation: dict[str, object] | None = None,
    ) -> bool:
        if outcome not in {"succeeded", "unknown", "rejected", "retryable"}:
            raise ValueError("Invalid external outcome")
        if (cost is None) != (tokens is None):
            raise ValueError("Incomplete definitive usage")
        if type(retry_after_seconds) is not int or not 0 <= retry_after_seconds <= 86400:
            raise ValueError("Invalid retry delay")

        async def operation() -> bool:
            async with self.factory.begin() as session:
                platform, owner, provider = await prefix(session, lease.task_id)
                rows = await locked_lease(session, lease)
                if rows is None:
                    return False
                task, step, attempt = rows
                call = await session.scalar(
                    select(ExternalCall)
                    .where(
                        ExternalCall.id == call_id,
                        ExternalCall.task_id == task.id,
                        ExternalCall.attempt_id == lease.attempt_id,
                    )
                    .with_for_update()
                )
                if call is None:
                    return False
                if observation is not None:
                    if (
                        call.provider_observation is not None
                        and call.provider_observation != observation
                    ):
                        raise ValueError("Provider observation conflict")
                    call.provider_observation = observation
                first_response = call.response_observed_at is None
                if first_response:
                    call.response_observed_at = await db_now(session)
                if retry_after_seconds and first_response and call.response_observed_at is not None:
                    provider.next_allowed_at = max(
                        provider.next_allowed_at,
                        call.response_observed_at + timedelta(seconds=retry_after_seconds),
                    )
                accepted = await owns(session, lease, rows) and step.selected_call_id == call_id
                duplicate = (
                    task.state == "succeeded"
                    and step.state == "succeeded"
                    and call.state == "succeeded"
                    and step.selected_call_id == call_id
                )
                if cost is not None and tokens is not None:
                    evidence_id = f"response:{call_id}"
                    previous = await session.get(CostEvidence, (call_id, evidence_id))
                    record = (
                        await session.scalars(
                            select(CallCost).where(CallCost.call_id == call_id).with_for_update()
                        )
                    ).one()
                    revision = previous.expected_revision if previous else record.revision
                    if (
                        previous is None
                        and record.revision > 0
                        and (record.confirmed_cost != cost or record.confirmed_tokens != tokens)
                    ):
                        raise ValueError("Response accounting conflict")
                    await apply_settlement(
                        session, task, call, evidence_id, cost, tokens, revision, True
                    )
                # Settlement may have waited for the cost row. All lease/deadline checks
                # must use fresh DB time after the final acquired lock.
                accepted = accepted and await owns(session, lease, rows)
                if not accepted:
                    # Evidence can settle an old call, never select an old continuation.
                    if outcome != "unknown" and call.state in {"authorized", "unknown"}:
                        call.state = "succeeded" if outcome == "succeeded" else "rejected"
                    return duplicate
                # Keep accepted usage even if M4 validation crosses the lease/deadline.
                # Roll back only business progression, including a newly flushed step.
                deadline, lease_until = task.deadline_at, step.lease_until
                progression = await session.begin_nested()
                now = await db_now(session)
                call.local_active = False
                call.state = (
                    "unknown"
                    if outcome == "unknown"
                    else ("succeeded" if outcome == "succeeded" else "rejected")
                )
                close_attempt(
                    step, attempt, now, "succeeded" if outcome == "succeeded" else "failed", outcome
                )
                task.task_version += 1
                if outcome == "unknown":
                    step.state = "unknown"
                    task.state = "suspended"
                    task.reason = "external_unknown"
                    event(session, task, "task.suspended")
                elif outcome == "retryable" and step.failures < 2:
                    step.failures += 1
                    step.state = "retry_wait"
                    step.selected_call_id = None
                    step.available_at = max(
                        provider.next_allowed_at,
                        now + timedelta(seconds=random.SystemRandom().uniform(0, 2**step.failures)),
                    )
                    event(session, task, "step.retry_scheduled")
                else:
                    step.state = "succeeded" if outcome == "succeeded" else "failed"
                    task.state = step.state
                    task.reason = None if outcome == "succeeded" else "external_rejected"
                    if outcome == "succeeded":
                        with session.no_autoflush:
                            await self.advance(session, task, step, call, result)
                        if step.state == "failed":
                            attempt.state = "failed"
                            attempt.reason = task.reason
                    if task.state in TERMINAL:
                        owner.open_tasks -= 1
                        platform.open_tasks -= 1
                    event(session, task, f"step.{step.state}")
                    if task.state in TERMINAL:
                        event(session, task, f"task.{task.state}")
                with session.no_autoflush:
                    finished_at = await db_now(session)
                if lease_until is None or finished_at >= min(deadline, lease_until):
                    await progression.rollback()
                    await session.refresh(call)
                    call.local_active = False
                    call.state = (
                        "unknown"
                        if outcome == "unknown"
                        else "succeeded"
                        if outcome == "succeeded"
                        else "rejected"
                    )
                    return False
                await progression.commit()
                return True

        return await retry_transaction(operation)

    async def maintenance(self) -> None:
        async with self.control() as session:
            candidates = (
                await session.scalars(
                    select(Task.id)
                    .where(Task.state.not_in(TERMINAL))
                    .order_by(Task.created_at, Task.id)
                    .limit(100)
                )
            ).all()
        for task_id in candidates:

            async def operation(task_id: UUID = task_id) -> None:
                async with self.control.begin() as session:
                    platform, owner, _ = await prefix(session, task_id)
                    task = (
                        await session.scalars(
                            select(Task).where(Task.id == task_id).with_for_update()
                        )
                    ).one()
                    if task.state in TERMINAL:
                        return
                    steps = (
                        await session.scalars(
                            select(Step)
                            .where(Step.task_id == task.id)
                            .order_by(Step.id)
                            .with_for_update()
                        )
                    ).all()
                    attempts = (
                        await session.scalars(
                            select(Attempt)
                            .where(Attempt.task_id == task.id)
                            .order_by(Attempt.id)
                            .with_for_update()
                        )
                    ).all()
                    calls = (
                        await session.scalars(
                            select(ExternalCall)
                            .where(ExternalCall.task_id == task.id)
                            .order_by(ExternalCall.id)
                            .with_for_update()
                        )
                    ).all()
                    now = await db_now(session)
                    if now >= task.deadline_at:
                        for attempt in attempts:
                            if attempt.state == "active":
                                attempt.state = "revoked"
                                attempt.ended_at = now
                                attempt.reason = "deadline"
                        for step in steps:
                            if step.state not in {"succeeded", "failed", "cancelled"}:
                                step.state = "cancelled"
                                step.holder = None
                                step.lease_until = None
                                step.generation += 1
                        for call in calls:
                            call.local_active = False
                            if call.state == "authorized":
                                call.state = "unknown"
                        task.state = "timed_out"
                        task.task_version += 1
                        owner.open_tasks -= 1
                        platform.open_tasks -= 1
                        event(session, task, "task.timed_out")
                        return
                    for step in steps:
                        if task.state == "suspended":
                            continue
                        if step.state == "retry_wait" and step.available_at <= now:
                            step.state = "ready"
                            task.task_version += 1
                            event(session, task, "step.ready")
                        elif (
                            step.state == "running"
                            and step.lease_until is not None
                            and step.lease_until <= now
                        ):
                            attempt = next(
                                a for a in attempts if a.step_id == step.id and a.state == "active"
                            )
                            close_attempt(step, attempt, now, "expired", "lease_lost")
                            selected = next(
                                (c for c in calls if c.id == step.selected_call_id), None
                            )
                            if selected is not None:
                                selected.local_active = False
                                if selected.state == "authorized":
                                    selected.state = "unknown"
                                step.state = "unknown"
                                task.state = "suspended"
                                task.reason = "external_unknown"
                            elif step.failures < 2:
                                step.failures += 1
                                step.state = "retry_wait"
                                step.available_at = now + timedelta(seconds=1)
                            else:
                                step.state = "failed"
                                task.state = "failed"
                                task.reason = "attempt_limit"
                                owner.open_tasks -= 1
                                platform.open_tasks -= 1
                            task.task_version += 1
                            event(
                                session,
                                task,
                                "task.suspended" if selected is not None else "step.recovered",
                            )

            await retry_transaction(operation)

    async def settle(
        self,
        call_id: UUID,
        evidence: str,
        cost: int,
        tokens: int,
        revision: int,
        *,
        final: bool = True,
    ) -> bool:
        valid_usage(cost, tokens, revision)
        async with self.factory() as session:
            task_id = await session.scalar(
                select(ExternalCall.task_id).where(ExternalCall.id == call_id)
            )
        if task_id is None:
            raise ValueError("Unknown call")

        async def operation() -> bool:
            async with self.factory.begin() as session:
                await prefix(session, task_id)
                task = (
                    await session.scalars(select(Task).where(Task.id == task_id).with_for_update())
                ).one()
                call = (
                    await session.scalars(
                        select(ExternalCall).where(ExternalCall.id == call_id).with_for_update()
                    )
                ).one()
                return await apply_settlement(
                    session, task, call, evidence, cost, tokens, revision, final
                )

        return await retry_transaction(operation)


async def apply_settlement(
    session: AsyncSession,
    task: Task,
    call: ExternalCall,
    evidence: str,
    cost: int,
    tokens: int,
    revision: int,
    final: bool,
) -> bool:
    valid_usage(cost, tokens, revision)
    if not evidence or len(evidence) > 100:
        raise ValueError("Invalid evidence identity")
    fingerprint = hashlib.sha256(f"{cost}:{tokens}:{revision}:{final}".encode()).hexdigest()
    previous = await session.get(CostEvidence, (call.id, evidence))
    if previous is not None:
        if previous.fingerprint != fingerprint:
            raise ValueError("Evidence conflict")
        return True
    record = (
        await session.scalars(select(CallCost).where(CallCost.call_id == call.id).with_for_update())
    ).one()
    if record.revision != revision:
        raise ValueError("Revision conflict")
    session.add(
        CostEvidence(
            call_id=call.id,
            evidence_id=evidence,
            fingerprint=fingerprint,
            expected_revision=revision,
            cost=cost,
            tokens=tokens,
            final=final,
        )
    )
    if final:
        changed = (
            record.revision == 0
            or record.confirmed_cost != cost
            or record.confirmed_tokens != tokens
            or record.exposure_cost != 0
            or record.exposure_tokens != 0
        )
        record.confirmed_cost = cost
        record.confirmed_tokens = tokens
        record.exposure_cost = 0
        record.exposure_tokens = 0
        record.revision += int(changed)
        record.overrun = (
            record.overrun or cost > record.reserved_cost or tokens > record.reserved_tokens
        )
    event(session, task, "usage.updated")
    return True
