"""Integer simulated reservations. All callers hold platform/owner/provider/task locks."""

from datetime import UTC, datetime, timedelta

from sqlalchemy import Integer, case, func, literal_column, or_, select, union
from sqlalchemy.ext.asyncio import AsyncSession

from task_platform.execution_models import (
    AccountingDecision,
    CallCost,
    ExternalCall,
    ProviderAdmission,
)
from task_platform.models import PlatformControl, Principal
from task_platform.task_models import Task


async def admission_reason(
    session: AsyncSession,
    task: Task,
    provider: ProviderAdmission,
    now: datetime,
    cost: int,
    tokens: int,
    *,
    kind: str = "simulator-v1",
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
    platform = await session.get(PlatformControl, 1)
    if platform is not None and platform.recovery is not None:
        if not platform.recovery.get("bounded"):
            return "capacity_wait"
    day = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    current = (ExternalCall.authorized_at >= day) & (
        ExternalCall.authorized_at < day + timedelta(days=1)
    )
    # Keep the threshold literal so generic plans can use the matching partial indexes.
    zero = literal_column("0", Integer())
    pending = or_(CallCost.exposure_cost > zero, CallCost.exposure_tokens > zero, CallCost.overrun)
    unresolved = select(CallCost.call_id).where(pending)
    for index, (scope, cap_cost, cap_tokens, daily) in enumerate(scopes):
        charge = (
            case((current, CallCost.confirmed_cost), else_=0) if daily else CallCost.confirmed_cost
        )
        usage = (
            case((current, CallCost.confirmed_tokens), else_=0)
            if daily
            else CallCost.confirmed_tokens
        )
        candidates = scope
        if daily:
            # Use independent indexable sets. A call can have both today's
            # confirmed charges and exposure, so deduplicate its identity.
            relevant = union(select(ExternalCall.id).where(current, scope), unresolved)
            candidates = scope & ExternalCall.id.in_(relevant)
        row = (
            await session.execute(
                select(
                    func.coalesce(func.sum(charge + CallCost.exposure_cost), 0),
                    func.coalesce(func.sum(usage + CallCost.exposure_tokens), 0),
                    func.coalesce(
                        func.bool_or(
                            CallCost.overrun
                            & ~select(AccountingDecision.call_id)
                            .where(
                                AccountingDecision.call_id == CallCost.call_id,
                                AccountingDecision.kind == "acknowledgement",
                                AccountingDecision.revision == CallCost.revision,
                            )
                            .exists()
                        ),
                        False,
                    ),
                )
                .join(ExternalCall, ExternalCall.id == CallCost.call_id)
                .where(candidates)
            )
        ).one()
        reserve_cost = reserve_tokens = 0
        if platform is not None and (platform.reserve_cost or platform.reserve_tokens):
            if index == 1:
                owner = await session.get(Principal, task.principal_id)
                assigned = (
                    await session.execute(
                        select(
                            func.coalesce(func.sum(Principal.reserve_cost), 0),
                            func.coalesce(func.sum(Principal.reserve_tokens), 0),
                        ).where(or_(Principal.reserve_cost > zero, Principal.reserve_tokens > zero))
                    )
                ).one()
                # Missing exposure with unknown ownership might belong to this owner.
                # Count it conservatively here; globally count the deployment total once.
                reserve_cost = platform.reserve_cost - int(assigned[0])
                reserve_tokens = platform.reserve_tokens - int(assigned[1])
                if owner is not None:
                    reserve_cost += owner.reserve_cost
                    reserve_tokens += owner.reserve_tokens
            elif index == 2:
                reserve_cost, reserve_tokens = platform.reserve_cost, platform.reserve_tokens
        if row[2]:
            return "overrun_review"
        if row[0] + reserve_cost + cost > cap_cost or row[1] + reserve_tokens + tokens > cap_tokens:
            return "task_limit" if index == 0 else "capacity_wait"
    calls = await session.scalar(
        select(func.count())
        .select_from(ExternalCall)
        .where(
            ExternalCall.task_id == task.id,
            ExternalCall.provider.in_(("simulator-v1", "openai-v1")),
        )
    )
    if (
        kind in {"simulator-v1", "openai-v1"}
        and int(calls or 0) >= int(str(task.limits["llm_calls"]))
    ) or (kind == "demo-v1" and task.tool_calls >= int(str(task.limits["tool_calls"]))):
        return "task_limit"
    active = await session.scalar(
        select(func.count()).select_from(ExternalCall).where(ExternalCall.local_active)
    )
    owner_active = await session.scalar(
        select(func.count())
        .select_from(ExternalCall)
        .where(ExternalCall.local_active, ExternalCall.principal_id == task.principal_id)
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
