"""One bounded HTTP request per report permit; evidence is identity-bound."""

import asyncio
import hashlib
import time
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import Field, ValidationError

from task_platform.command_schemas import CommandError, StrictSchema, parse_command
from task_platform.execution import Permit


class ReportProof(StrictSchema):
    owner: str
    operation: str
    effect_id: str
    payload_hash: str = Field(pattern=r"^[0-9a-f]{64}$")


def validate_proof(
    value: object, owner: str, operation: str, payload: bytes | None = None
) -> dict[str, object]:
    proof = ReportProof.model_validate(value)
    for identifier in (proof.owner, proof.operation, proof.effect_id):
        UUID(identifier)
    if (
        proof.owner != owner
        or proof.operation != operation
        or (payload is not None and proof.payload_hash != hashlib.sha256(payload).hexdigest())
    ):
        raise ValueError("Invalid report evidence")
    return proof.model_dump()


class ReportTransport:
    def __init__(self, url: str, key: str) -> None:
        parsed = urlsplit(url)
        if (
            parsed.scheme != "http"
            or parsed.hostname not in {"demo", "127.0.0.1"}
            or parsed.username
            or parsed.password
            or parsed.query
            or parsed.fragment
            or parsed.path not in {"", "/"}
            or len(key) < 32
        ):
            raise ValueError("Fixed demo destination and service credential required")
        self.url = url.rstrip("/")
        self.client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(retries=0),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=2),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=4),
            headers={"Authorization": f"Bearer {key}"},
        )

    async def request(
        self,
        method: str,
        path: str,
        seconds: float,
        *,
        payload: bytes = b"",
        transmission: str = "",
    ) -> object:
        if seconds <= 0:
            return None
        try:
            async with (
                asyncio.timeout(min(15, seconds)),
                self.client.stream(
                    method,
                    self.url + path,
                    content=payload,
                    headers={"Content-Type": "application/json", "X-Transmission-ID": transmission},
                ) as response,
            ):
                if response.status_code != 200:
                    return None
                data = bytearray()
                async for chunk in response.aiter_raw():
                    if len(data) + len(chunk) > 65536:
                        return None
                    data.extend(chunk)
                value, _ = parse_command(bytes(data))
                return value
        except httpx.HTTPError, TimeoutError, CommandError:
            return None

    async def send(self, permit: Permit) -> dict[str, object] | None:
        try:
            value, _ = parse_command(permit.payload)
            if not isinstance(value, dict):
                return None
            owner, operation = str(value["owner"]), str(value["operation"])
            seconds = permit.remaining_seconds - (time.monotonic() - permit.measured_at)
            evidence = await self.request(
                "POST",
                "/reports",
                seconds,
                payload=permit.payload,
                transmission=str(permit.call_id),
            )
            return validate_proof(evidence, owner, operation, permit.payload)
        except ValueError, ValidationError, KeyError, CommandError:
            return None

    async def lookup(self, owner: str, operation: str, seconds: float) -> dict[str, object] | None:
        try:
            # Trusted IDs only, never model-selected URL segments.
            owner, operation = str(UUID(owner)), str(UUID(operation))
            evidence = await self.request("GET", f"/operations/{owner}/{operation}", seconds)
            return validate_proof(evidence, owner, operation)
        except ValueError, ValidationError:
            return None

    async def close(self) -> None:
        await self.client.aclose()
