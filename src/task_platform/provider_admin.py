"""Administrative provider registration and evidence-based reconciliation."""

import hashlib
from typing import Literal, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator
from sqlalchemy import select

from task_platform.agent_protocol import encode
from task_platform.command_schemas import StrictSchema
from task_platform.execution import apply_settlement, prefix
from task_platform.execution_models import (
    AccountingDecision,
    CallCost,
    ExternalCall,
    ProviderAdmission,
)
from task_platform.models import AuditEvent, PlatformControl
from task_platform.responses import ProviderProfile
from task_platform.task_models import Task, WorkflowVersion
from task_platform.tasks import Factory, retry_transaction


def definition(profile: ProviderProfile) -> dict[str, object]:
    return {
        "workflow": "2",
        "prompt": "1",
        "tools": "1",
        "context_builder": "responses-1",
        "provider": profile.model_dump(),
    }


async def configure_provider(factory: Factory, profile: ProviderProfile) -> None:
    async with factory() as session, session.begin():
        platform = (
            await session.scalars(
                select(PlatformControl).where(PlatformControl.id == 1).with_for_update()
            )
        ).one()
        current = await session.get(WorkflowVersion, ("analysis", "2"))
        if current is not None and current.definition != definition(profile):
            raise ValueError("Provider workflow configuration is immutable")
        if current is None:
            session.add(
                WorkflowVersion(
                    name="analysis", version="2", available=True, definition=definition(profile)
                )
            )
            session.add(
                AuditEvent(
                    id=uuid4(), action="provider.configured", subject_id=platform.execution_epoch
                )
            )
        provider = (
            await session.scalars(
                select(ProviderAdmission).where(ProviderAdmission.id == 1).with_for_update()
            )
        ).one()
        provider.requests_per_minute = profile.effective_rpm
        provider.tokens_per_minute = profile.effective_tpm
        # Windows and cooldown are durable records; registration never resets them.


class Decision(StrictSchema):
    kind: Literal["settlement", "acknowledgement"]
    evidence_id: str = Field(min_length=1, max_length=90)
    revision: int = Field(ge=0, le=2**63 - 1)
    source: str = Field(min_length=1, max_length=200)
    proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=1000)
    cost: int | None = Field(default=None, ge=0, le=2**63 - 1)
    tokens: int | None = Field(default=None, ge=0, le=2**63 - 1)
    final: bool = True
    estimated: bool = False
    tariff_review: str | None = Field(default=None, min_length=1, max_length=1000)
    estimator_review: str | None = Field(default=None, min_length=1, max_length=1000)

    @model_validator(mode="after")
    def complete(self) -> Self:
        if self.kind == "settlement":
            if self.cost is None or self.tokens is None:
                raise ValueError("Complete observed usage required")
        elif (
            not self.tariff_review
            or not self.estimator_review
            or self.cost is not None
            or self.tokens is not None
            or self.estimated
        ):
            raise ValueError("Explicit tariff and estimator review required")
        return self


async def reconcile(factory: Factory, call_id: str, decision: object) -> bool:
    value = Decision.model_validate(decision)
    identity = UUID(call_id)
    details = value.model_dump(mode="json")
    fingerprint = hashlib.sha256(encode(details)).hexdigest()
    async with factory() as session:
        task_id = await session.scalar(
            select(ExternalCall.task_id).where(ExternalCall.id == identity)
        )
    if task_id is None:
        raise ValueError("Unknown call")

    async def operation() -> bool:
        async with factory() as session, session.begin():
            await prefix(session, task_id)
            task = (
                await session.scalars(select(Task).where(Task.id == task_id).with_for_update())
            ).one()
            call = (
                await session.scalars(
                    select(ExternalCall).where(ExternalCall.id == identity).with_for_update()
                )
            ).one()
            previous = await session.get(AccountingDecision, (identity, value.evidence_id))
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    raise ValueError("Evidence content conflict")
                return True
            cost = (
                await session.scalars(
                    select(CallCost).where(CallCost.call_id == identity).with_for_update()
                )
            ).one()
            if cost.revision != value.revision:
                raise ValueError("Revision conflict")
            if value.kind == "settlement":
                if value.cost is None or value.tokens is None:
                    raise ValueError("Missing usage")
                await apply_settlement(
                    session,
                    task,
                    call,
                    "admin:" + value.evidence_id,
                    value.cost,
                    value.tokens,
                    value.revision,
                    value.final,
                )
            elif not cost.overrun:
                raise ValueError("No overrun to acknowledge")
            session.add(
                AccountingDecision(
                    call_id=identity,
                    evidence_id=value.evidence_id,
                    fingerprint=fingerprint,
                    kind=value.kind,
                    revision=value.revision,
                    details=details,
                )
            )
            session.add(
                AuditEvent(
                    id=uuid4(),
                    action="accounting." + value.kind,
                    principal_id=task.principal_id,
                    subject_id=identity,
                )
            )
            return True

    return await retry_transaction(operation)
