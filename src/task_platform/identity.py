"""Identity services own authorization and complete mutation transactions."""

import hashlib
import re
import secrets
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from task_platform.models import APIKey, AuditEvent, PlatformControl, Principal


class NotFoundError(Exception):
    pass


class AuthenticationError(Exception):
    pass


@dataclass(frozen=True)
class Identity:
    principal_id: UUID
    key_id: UUID


def key_digest(token: str) -> str:
    return hashlib.sha256(token.encode("ascii")).hexdigest()


def new_key() -> str:
    return "atp_" + secrets.token_urlsafe(32)


async def authenticate(session: AsyncSession, token: str) -> Identity:
    if not re.fullmatch(r"atp_[A-Za-z0-9_-]{43}", token):
        raise AuthenticationError
    row = (
        await session.execute(
            select(APIKey.principal_id, APIKey.id)
            .join(Principal)
            .where(
                APIKey.digest == key_digest(token),
                APIKey.revoked_at.is_(None),
                (APIKey.expires_at.is_(None)) | (APIKey.expires_at > func.clock_timestamp()),
                Principal.active.is_(True),
            )
        )
    ).one_or_none()
    if row is None:
        raise AuthenticationError
    return Identity(row.principal_id, row.id)


async def owned_key(session: AsyncSession, identity: Identity, key_id: UUID) -> APIKey:
    key = await session.scalar(
        select(APIKey).where(APIKey.id == key_id, APIKey.principal_id == identity.principal_id)
    )
    if key is None:
        raise NotFoundError
    return key


async def provision_principal(factory: async_sessionmaker[AsyncSession], name: str) -> UUID:
    if not name.strip() or len(name) > 100:
        raise ValueError("Principal name must contain 1 to 100 characters")
    principal_id = uuid4()
    async with factory.begin() as session:
        session.add(Principal(id=principal_id, name=name))
        await session.flush()
        session.add(
            AuditEvent(
                id=uuid4(),
                action="principal.created",
                subject_id=principal_id,
                principal_id=principal_id,
            )
        )
    return principal_id


async def issue_key(
    factory: async_sessionmaker[AsyncSession],
    principal_id: UUID,
    expires_at: datetime | None = None,
) -> tuple[UUID, str]:
    token, key_id = new_key(), uuid4()
    async with factory.begin() as session:
        principal = await session.scalar(
            select(Principal).where(Principal.id == principal_id).with_for_update()
        )
        if principal is None or not principal.active:
            raise NotFoundError
        if expires_at is not None:
            now = (await session.execute(select(func.clock_timestamp()))).scalar_one()
            if expires_at.tzinfo is None or expires_at <= now:
                raise ValueError("Expiry must be a future timezone-aware timestamp")
        session.add(
            APIKey(
                id=key_id,
                principal_id=principal_id,
                digest=key_digest(token),
                expires_at=expires_at,
            )
        )
        session.add(
            AuditEvent(
                id=uuid4(), action="key.created", subject_id=key_id, principal_id=principal_id
            )
        )
    return key_id, token


async def revoke_key(
    factory: async_sessionmaker[AsyncSession], principal_id: UUID, key_id: UUID
) -> None:
    async with factory.begin() as session:
        principal = await session.scalar(
            select(Principal).where(Principal.id == principal_id).with_for_update()
        )
        if principal is None:
            raise NotFoundError
        key = await session.scalar(
            select(APIKey)
            .where(APIKey.id == key_id, APIKey.principal_id == principal_id)
            .with_for_update()
        )
        if key is None:
            raise NotFoundError
        if key.revoked_at is None:
            key.revoked_at = await session.scalar(select(func.clock_timestamp()))
            key.permission_version += 1
            session.add(
                AuditEvent(
                    id=uuid4(), action="key.revoked", subject_id=key_id, principal_id=principal_id
                )
            )


async def disable_principal(factory: async_sessionmaker[AsyncSession], principal_id: UUID) -> None:
    async with factory.begin() as session:
        principal = await session.scalar(
            select(Principal).where(Principal.id == principal_id).with_for_update()
        )
        if principal is None:
            raise NotFoundError
        if principal.active:
            principal.active = False
            principal.permission_version += 1
            session.add(
                AuditEvent(
                    id=uuid4(),
                    action="principal.disabled",
                    subject_id=principal_id,
                    principal_id=principal_id,
                )
            )


async def initialize_platform(factory: async_sessionmaker[AsyncSession], epoch: UUID) -> None:
    async with factory.begin() as session:
        current = await session.get(PlatformControl, 1)
        if current is not None:
            if current.execution_epoch != epoch:
                raise ValueError("Existing epoch differs; restoration is not implemented in M1")
            return
        session.add(PlatformControl(id=1, execution_epoch=epoch))
        session.add(AuditEvent(id=uuid4(), action="platform.initialized", subject_id=epoch))
