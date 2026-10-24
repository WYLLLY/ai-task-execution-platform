"""One HTTPX request per permit; fixed OpenAI destination, no SDK/proxy/retry."""

import asyncio
import os
import time
from types import TracebackType
from typing import Self
from urllib.parse import urlsplit

import httpx
from pydantic import SecretStr

from task_platform.command_schemas import CommandError, parse_command
from task_platform.execution import Permit
from task_platform.responses import ProviderProfile, parse_output
from task_platform.transport import Outcome, retry_delay


class ResponsesTransport:
    def __init__(
        self, profile: ProviderProfile, key: SecretStr, *, test_url: str | None = None
    ) -> None:
        self.profile = profile
        self.url = "https://api.openai.com/v1/responses"
        if test_url is not None:
            parsed = urlsplit(test_url)
            if (
                os.environ.get("ATP_TEST_PROFILE") != "1"
                or key.get_secret_value() != "synthetic-test-key"
                or parsed.scheme != "http"
                or parsed.hostname != "127.0.0.1"
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
                or parsed.path not in {"", "/"}
            ):
                raise ValueError("Isolated synthetic loopback fixture required")
            self.url = test_url.rstrip("/") + "/v1/responses"
        if not key.get_secret_value():
            raise ValueError("Worker credential required")
        limits = httpx.Limits(max_connections=4, max_keepalive_connections=4)
        self.client = httpx.AsyncClient(
            transport=httpx.AsyncHTTPTransport(retries=0, trust_env=False, limits=limits),
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(connect=5, read=10, write=10, pool=2),
            headers={
                "Authorization": "Bearer " + key.get_secret_value(),
                "OpenAI-Project": profile.project,
                "Content-Type": "application/json",
                "Accept-Encoding": "identity",
            },
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
                    headers={"X-Client-Request-Id": str(permit.call_id)},
                ) as response,
            ):
                delay = retry_delay(response.headers.get("Retry-After"), response.status_code)
                if response.status_code != 200:
                    return Outcome(retry_after_seconds=delay)
                data = bytearray()
                async for chunk in response.aiter_raw():
                    if len(data) + len(chunk) > 1048576:
                        return Outcome(retry_after_seconds=delay)
                    data.extend(chunk)
                raw, _ = parse_command(bytes(data))
                return parse_output(self.profile, raw).model_copy(
                    update={"retry_after_seconds": delay}
                )
        except httpx.HTTPError, TimeoutError, CommandError:
            return Outcome()
