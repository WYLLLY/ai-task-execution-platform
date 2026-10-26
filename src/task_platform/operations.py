"""Fixed operational signals, Prometheus output and actionable bounded alert timers."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Self

from prometheus_client import CollectorRegistry, Gauge, generate_latest
from pydantic import Field, model_validator
from sqlalchemy import text

from task_platform.command_schemas import StrictSchema
from task_platform.tasks import Factory


@dataclass(frozen=True)
class Rule:
    field: str
    above: int
    hold: int
    identity: str
    action: str
    section: str


RULES = (
    Rule(
        "runtime_status_unknown",
        0,
        0,
        "runtime.check_missing",
        "Inspect private API availability and metrics collection; pool state is unknown.",
        "pools",
    ),
    Rule(
        "oldest_ready_seconds",
        30,
        120,
        "queue.delayed",
        "Check workers, provider capacity and DB waits; exclude scheduled capacity waits.",
        "queue",
    ),
    Rule(
        "expired_lease_seconds",
        15,
        0,
        "lease.expired",
        "Inspect worker event loop and control pool; stop the defective worker.",
        "leases",
    ),
    Rule(
        "renewal_failures",
        2,
        0,
        "lease.renewal_failed",
        "Inspect DB availability and stop the worker that cannot renew ownership.",
        "leases",
    ),
    Rule(
        "pool_utilization_percent",
        80,
        60,
        "pool.saturated",
        "Reduce read traffic and inspect long transactions; preserve control capacity.",
        "pools",
    ),
    Rule(
        "pool_acquire_milliseconds",
        1000,
        0,
        "pool.acquire_slow",
        "Inspect pool waiters and long transactions; preserve heartbeat connections.",
        "pools",
    ),
    Rule(
        "oldest_unknown_seconds",
        900,
        0,
        "accounting.unknown",
        "Reconcile retained exposure with evidence; never release it by age.",
        "accounting",
    ),
    Rule(
        "overrun_calls",
        0,
        0,
        "accounting.overrun",
        "Review tariff and estimator, then explicitly acknowledge the current revision.",
        "accounting",
    ),
    Rule(
        "handler_unavailable_tasks",
        0,
        0,
        "handler.unavailable",
        "Restore the pinned compatible handler before owner-authorized resolution.",
        "handlers",
    ),
    Rule(
        "event_invariants",
        0,
        0,
        "events.invariant",
        "Inspect event continuity and retention metadata; reload snapshots, do not skip gaps.",
        "events",
    ),
    Rule(
        "disk_used_percent",
        80,
        0,
        "storage.disk_full",
        "Check data/WAL disk usage and close new admission if storage is unsafe.",
        "storage",
    ),
    Rule(
        "backup_failed",
        0,
        0,
        "backup.failed",
        "Inspect the backup job and last verified copy; report the actual RPO.",
        "backups",
    ),
    Rule(
        "backup_age_seconds",
        86400,
        0,
        "backup.stale",
        "Create and verify a backup; report the current recovery exposure.",
        "backups",
    ),
    Rule(
        "disk_status_unknown",
        0,
        0,
        "storage.check_missing",
        "Provide a current verified data-volume disk observation.",
        "storage",
    ),
    Rule(
        "backup_status_unknown",
        0,
        0,
        "backup.check_missing",
        "Provide the last verified backup result; backup execution is implemented in M7.",
        "backups",
    ),
)
ALERT_IDS = frozenset(rule.identity for rule in RULES)
METRICS = frozenset(rule.field for rule in RULES) | {
    "unknown_calls",
    "active_calls",
    "exposure_micro_usd",
    "exposure_tokens",
    "suspended_tasks",
    "expired_leases",
    "sse_connections",
    "sse_followed_tasks",
    "read_pool_in_use",
    "control_pool_in_use",
}


class Alerts:
    def __init__(self) -> None:
        self.pending: dict[str, float] = {}

    def evaluate(self, values: dict[str, int], now: float) -> list[dict[str, str]]:
        result = []
        for rule in RULES:
            if values.get(rule.field, 0) <= rule.above:
                self.pending.pop(rule.identity, None)
                continue
            started = self.pending.setdefault(rule.identity, now)
            if now - started >= rule.hold:
                result.append(
                    {
                        "id": rule.identity,
                        "action": rule.action,
                        "runbook": "docs/operations.md#" + rule.section,
                    }
                )
        return result


def prometheus(values: dict[str, int]) -> str:
    registry = CollectorRegistry()
    for name in sorted(METRICS & values.keys()):
        value = values[name]
        if type(value) is int and value >= 0:
            Gauge("atp_" + name, "AI Task Platform " + name, registry=registry).set(value)
    return generate_latest(registry).decode("ascii")


class Probe(StrictSchema):
    disk_used_percent: int = Field(ge=0, le=100)
    backup_last_success: datetime | None
    backup_failed: bool

    @model_validator(mode="after")
    def valid_time(self) -> Self:
        stamp = self.backup_last_success
        if stamp is not None and (stamp.tzinfo is None or stamp > datetime.now(UTC)):
            raise ValueError("Verified backup timestamp must be timezone aware and not future")
        return self

    def values(self) -> dict[str, int]:
        return {
            "disk_used_percent": self.disk_used_percent,
            "backup_failed": int(self.backup_failed),
            "backup_age_seconds": int(
                (datetime.now(UTC) - self.backup_last_success).total_seconds()
            )
            if self.backup_last_success
            else 0,
            "backup_status_unknown": int(self.backup_last_success is None),
        }


async def operational_snapshot(factory: Factory) -> dict[str, int]:
    async with factory() as session:
        if await session.scalar(text("SELECT current_user")) != "atp_admin":
            raise ValueError("Dedicated administrator role required")
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
                AND lease_until<clock_timestamp()) AS expired_leases,
            (SELECT coalesce(max(extract(epoch FROM clock_timestamp()-lease_until)),0)
                FROM steps WHERE state='running'
                AND lease_until<clock_timestamp()) AS expired_lease_seconds,
            (SELECT coalesce(max(extract(epoch FROM clock_timestamp()-s.available_at)),0)
                FROM steps s JOIN tasks t ON t.id=s.task_id JOIN platform_control p ON p.id=1
                WHERE s.state='ready' AND s.available_at<=clock_timestamp()
                  AND t.state IN('queued','running') AND t.execution_epoch=p.execution_epoch
                  AND t.deadline_at>clock_timestamp()) AS oldest_ready_seconds,
            (SELECT coalesce(max(extract(epoch FROM clock_timestamp()-x.authorized_at)),0)
                FROM external_calls x JOIN call_costs c ON c.call_id=x.id
                WHERE x.state='unknown' AND (c.exposure_cost>0 OR c.exposure_tokens>0))
                AS oldest_unknown_seconds,
            (SELECT count(*) FROM call_costs c WHERE c.overrun AND NOT EXISTS (
                SELECT 1 FROM accounting_decisions d WHERE d.call_id=c.call_id
                  AND d.kind='acknowledgement' AND d.revision=c.revision)) AS overrun_calls,
            (SELECT count(*) FROM tasks WHERE state='suspended'
                AND reason='handler_unavailable') AS handler_unavailable_tasks
        """)
                )
            )
            .mappings()
            .one()
        )
        return {name: int(value) for name, value in values.items()}


async def runtime_snapshot() -> dict[str, int]:
    """Collect only fixed aggregate signals from the private API process."""
    import asyncio
    from decimal import Decimal, InvalidOperation

    import httpx

    allowed = {
        "sse_connections",
        "sse_followed_tasks",
        "read_pool_in_use",
        "control_pool_in_use",
        "event_invariants",
        "pool_acquire_milliseconds",
        "pool_utilization_percent",
    }
    try:
        async with (
            asyncio.timeout(1),
            httpx.AsyncClient(
                trust_env=False,
                follow_redirects=False,
                transport=httpx.AsyncHTTPTransport(retries=0),
                timeout=0.8,
            ) as client,
            client.stream("GET", "http://api:8000/metrics") as response,
        ):
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                body.extend(chunk)
                if len(body) > 16384:
                    raise ValueError("Runtime metrics size limit")
        values = {}
        for line in body.decode("ascii").splitlines():
            if line.startswith("#"):
                continue
            parts = line.split()
            if len(parts) != 2 or not parts[0].startswith("atp_"):
                continue
            name = parts[0][4:]
            if name not in allowed:
                continue
            value = Decimal(parts[1])
            if not value.is_finite() or value < 0 or value > 2**63 - 1 or value != int(value):
                raise ValueError("Invalid runtime metric")
            values[name] = int(value)
        if values.keys() != allowed:
            raise ValueError("Incomplete runtime metrics")
        return values
    except httpx.HTTPError, OSError, TimeoutError, ValueError, InvalidOperation:
        return {"runtime_status_unknown": 1}
