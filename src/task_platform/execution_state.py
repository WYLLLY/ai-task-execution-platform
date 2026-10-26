"""Atomic cleanup shared by commands and maintenance, never remote cancellation."""

from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from task_platform.execution_models import ExternalCall
from task_platform.task_models import Task


async def abandon_calls(session: AsyncSession, task: Task) -> None:
    calls = (
        await session.scalars(
            select(ExternalCall)
            .where(ExternalCall.task_id == task.id)
            .order_by(ExternalCall.id)
            .with_for_update()
        )
    ).all()
    for call in calls:
        if call.state == "authorized":
            call.state = "unknown"
        call.local_active = False


def valid_usage(cost: int, tokens: int, revision: int) -> None:
    if any(
        type(value) is not int or value < 0 or value > 2**63 - 1
        for value in (cost, tokens, revision)
    ):
        raise ValueError("Invalid usage evidence")


def remaining(deadline: datetime, now: datetime) -> float:
    return max(0.0, (deadline - now).total_seconds())
