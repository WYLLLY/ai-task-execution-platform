"""PostgreSQL records for simulated external execution and accounting."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from task_platform.models import Base


class ExternalCall(Base):
    __tablename__ = "external_calls"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    task_id: Mapped[UUID]
    principal_id: Mapped[UUID]
    step_id: Mapped[UUID]
    attempt_id: Mapped[UUID]
    generation: Mapped[int] = mapped_column(BigInteger)
    execution_epoch: Mapped[UUID]
    principal_version: Mapped[int] = mapped_column(BigInteger)
    key_version: Mapped[int] = mapped_column(BigInteger)
    state: Mapped[str] = mapped_column(String(20))
    payload: Mapped[bytes] = mapped_column(LargeBinary)
    payload_hash: Mapped[str] = mapped_column(String(64))
    provider: Mapped[str] = mapped_column(String(40))
    authorized_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    response_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    local_active: Mapped[bool] = mapped_column(Boolean)


class CallCost(Base):
    __tablename__ = "call_costs"
    call_id: Mapped[UUID] = mapped_column(primary_key=True)
    reserved_cost: Mapped[int] = mapped_column(BigInteger)
    reserved_tokens: Mapped[int] = mapped_column(BigInteger)
    confirmed_cost: Mapped[int] = mapped_column(BigInteger, default=0)
    confirmed_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    exposure_cost: Mapped[int] = mapped_column(BigInteger)
    exposure_tokens: Mapped[int] = mapped_column(BigInteger)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    overrun: Mapped[bool] = mapped_column(Boolean, default=False)
    tariff: Mapped[str] = mapped_column(String(40))
    tokenizer: Mapped[str] = mapped_column(String(40))


class CostEvidence(Base):
    __tablename__ = "cost_evidence"
    call_id: Mapped[UUID] = mapped_column(primary_key=True)
    evidence_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    fingerprint: Mapped[str] = mapped_column(String(64))
    expected_revision: Mapped[int] = mapped_column(BigInteger)
    cost: Mapped[int] = mapped_column(BigInteger)
    tokens: Mapped[int] = mapped_column(BigInteger)
    final: Mapped[bool] = mapped_column(Boolean)


class ProviderAdmission(Base):
    __tablename__ = "provider_admission"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    requests_per_minute: Mapped[int] = mapped_column(Integer)
    tokens_per_minute: Mapped[int] = mapped_column(BigInteger)
    max_active: Mapped[int] = mapped_column(Integer)
    max_owner_active: Mapped[int] = mapped_column(Integer)
    next_allowed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    global_daily_cost: Mapped[int] = mapped_column(BigInteger)
    owner_daily_cost: Mapped[int] = mapped_column(BigInteger)
    global_daily_tokens: Mapped[int] = mapped_column(BigInteger)
    owner_daily_tokens: Mapped[int] = mapped_column(BigInteger)
