"""M2 durable command/state records; no worker execution or external calls."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from task_platform.models import Base


class WorkflowVersion(Base):
    __tablename__ = "workflow_versions"
    name: Mapped[str] = mapped_column(String(80), primary_key=True)
    version: Mapped[str] = mapped_column(String(40), primary_key=True)
    available: Mapped[bool] = mapped_column(Boolean)
    definition: Mapped[dict[str, object]] = mapped_column(JSONB)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    principal_id: Mapped[UUID]
    key_id: Mapped[UUID]
    workflow_name: Mapped[str] = mapped_column(String(80))
    workflow_version: Mapped[str] = mapped_column(String(40))
    versions: Mapped[dict[str, object]] = mapped_column(JSONB)
    input: Mapped[dict[str, object]] = mapped_column(JSONB)
    limits: Mapped[dict[str, object]] = mapped_column(JSONB)
    execution_epoch: Mapped[UUID]
    state: Mapped[str] = mapped_column(String(20))
    reason: Mapped[str | None] = mapped_column(String(80))
    task_version: Mapped[int] = mapped_column(BigInteger)
    event_sequence: Mapped[int] = mapped_column(BigInteger)
    oldest_retained_sequence: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    deadline_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    result: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    storage_bytes: Mapped[int] = mapped_column(BigInteger)


class Step(Base):
    __tablename__ = "steps"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    task_id: Mapped[UUID]
    principal_id: Mapped[UUID]
    path: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(20))
    generation: Mapped[int] = mapped_column(BigInteger, default=0)
    holder: Mapped[str | None] = mapped_column(String(100))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Attempt(Base):
    __tablename__ = "attempts"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    task_id: Mapped[UUID]
    step_id: Mapped[UUID]
    attempt_number: Mapped[int] = mapped_column(Integer)
    generation: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(20))


class TaskEvent(Base):
    __tablename__ = "task_events"
    task_id: Mapped[UUID] = mapped_column(primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    principal_id: Mapped[UUID]
    kind: Mapped[str] = mapped_column(String(40))
    data: Mapped[dict[str, object]] = mapped_column(JSONB)


class IdempotencyRecord(Base):
    __tablename__ = "idempotency_records"
    principal_id: Mapped[UUID] = mapped_column(primary_key=True)
    route: Mapped[str] = mapped_column(String(100), primary_key=True)
    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    canonical_version: Mapped[str] = mapped_column(String(8))
    fingerprint: Mapped[str] = mapped_column(String(64))
    task_id: Mapped[UUID]
    response_status: Mapped[int] = mapped_column(Integer)
    response_body: Mapped[str] = mapped_column(Text)
