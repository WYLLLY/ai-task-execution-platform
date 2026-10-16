"""Bounded independent SQLAlchemy pools, with no implicit transaction retries."""

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from task_platform.config import Settings


def create_engine(settings: Settings, *, control: bool = False) -> AsyncEngine:
    size = settings.control_pool_size if control else settings.read_pool_size
    if settings.role == "worker" and not control:
        size = settings.worker_pool_size
    if settings.role in {"admin", "migrator"}:
        size = 2 if settings.role == "admin" else 1
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=size,
        max_overflow=0,
        pool_timeout=2,
        pool_pre_ping=True,
        isolation_level="READ COMMITTED",
        hide_parameters=True,
        connect_args={
            "connect_timeout": 2,
            "options": "-c statement_timeout=3000 -c lock_timeout=1000 "
            "-c idle_in_transaction_session_timeout=5000 -c timezone=UTC",
        },
    )


def sessions(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False)
