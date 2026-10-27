"""Identity and operational control with PostgreSQL-owned admission counters."""

from datetime import datetime
from uuid import UUID

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, Integer, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Principal(Base):
    __tablename__ = "principals"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    open_tasks: Mapped[int] = mapped_column(Integer, default=0)
    reserve_cost: Mapped[int] = mapped_column(BigInteger, default=0)
    reserve_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    max_open_tasks: Mapped[int] = mapped_column(Integer, default=5)
    storage_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    max_storage_bytes: Mapped[int] = mapped_column(BigInteger, default=104857600)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    permission_version: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class APIKey(Base):
    __tablename__ = "api_keys"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    principal_id: Mapped[UUID] = mapped_column(ForeignKey("principals.id"), index=True)
    digest: Mapped[str] = mapped_column(String(64), unique=True)
    permission_version: Mapped[int] = mapped_column(BigInteger, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class PlatformControl(Base):
    __tablename__ = "platform_control"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    execution_epoch: Mapped[UUID]
    recovery: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    reserve_cost: Mapped[int] = mapped_column(BigInteger, default=0)
    reserve_tokens: Mapped[int] = mapped_column(BigInteger, default=0)
    reserve_revision: Mapped[int] = mapped_column(BigInteger, default=0)
    dispatch_open: Mapped[bool] = mapped_column(Boolean, default=False)
    admission_open: Mapped[bool] = mapped_column(Boolean, default=False)
    version: Mapped[int] = mapped_column(BigInteger, default=1)
    open_tasks: Mapped[int] = mapped_column(Integer, default=0)
    max_open_tasks: Mapped[int] = mapped_column(Integer, default=100)
    storage_bytes: Mapped[int] = mapped_column(BigInteger, default=0)
    max_storage_bytes: Mapped[int] = mapped_column(BigInteger, default=2147483648)


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    action: Mapped[str] = mapped_column(String(40))
    principal_id: Mapped[UUID | None] = mapped_column(ForeignKey("principals.id"))
    subject_id: Mapped[UUID]
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
