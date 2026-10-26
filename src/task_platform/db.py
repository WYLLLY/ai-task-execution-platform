"""Bounded independent SQLAlchemy pools, with no implicit transaction retries."""

import time
from collections import deque
from typing import Any

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import AsyncAdaptedQueuePool, ConnectionPoolEntry

from task_platform.config import Settings


class ObservedPool(AsyncAdaptedQueuePool):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.slow_acquisitions: deque[tuple[float, int]] = deque(maxlen=256)

    def _do_get(self) -> ConnectionPoolEntry:
        started = time.monotonic()
        try:
            return super()._do_get()
        finally:
            milliseconds = int((time.monotonic() - started) * 1000)
            if milliseconds > 1000:
                self.slow_acquisitions.append((time.monotonic(), milliseconds))

    @property
    def max_recent_acquire_milliseconds(self) -> int:
        cutoff = time.monotonic() - 60
        while self.slow_acquisitions and self.slow_acquisitions[0][0] < cutoff:
            self.slow_acquisitions.popleft()
        return max((value for stamp, value in self.slow_acquisitions), default=0)


def create_engine(settings: Settings, *, control: bool = False) -> AsyncEngine:
    size = settings.control_pool_size if control else settings.read_pool_size
    if settings.role == "worker" and not control:
        size = settings.worker_pool_size
    if settings.role in {"admin", "migrator"}:
        size = 2 if settings.role == "admin" else 1
    return create_async_engine(
        settings.database_url.get_secret_value(),
        pool_size=size,
        poolclass=ObservedPool,
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
