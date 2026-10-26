"""Fixed simulated HTTP boundary: one physical request per durable permit."""

import asyncio
import math
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from types import TracebackType
from typing import Literal, Self
from urllib.parse import urlsplit

import httpx
from pydantic import Field, ValidationError, model_validator

from task_platform.command_schemas import CommandError, StrictSchema, parse_command
from task_platform.execution import Permit


class Outcome(StrictSchema):
    outcome: Literal["succeeded", "rejected", "retryable", "unknown"] = "unknown"
    cost: int | None = Field(default=None, ge=0, le=2**63 - 1)
    tokens: int | None = Field(default=None, ge=0, le=2**63 - 1)
    retry_after_seconds: int = Field(default=0, ge=0, le=86400)

    @model_validator(mode="after")
    def complete_usage(self) -> Self:
        if (self.cost is None) != (self.tokens is None):
            raise ValueError("Incomplete usage")
        return self


class SimulationTransport:
    def __init__(self, url: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"127.0.0.1", "simulator"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
        ):
            raise ValueError("Fixed simulation destination required")
        self.url = url.rstrip("/") + "/complete"
        self.client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(retries=0),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=2),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        kind: type[BaseException] | None,
        value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        await self.client.aclose()

    async def send(self, permit: Permit) -> Outcome:
        remaining = min(60.0, permit.remaining_seconds) - (time.monotonic() - permit.measured_at)
        if remaining <= 0:
            return Outcome()
        try:
            async with (
                asyncio.timeout(remaining),
                self.client.stream(
                    "POST",
                    self.url,
                    content=permit.payload,
                    headers={
                        "Content-Type": "application/json",
                        "X-Transmission-ID": str(permit.call_id),
                    },
                ) as response,
            ):
                retry_after = retry_delay(response.headers.get("Retry-After"), response.status_code)
                if response.status_code != 200:
                    return Outcome(retry_after_seconds=retry_after)
                data = bytearray()
                async for chunk in response.aiter_raw():
                    if len(data) + len(chunk) > 1048576:
                        return Outcome()
                    data.extend(chunk)
                value, _ = parse_command(bytes(data))
                return Outcome.model_validate(value).model_copy(
                    update={"retry_after_seconds": retry_after}
                )
        except httpx.HTTPError, TimeoutError, CommandError, ValidationError:
            # These establish ambiguity, never a proof of no remote effect.
            return Outcome()


def retry_delay(value: str | None, status: int) -> int:
    """Bound untrusted Retry-After; status alone never establishes no effect."""
    if value is None:
        return 1 if status == 429 else 0
    try:
        if value.strip().isascii() and value.strip().isdigit():
            return min(86400, int(value))
        when = parsedate_to_datetime(value)
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        return max(0, min(86400, math.ceil((when - datetime.now(UTC)).total_seconds())))
    except ValueError, TypeError, OverflowError:
        return 1
