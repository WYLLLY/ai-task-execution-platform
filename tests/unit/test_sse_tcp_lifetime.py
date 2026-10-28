"""Physical HTTP chunk framing at repeated normal SSE expiry (no database required)."""

import asyncio
import sys
import time

import httpx


async def test_normal_lifetime_reconnects_with_clean_tcp_eof_and_releases_tickets() -> None:
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.sse_expiry_server",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        assert process.stdout is not None
        port = (await asyncio.wait_for(process.stdout.readline(), 5)).decode("ascii")
        assert port.strip().isdigit(), "trusted local fixture did not publish its TCP port"
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port.strip()}", trust_env=False, timeout=2
        ) as client:
            deadline = time.monotonic() + 5
            while True:
                try:
                    assert (await client.get("/health")).status_code == 200
                    break
                except httpx.ConnectError:
                    assert time.monotonic() < deadline
                    await asyncio.sleep(0.01)
            for _ in range(3):
                async with client.stream("GET", "/stream") as response:
                    assert response.status_code == 200
                    body = b"".join([chunk async for chunk in response.aiter_bytes()])
                    assert body == b"id: 1\ndata: synthetic\n\n"
            assert (await client.get("/counts")).json() == {"opened": 3, "released": 3}
    finally:
        process.terminate()
        await asyncio.wait_for(process.communicate(), 5)
