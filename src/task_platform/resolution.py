"""Minimal owner resolution: never invent provider evidence or waive exposure."""

from typing import Literal
from uuid import UUID

from pydantic import Field, ValidationError
from sqlalchemy import select

from task_platform.accounting import admission_reason
from task_platform.command_schemas import CommandError, StrictSchema, TaskInput, invalid
from task_platform.corpus import authorized_documents
from task_platform.execution import db_now, event
from task_platform.execution_models import ExternalCall, ProviderAdmission
from task_platform.execution_state import abandon_calls
from task_platform.identity import NotFoundError
from task_platform.models import APIKey
from task_platform.task_models import Attempt, Step, WorkflowVersion
from task_platform.tasks import (
    CommandResponse,
    Factory,
    commit_command,
    locked_prefix,
    owned_task,
    replay,
    response_for,
    retain,
    retained,
)


class ResolveTask(StrictSchema):
    expected_task_version: int = Field(gt=0)
    action: Literal["regenerate", "resume"]
    accept_overlap_cost_risk: bool = False


async def resolve_task(
    factory: Factory, token: str, task_id: UUID, key: str, value: object, fingerprint: str
) -> CommandResponse:
    route = f"/v1/tasks/{task_id}/resolve"
    identity, existing = await replay(factory, token, route, key, fingerprint)
    if existing is not None:
        return existing
    async with factory() as session:
        await owned_task(session, identity, task_id)
    try:
        command = ResolveTask.model_validate(value)
    except ValidationError:
        raise invalid() from None
    if command.action == "regenerate" and not command.accept_overlap_cost_risk:
        raise invalid()

    async def operation() -> CommandResponse:
        async with factory.begin() as session:
            platform, owner = await locked_prefix(session, identity, token)
            existing = await retained(session, owner.id, route, key, fingerprint)
            if existing is not None:
                return existing
            provider = await session.get(ProviderAdmission, 1)
            task = await owned_task(session, identity, task_id, lock=True)
            if task.task_version != command.expected_task_version or task.state != "suspended":
                raise CommandError(409, "Task version or state conflict")
            if task.reason not in {
                "external_unknown",
                "permission_revoked",
                "handler_unavailable",
                "dispatch_closed",
            }:
                raise CommandError(409, "Administrative resolution required")
            steps = (
                await session.scalars(
                    select(Step).where(Step.task_id == task.id).order_by(Step.id).with_for_update()
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
            step = next((s for s in steps if s.state in {"ready", "unknown"}), None)
            if step is None or any(a.state == "active" for a in attempts):
                raise CommandError(409, "Unresolved execution state")
            old = (
                await session.get(ExternalCall, step.selected_call_id)
                if step.selected_call_id
                else None
            )
            await abandon_calls(session, task)
            now = await db_now(session)
            if now >= task.deadline_at:
                for child in steps:
                    if child.state not in {"succeeded", "failed", "cancelled"}:
                        child.state = "cancelled"
                        child.generation += 1
                        child.holder = None
                        child.lease_until = None
                task.state = "timed_out"
                task.task_version += 1
                owner.open_tasks -= 1
                platform.open_tasks -= 1
                event(session, task, "task.timed_out")
                response = response_for(task)
                retain(session, identity, route, key, fingerprint, task, response)
                return response
            key_record = await session.get(APIKey, task.key_id)
            workflow = await session.get(
                WorkflowVersion, (task.workflow_name, task.workflow_version)
            )
            if (
                not platform.dispatch_open
                or platform.execution_epoch != task.execution_epoch
                or provider is None
                or key_record is None
                or key_record.revoked_at is not None
                or (key_record.expires_at is not None and key_record.expires_at <= now)
                or workflow is None
                or not workflow.available
            ):
                raise CommandError(409, "Execution conditions unavailable")
            try:
                await authorized_documents(
                    session, task.principal_id, TaskInput.model_validate(task.input).documents
                )
            except NotFoundError:
                raise CommandError(409, "Document rights unavailable") from None
            if (command.action == "regenerate") != (step.state == "unknown"):
                raise CommandError(409, "Resolution action conflict")
            if (
                command.action == "regenerate"
                and old is not None
                and old.provider not in {"simulator-v1", "openai-v1"}
            ):
                raise CommandError(409, "Report resolution requires remote evidence")
            payload_size = len(old.payload) if old else 2
            reserve_cost, reserve_tokens = (payload_size + 2000) * 2, payload_size + 2000
            kind = "simulator-v1"
            if old is not None and old.provider == "openai-v1":
                from task_platform.responses import estimate_input, price, validate_profile

                profile = validate_profile(task.versions.get("provider"))
                count = estimate_input(old.payload)
                reserve_cost = price(profile, count, profile.output_tokens, 0)
                reserve_tokens = count + profile.output_tokens
                kind = "openai-v1"
            if await admission_reason(
                session, task, provider, now, reserve_cost, reserve_tokens, kind=kind
            ):
                raise CommandError(409, "Execution limits unavailable")
            step.selected_call_id = None
            step.state = "ready"
            step.available_at = now
            step.generation += 1
            task.state = "queued"
            task.reason = None
            task.task_version += 1
            event(session, task, "task.resumed")
            response = response_for(task)
            retain(session, identity, route, key, fingerprint, task, response)
        return response

    return await commit_command(operation, factory, token, route, key, fingerprint)
