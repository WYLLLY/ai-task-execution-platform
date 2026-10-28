"""Administrative erasure in the independent demo DB, using explicit terminal evidence."""

import json
from datetime import datetime, timedelta
from uuid import UUID

from pydantic import Field, field_validator
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from task_platform.command_schemas import StrictSchema
from task_platform.config import secret_setting


class TerminalEvidence(StrictSchema):
    evidence_id: str = Field(pattern=r"^[A-Za-z0-9:_-]{1,128}$")
    owner: UUID
    operation: UUID
    payload_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    terminal_at: datetime
    proof_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    reason: str = Field(min_length=1, max_length=240)

    @field_validator("terminal_at")
    @classmethod
    def utc_terminal(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() != timedelta(0):
            raise ValueError("Explicit UTC terminal clock required")
        return value


async def erase_demo_content(value: object) -> bool:
    evidence = TerminalEvidence.model_validate_json(json.dumps(value, allow_nan=False))
    url = secret_setting("DEMO_RETENTION_DATABASE_URL").get_secret_value()
    if not url.startswith("postgresql+psycopg://"):
        raise ValueError("Explicit administrative demo PostgreSQL credential required")
    engine = create_async_engine(
        url,
        pool_size=1,
        max_overflow=0,
        pool_timeout=2,
        hide_parameters=True,
        connect_args={"options": "-c timezone=UTC -c statement_timeout=5000 -c lock_timeout=4000"},
    )
    try:
        async with engine.begin() as connection:
            result = await connection.scalar(
                text("SELECT public.purge_demo_content(CAST(:evidence AS jsonb))"),
                {"evidence": json.dumps(evidence.model_dump(mode="json"))},
            )
            if type(result) is not bool:
                raise ValueError("Invalid retention decision")
            return result
    finally:
        await engine.dispose()
