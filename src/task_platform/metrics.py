"""Bounded aggregate metrics from durable state; no user/source labels."""

from sqlalchemy import text

from task_platform.tasks import Factory


async def execution_metrics(factory: Factory) -> dict[str, int]:
    async with factory() as session:
        values = (
            (
                await session.execute(
                    text("""
            SELECT
              (SELECT count(*) FROM external_calls WHERE state='unknown') AS unknown_calls,
              (SELECT count(*) FROM external_calls WHERE local_active) AS active_calls,
              (SELECT coalesce(sum(exposure_cost),0) FROM call_costs) AS exposure_micro_usd,
              (SELECT coalesce(sum(exposure_tokens),0) FROM call_costs) AS exposure_tokens,
              (SELECT count(*) FROM tasks WHERE state='suspended') AS suspended_tasks,
              (SELECT count(*) FROM steps WHERE state='running'
                AND lease_until<=clock_timestamp()) AS expired_leases,
              (SELECT coalesce(max(greatest(0,
                extract(epoch FROM clock_timestamp()-available_at))),0)
                FROM steps WHERE state='ready') AS oldest_ready_seconds
        """)
                )
            )
            .mappings()
            .one()
        )
        return {name: int(value) for name, value in values.items()}
