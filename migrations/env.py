"""Migrations require the dedicated credential; never use a runtime role."""

import asyncio

from alembic import context
from sqlalchemy import Connection, text

from task_platform.config import load_settings
from task_platform.db import create_engine
from task_platform.models import Base


def migrate(connection: Connection) -> None:
    if connection.scalar(text("SELECT current_user")) != "atp_migrator":
        raise ValueError("Dedicated migration role required")
    connection.commit()
    context.configure(connection=connection, target_metadata=Base.metadata)
    with context.begin_transaction():
        context.run_migrations()


async def online() -> None:
    engine = create_engine(load_settings("migrator"))
    try:
        async with engine.connect() as connection:
            await connection.run_sync(migrate)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise ValueError("Online migration with the dedicated role is required")
asyncio.run(online())
