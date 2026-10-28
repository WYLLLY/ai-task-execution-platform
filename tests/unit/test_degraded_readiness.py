"""The recovery barrier changes no command or infrastructure state."""

import asyncio

import httpx
import pytest

from tests.support.degraded_readiness import wait_for_recovery


async def test_existing_barrier_accepts_recovered_independent_redis() -> None:
    async def ping() -> bool:
        return True

    async with httpx.AsyncClient(
        base_url="http://api",
        transport=httpx.MockTransport(
            lambda _: httpx.Response(200, json={"submission_available": True, "redis": True})
        ),
    ) as client:
        await wait_for_recovery(ping, client)


async def test_recovery_waits_for_actual_api_readiness_without_sending_a_command() -> None:
    requests: list[httpx.Request] = []

    async def ping() -> bool:
        return True

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        ready = len(requests) >= 3
        return httpx.Response(200, json={"redis": ready, "submission_available": ready})

    async with httpx.AsyncClient(
        base_url="http://api", transport=httpx.MockTransport(respond)
    ) as client:
        await wait_for_recovery(ping, client, seconds=1)
    assert len(requests) == 3, "independent Redis PING is not API recovery"
    assert all(item.method == "GET" and item.url.path == "/health/ready" for item in requests)


async def test_api_that_never_recovers_fails_within_the_same_absolute_bound() -> None:
    probes: list[httpx.Request] = []

    async def ping() -> bool:
        return True

    def respond(request: httpx.Request) -> httpx.Response:
        probes.append(request)
        return httpx.Response(503, json={"redis": False, "submission_available": False})

    async with httpx.AsyncClient(
        base_url="http://api", transport=httpx.MockTransport(respond)
    ) as client:
        async with asyncio.timeout(0.3):
            with pytest.raises(TimeoutError):
                await wait_for_recovery(ping, client, seconds=0.03)
    assert probes
    assert all(item.method == "GET" for item in probes)
