"""Explicit manual delivery phases; failed checks never open dispatch."""

from sqlalchemy import func, select, text

from task_platform.config import Settings
from task_platform.execution_models import ExternalCall
from task_platform.health import SCHEMA_REVISION, runtime_privileges
from task_platform.recovery import locked_platform
from task_platform.task_models import Attempt
from task_platform.tasks import Factory, required_workflows, supported_workflow


async def delivery_phase(factory: Factory, settings: Settings, phase: str) -> dict[str, object]:
    if phase not in {"close", "drain", "preflight", "reopen"}:
        raise ValueError("Unknown delivery phase")
    async with factory.begin() as session:
        platform = await locked_platform(session)
        if phase == "close":
            platform.admission_open = platform.dispatch_open = False
            platform.version += 1
            return {"phase": phase, "ready": True}
        active = int(
            await session.scalar(
                select(func.count()).select_from(Attempt).where(Attempt.state == "active")
            )
            or 0
        )
        sending = int(
            await session.scalar(
                select(func.count())
                .select_from(ExternalCall)
                .where(ExternalCall.local_active.is_(True))
            )
            or 0
        )
        closed = not platform.admission_open and not platform.dispatch_open
        ready = closed and active == 0 and sending == 0
        if phase == "drain":
            return {
                "phase": phase,
                "ready": ready,
                "active_attempts": active,
                "local_calls": sending,
            }
        revisions = (await session.scalars(text("SELECT version_num FROM alembic_version"))).all()
        workflows = await required_workflows(session)
        compatible = bool(workflows) and all(supported_workflow(w) for w in workflows)
        ready = ready and revisions == [SCHEMA_REVISION] and compatible
        ready = ready and platform.execution_epoch == settings.execution_epoch
        connection = await session.connection()
        privileges = await runtime_privileges(connection, "api")
        privileges = await runtime_privileges(connection, "worker") and privileges
        ready = ready and privileges
        if phase == "reopen":
            if not ready:
                raise ValueError("Delivery preflight failed; dispatch stays closed")
            if platform.recovery is not None and not platform.recovery.get("reopened"):
                raise ValueError("Recovery requires evidence-based reopening")
            platform.admission_open = platform.dispatch_open = True
            platform.version += 1
        return {
            "phase": phase,
            "ready": ready,
            "schema_compatible": revisions == [SCHEMA_REVISION],
            "handlers_compatible": compatible,
            "runtime_privileges": privileges,
        }
