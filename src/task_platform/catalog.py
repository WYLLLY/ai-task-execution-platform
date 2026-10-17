"""Explicit administrative controls; catalogue definitions remain immutable."""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from task_platform.identity import NotFoundError
from task_platform.models import AuditEvent, PlatformControl
from task_platform.task_models import WorkflowVersion


async def set_admission(factory: async_sessionmaker[AsyncSession], enabled: bool) -> None:
    async with factory.begin() as session:
        platform = await session.scalar(
            select(PlatformControl).where(PlatformControl.id == 1).with_for_update()
        )
        if platform is None:
            raise NotFoundError
        if platform.admission_open != enabled:
            platform.admission_open = enabled
            platform.version += 1
            session.add(
                AuditEvent(
                    id=uuid4(), action="platform.admission", subject_id=platform.execution_epoch
                )
            )


async def set_workflow_availability(
    factory: async_sessionmaker[AsyncSession], name: str, version: str, enabled: bool
) -> None:
    async with factory.begin() as session:
        platform = await session.scalar(
            select(PlatformControl).where(PlatformControl.id == 1).with_for_update()
        )
        if platform is None:
            raise NotFoundError
        workflow = await session.scalar(
            select(WorkflowVersion)
            .where(WorkflowVersion.name == name, WorkflowVersion.version == version)
            .with_for_update()
        )
        if workflow is None:
            raise NotFoundError
        if workflow.available != enabled:
            workflow.available = enabled
            session.add(
                AuditEvent(
                    id=uuid4(), action="workflow.availability", subject_id=platform.execution_epoch
                )
            )
