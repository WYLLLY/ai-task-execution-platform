"""Evidence-based recovery: lost history remains uncertainty, never invented B rows."""

import hashlib
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Literal
from uuid import UUID, uuid4

from pydantic import AwareDatetime, Field, field_validator
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from task_platform.accounting import admission_reason
from task_platform.agent_protocol import encode
from task_platform.command_schemas import StrictSchema, TaskInput
from task_platform.corpus import authorized_documents
from task_platform.execution import db_now
from task_platform.execution_models import ExternalCall, ProviderAdmission
from task_platform.identity import NotFoundError
from task_platform.models import APIKey, PlatformControl, Principal
from task_platform.task_models import Attempt, Step, Task, TaskEvent, WorkflowVersion
from task_platform.tasks import Factory, required_workflows, supported_workflow


class Evidence(StrictSchema):
    evidence_id: str = Field(min_length=1, max_length=90)
    proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")


class RestoreEvidence(Evidence):
    restored_at: AwareDatetime
    incident_at: AwareDatetime
    old_workers_isolated: Literal[True]
    credentials_revoked: Literal[True]
    live_egress_blocked: Literal[True]

    @field_validator("restored_at", "incident_at", mode="before")
    @classmethod
    def timestamp(cls, value: object) -> datetime:
        if not isinstance(value, str):
            raise ValueError("Explicit ISO timestamp required")
        return datetime.fromisoformat(value)


class OwnerReserve(StrictSchema):
    principal_id: str
    cost: int = Field(ge=0, le=2**63 - 1)
    tokens: int = Field(ge=0, le=2**63 - 1)


class ReserveEvidence(Evidence):
    revision: int = Field(ge=0, le=2**63 - 2)
    cost: int = Field(ge=0, le=2**63 - 1)
    tokens: int = Field(ge=0, le=2**63 - 1)
    owners: list[OwnerReserve] = Field(max_length=1000)
    reason: str = Field(min_length=1, max_length=1000)


async def administrator(session: AsyncSession) -> None:
    if await session.scalar(text("SELECT current_user")) != "atp_admin":
        raise ValueError("Dedicated administrator role required")


async def locked_platform(session: AsyncSession) -> PlatformControl:
    await administrator(session)
    return (
        await session.scalars(
            select(PlatformControl).where(PlatformControl.id == 1).with_for_update()
        )
    ).one()


async def previous(
    session: AsyncSession, epoch: UUID, details: dict[str, object], kind: str
) -> bool:
    row = (
        await session.execute(
            text(
                "SELECT fingerprint,kind FROM restore_decisions "
                "WHERE epoch=:epoch AND evidence_id=:id"
            ),
            {"epoch": epoch, "id": details["evidence_id"]},
        )
    ).one_or_none()
    if row is None:
        return False
    digest = hashlib.sha256(encode(details)).hexdigest()
    if row != (digest, kind):
        raise ValueError("Evidence content conflict")
    return True


async def record(session: AsyncSession, epoch: UUID, details: dict[str, object], kind: str) -> None:
    await session.execute(
        text(
            "INSERT INTO restore_decisions(epoch,evidence_id,fingerprint,kind,details) "
            "VALUES(:epoch,:id,:hash,:kind,CAST(:details AS jsonb))"
        ),
        {
            "epoch": epoch,
            "id": details["evidence_id"],
            "hash": hashlib.sha256(encode(details)).hexdigest(),
            "kind": kind,
            "details": json.dumps(details),
        },
    )


def current(platform: PlatformControl, epoch: UUID) -> dict[str, object]:
    if platform.execution_epoch != epoch or platform.recovery is None:
        raise ValueError("Recovery epoch mismatch")
    return platform.recovery


async def begin_restore(factory: Factory, epoch: UUID, evidence: object) -> dict[str, int]:
    value = RestoreEvidence.model_validate(evidence)
    details = value.model_dump(mode="json")
    details["restored_at"] = value.restored_at.isoformat()
    details["incident_at"] = value.incident_at.isoformat()
    if os.environ.get("DISPATCH_ENABLED") != "false":
        raise ValueError("Explicit external dispatch lock required")
    now = datetime.now(UTC)
    if not now - timedelta(days=7) <= value.restored_at <= value.incident_at <= now:
        raise ValueError("Invalid or expired backup point")
    async with factory.begin() as session:
        platform = await locked_platform(session)
        if await previous(session, epoch, details, "begin"):
            recovery = current(platform, epoch)
            return {"quarantined": int(str(recovery["quarantined"]))}
        if platform.execution_epoch == epoch:
            raise ValueError("New epoch required")
        result = await session.scalar(
            text("SELECT quarantine_restore(:epoch,CAST(:details AS jsonb))"),
            {"epoch": epoch, "details": json.dumps(details)},
        )
        await record(session, epoch, details, "begin")
        if not isinstance(result, dict):
            raise ValueError("Invalid recovery result")
        return {"quarantined": int(result["quarantined"])}


async def revise_reserve(factory: Factory, epoch: UUID, evidence: object) -> bool:
    value = ReserveEvidence.model_validate(evidence)
    details = value.model_dump(mode="json")
    allocations = {UUID(item.principal_id): item for item in value.owners}
    if len(allocations) != len(value.owners):
        raise ValueError("Duplicate owner allocation")
    if (
        sum(a.cost for a in value.owners) > value.cost
        or sum(a.tokens for a in value.owners) > value.tokens
    ):
        raise ValueError("Owner reserves exceed deployment reserve")
    async with factory.begin() as session:
        platform = await locked_platform(session)
        recovery = current(platform, epoch)
        if await previous(session, epoch, details, "reserve"):
            return True
        if platform.reserve_revision != value.revision:
            raise ValueError("Reserve revision conflict")
        owners = (
            await session.scalars(select(Principal).order_by(Principal.id).with_for_update())
        ).all()
        if not allocations.keys() <= {owner.id for owner in owners}:
            raise ValueError("Unknown owner allocation")
        for owner in owners:
            allocation = allocations.get(owner.id)
            owner.reserve_cost = allocation.cost if allocation else 0
            owner.reserve_tokens = allocation.tokens if allocation else 0
        platform.reserve_cost = value.cost
        platform.reserve_tokens = value.tokens
        platform.reserve_revision += 1
        platform.recovery = {**recovery, "bounded": True}
        await record(session, epoch, details, "reserve")
        return True


async def reopen(factory: Factory, epoch: UUID, evidence: object) -> bool:
    value = Evidence.model_validate(evidence)
    details = value.model_dump(mode="json")
    async with factory.begin() as session:
        platform = await locked_platform(session)
        recovery = current(platform, epoch)
        if await previous(session, epoch, details, "reopen"):
            return True
        if not recovery.get("bounded"):
            raise ValueError("Disaster exposure is unbounded")
        owners = (
            await session.scalars(select(Principal).order_by(Principal.id).with_for_update())
        ).all()
        workflows = await required_workflows(session)
        if not workflows or any(not supported_workflow(w) for w in workflows):
            raise ValueError("Compatible handlers required")
        provider = await session.scalar(
            select(ProviderAdmission).where(ProviderAdmission.id == 1).with_for_update()
        )
        if provider is None:
            raise ValueError("Provider capacity unavailable")
        now = await db_now(session)
        # Check deployment exposure even when the restored point has no owners.
        # The first identity has no local records; it also conservatively checks
        # unallocated exposure against a possible future owner's limits.
        for owner_id in [uuid4(), *(owner.id for owner in owners)]:
            # A read-only prospective admission uses the same accounting formula.
            # This object is never added/flushed and creates no task, call or B record.
            prospective = Task(
                id=uuid4(),
                principal_id=owner_id,
                tool_calls=0,
                limits={
                    "cost_micro_usd": 2**63 - 1,
                    "tokens": 2**63 - 1,
                    "llm_calls": 2**31 - 1,
                    "tool_calls": 2**31 - 1,
                },
            )
            if await admission_reason(session, prospective, provider, now, 0, 0):
                raise ValueError("Restored budget or capacity requires explicit adjustment")
        platform.admission_open = True
        platform.dispatch_open = True
        platform.recovery = {**recovery, "reopened": True}
        platform.version += 1
        await record(session, epoch, details, "reopen")
        return True


class ReleaseEvidence(Evidence):
    task_id: str
    key_id: str
    expected_task_version: int = Field(gt=0, le=2**63 - 1)
    no_unrecorded_effect: Literal[True]
    action: Literal["resume", "owner_resolution"]
    reason: str = Field(min_length=1, max_length=1000)


async def release_task(factory: Factory, epoch: UUID, evidence: object) -> bool:
    value = ReleaseEvidence.model_validate(evidence)
    details = value.model_dump(mode="json")
    task_id, key_id = UUID(value.task_id), UUID(value.key_id)
    async with factory.begin() as session:
        platform = await locked_platform(session)
        recovery = current(platform, epoch)
        if await previous(session, epoch, details, "release"):
            return True
        if not recovery.get("bounded") or not platform.dispatch_open:
            raise ValueError("Recovery admission remains closed")
        owner_id = await session.scalar(select(Task.principal_id).where(Task.id == task_id))
        owner = await session.scalar(
            select(Principal).where(Principal.id == owner_id).with_for_update()
        )
        if owner is None or not owner.active:
            raise ValueError("Current owner unavailable")
        key = await session.scalar(select(APIKey).where(APIKey.id == key_id).with_for_update())
        if key is None or key.principal_id != owner.id or key.revoked_at is not None:
            raise ValueError("Current owner authorization required")
        # Corpus mutations share the platform/owner locks; read before later categories.
        original = await session.get(Task, task_id)
        if original is None:
            raise ValueError("Unknown task")
        workflow = await session.get(
            WorkflowVersion, (original.workflow_name, original.workflow_version)
        )
        if workflow is None or not workflow.available or not supported_workflow(workflow):
            raise ValueError("Compatible handler unavailable")
        try:
            await authorized_documents(
                session, owner.id, TaskInput.model_validate(original.input).documents
            )
        except NotFoundError:
            raise ValueError("Current document rights required") from None
        await session.execute(
            text("SELECT lock_restore_state(:task,:key)"), {"task": task_id, "key": key_id}
        )
        task = await session.scalar(select(Task).where(Task.id == task_id))
        if (
            task is None
            or task.state != "suspended"
            or task.reason != "restore_quarantine"
            or (task.task_version != value.expected_task_version)
        ):
            raise ValueError("Quarantine state or version conflict")
        steps = (
            await session.scalars(select(Step).where(Step.task_id == task_id).order_by(Step.id))
        ).all()
        attempts = (
            await session.scalars(
                select(Attempt).where(Attempt.task_id == task_id).order_by(Attempt.id)
            )
        ).all()
        calls = (
            await session.scalars(
                select(ExternalCall)
                .where(ExternalCall.task_id == task_id)
                .order_by(ExternalCall.id)
            )
        ).all()
        now = await db_now(session)
        if task.deadline_at <= now or (key.expires_at is not None and key.expires_at <= now):
            raise ValueError("Deadline or current authorization expired")
        if any(a.state == "active" for a in attempts) or not steps:
            raise ValueError("Unresolved attempt state")
        unknown = [s for s in steps if s.state == "unknown"]
        if value.action == "resume":
            if unknown or any(c.state in {"unknown", "authorized"} for c in calls):
                raise ValueError("Uncertain external result requires owner resolution")
            if not any(s.state == "ready" for s in steps):
                raise ValueError("No durable continuation")
        elif len(unknown) != 1 or not any(
            c.id == unknown[0].selected_call_id and c.provider in {"simulator-v1", "openai-v1"}
            for c in calls
        ):
            raise ValueError("Owner resolution requires a retained uncertain LLM call")
        if any(not c.payload for c in calls) or any(
            s.path.endswith("/save_report") and not s.payload for s in steps
        ):
            raise ValueError("Exact payload unavailable")
        await record(session, epoch, details, "release")
        await session.execute(
            text("SELECT associate_restored_task(:task,:key,:epoch,:resolution)"),
            {
                "task": task_id,
                "key": key_id,
                "epoch": epoch,
                "resolution": value.action == "owner_resolution",
            },
        )
        await session.refresh(task)
        session.add(
            TaskEvent(
                task_id=task.id,
                principal_id=task.principal_id,
                sequence=task.event_sequence,
                kind="task.recovery_released",
                data={"state": task.state, "task_version": task.task_version},
            )
        )
        return True


async def recovery_status(factory: Factory) -> dict[str, object] | None:
    async with factory() as session:
        platform = await session.get(PlatformControl, 1)
        if platform is None or platform.recovery is None:
            return None
        return {
            key: platform.recovery[key]
            for key in ("restored_at", "incident_at", "acceptance_history_may_be_lost")
        }
