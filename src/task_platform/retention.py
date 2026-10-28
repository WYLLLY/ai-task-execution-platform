"""Administrative retention boundary; only bounded, database-enforced purge is allowed."""

from sqlalchemy import text

from task_platform.tasks import Factory


async def purge(factory: Factory, *, limit: int = 100) -> dict[str, int]:
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Retention batch must be between 1 and 100")
    async with factory() as session:
        if await session.scalar(text("SELECT current_user")) != "atp_admin":
            raise ValueError("Dedicated administrator role required")
        candidates = (
            await session.scalars(
                text("""
            SELECT id FROM tasks t WHERE
                (purged_at IS NULL AND terminal_at<=clock_timestamp()-interval '30 days')
                OR EXISTS(SELECT 1 FROM idempotency_records r WHERE r.task_id=t.id
                    AND r.response_purged_at IS NULL
                    AND r.created_at<=clock_timestamp()-interval '30 days')
            ORDER BY id LIMIT :limit
        """),
                {"limit": limit},
            )
        ).all()
    totals = {"tasks": 0, "responses": 0}
    for task in candidates:
        # Each task gets its own complete lock order and commit; never acquire
        # a later task after holding another task's call/cost locks.
        async with factory.begin() as session:
            result = await session.scalar(text("SELECT purge_task_contents(:task)"), {"task": task})
            if not isinstance(result, dict):
                raise ValueError("Invalid retention result")
            for name in totals:
                totals[name] += int(result[name])
    return totals
