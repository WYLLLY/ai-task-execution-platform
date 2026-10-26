"""Validated, role-specific configuration; never render secret inputs in errors."""

import os
from pathlib import Path
from typing import Literal, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator

Role = Literal["api", "worker", "admin", "migrator"]


def secret_setting(name: str) -> SecretStr:
    value = os.environ.get(name)
    filename = os.environ.get(f"{name}_FILE")
    if bool(value) == bool(filename):
        raise ValueError(f"Set exactly one of {name} and {name}_FILE")
    return SecretStr(Path(filename).read_text().strip() if filename else value or "")


class Settings(BaseModel):
    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)

    role: Role
    database_url: SecretStr
    redis_url: SecretStr | None = None
    execution_epoch: UUID
    dispatch_enabled: bool = False
    read_pool_size: int = Field(default=8, ge=1, le=8)
    control_pool_size: int = Field(default=2, ge=2, le=2)
    worker_pool_size: int = Field(default=4, ge=1, le=4)
    db_max_connections: int = Field(default=50, ge=1)

    @model_validator(mode="after")
    def validate_budget(self) -> Self:
        # One API + two workers + admin + migration + probes + reserved operator capacity.
        required = self.read_pool_size + self.control_pool_size
        required += 2 * (self.worker_pool_size + self.control_pool_size) + 2 + 1 + 3 + 10
        if required > self.db_max_connections:
            raise ValueError("Database connection budget exceeds configured capacity")
        if not self.database_url.get_secret_value().startswith("postgresql+psycopg://"):
            raise ValueError("The psycopg PostgreSQL URL scheme is required")
        return self


def load_settings(role: Role) -> Settings:
    return Settings(
        role=role,
        database_url=secret_setting("DATABASE_URL"),
        redis_url=secret_setting("REDIS_URL") if role == "api" else None,
        execution_epoch=UUID(os.environ["EXECUTION_EPOCH"]),
        dispatch_enabled=os.environ.get("DISPATCH_ENABLED", "false").lower() == "true",
    )
