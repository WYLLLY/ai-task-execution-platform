"""Integer simulated reservations. All callers hold platform/owner/provider/task locks."""

from datetime import datetime, timedelta

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from task_platform.execution_models import CallCost, ExternalCall, ProviderAdmission
from task_platform.task_models import Task


async def admission_reason(
    session: AsyncSession,
    task: Task,
    provider: ProviderAdmission,
    now: datetime,
    cost: int,
    tokens: int,
) -> str | None:
    scopes = (
        (
            ExternalCall.task_id == task.id,
            int(str(task.limits["cost_micro_usd"])),
            int(str(task.limits["tokens"])),
            False,
        ),
        (
            ExternalCall.principal_id == task.principal_id,
            provider.owner_daily_cost,
            provider.owner_daily_tokens,
            True,
        ),
        (
            ExternalCall.id.is_not(None),
            provider.global_daily_cost,
            provider.global_daily_tokens,
            True,
        ),
    )
    for index, (scope, cap_cost, cap_tokens, daily) in enumerate(scopes):
        current: ColumnElement[bool] = func.date(ExternalCall.authorized_at) == now.date()
        charge = (
            case((current, CallCost.confirmed_cost), else_=0) if daily else CallCost.confirmed_cost
        )
        usage = (
            case((current, CallCost.confirmed_tokens), else_=0)
            if daily
            else CallCost.confirmed_tokens
        )
        row = (
            await session.execute(
                select(
                    func.coalesce(func.sum(charge + CallCost.exposure_cost), 0),
                    func.coalesce(func.sum(usage + CallCost.exposure_tokens), 0),
                    func.coalesce(func.bool_or(CallCost.overrun), False),
                )
                .join(ExternalCall, ExternalCall.id == CallCost.call_id)
                .where(scope)
            )
        ).one()
        if row[2]:
            return "overrun_review"
        if row[0] + cost > cap_cost or row[1] + tokens > cap_tokens:
            return "task_limit" if index == 0 else "capacity_wait"
    calls = await session.scalar(
        select(func.count()).select_from(ExternalCall).where(ExternalCall.task_id == task.id)
    )
    if int(calls or 0) >= int(str(task.limits["llm_calls"])):
        return "task_limit"
    active = await session.scalar(
        select(func.count()).select_from(ExternalCall).where(ExternalCall.local_active.is_(True))
    )
    owner_active = await session.scalar(
        select(func.count())
        .select_from(ExternalCall)
        .where(ExternalCall.local_active.is_(True), ExternalCall.principal_id == task.principal_id)
    )
    window = (
        await session.execute(
            select(func.count(), func.coalesce(func.sum(CallCost.reserved_tokens), 0))
            .select_from(ExternalCall)
            .join(CallCost, CallCost.call_id == ExternalCall.id)
            .where(ExternalCall.authorized_at > now - timedelta(seconds=60))
        )
    ).one()
    if (
        int(active or 0) >= provider.max_active
        or int(owner_active or 0) >= provider.max_owner_active
        or window[0] >= provider.requests_per_minute
        or window[1] + tokens > provider.tokens_per_minute
        or provider.next_allowed_at > now
    ):
        return "capacity_wait"
    return None
