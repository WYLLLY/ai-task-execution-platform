"""Versioned corpus. Administrative mutations share the admission/dispatch prefix locks."""

import hashlib
from uuid import UUID

from sqlalchemy import Boolean, Integer, String, Text, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from task_platform.command_schemas import DocumentReference
from task_platform.identity import NotFoundError
from task_platform.models import Base, PlatformControl, Principal
from task_platform.tasks import Factory, retry_transaction


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[UUID] = mapped_column(primary_key=True)
    version: Mapped[int] = mapped_column(Integer, primary_key=True)
    principal_id: Mapped[UUID]
    content: Mapped[str] = mapped_column(Text)
    content_hash: Mapped[str] = mapped_column(String(64))
    readable: Mapped[bool] = mapped_column(Boolean, default=True)


async def import_document(factory: Factory, owner_id: UUID, doc_id: UUID, content: str) -> int:
    size = len(content.encode())
    if not 0 < size <= 1048576 or "\x00" in content:
        raise ValueError("Invalid document size or text")

    async def operation() -> int:
        async with factory.begin() as session:
            platform = (await session.scalars(select(PlatformControl).with_for_update())).one()
            owner = await session.scalar(
                select(Principal).where(Principal.id == owner_id).with_for_update()
            )
            if owner is None or not owner.active:
                raise NotFoundError
            previous = (
                await session.scalars(
                    select(Document).where(Document.id == doc_id).order_by(Document.version.desc())
                )
            ).first()
            if previous is not None and previous.principal_id != owner_id:
                raise NotFoundError
            charge = size + 256
            if (
                owner.storage_bytes + charge > owner.max_storage_bytes
                or platform.storage_bytes + charge > platform.max_storage_bytes
            ):
                raise ValueError("Corpus storage limit")
            version = previous.version + 1 if previous else 1
            session.add(
                Document(
                    id=doc_id,
                    version=version,
                    principal_id=owner_id,
                    content=content,
                    content_hash=hashlib.sha256(content.encode()).hexdigest(),
                )
            )
            owner.storage_bytes += charge
            platform.storage_bytes += charge
            return version

    return await retry_transaction(operation)


async def revoke_document(factory: Factory, owner_id: UUID, doc_id: UUID, version: int) -> None:
    async with factory.begin() as session:
        await session.execute(select(PlatformControl).with_for_update())
        await session.execute(select(Principal).where(Principal.id == owner_id).with_for_update())
        doc = await session.scalar(
            select(Document)
            .where(
                Document.id == doc_id,
                Document.version == version,
                Document.principal_id == owner_id,
            )
            .with_for_update()
        )
        if doc is None:
            raise NotFoundError
        doc.readable = False


async def authorized_documents(
    session: AsyncSession, owner_id: UUID, refs: list[DocumentReference]
) -> list[Document]:
    documents: list[Document] = []
    for ref in sorted(refs, key=lambda r: (r.id, r.version)):
        doc = await session.get(Document, (UUID(ref.id), ref.version))
        if doc is None or doc.principal_id != owner_id or not doc.readable:
            raise NotFoundError
        documents.append(doc)
    return documents
