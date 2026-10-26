"""Independent PostgreSQL demo effect/deduplication and scoped HTTP lookup."""

import asyncio
import hashlib
import os
from pathlib import Path
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient

from task_platform.agent_protocol import encode
from task_platform.demo import create_demo_app

pytestmark = pytest.mark.integration


async def test_concurrent_exact_resends_have_one_effect_and_scoped_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    directory = Path(os.environ["TEST_SECRETS_DIR"])
    monkeypatch.setenv("DEMO_DATABASE_URL_FILE", str(directory / "demo_database_url"))
    monkeypatch.setenv("DEMO_SERVICE_KEY_FILE", str(directory / "demo_service_key"))
    key = (directory / "demo_service_key").read_text().strip()
    app = create_demo_app()
    owner, operation = str(uuid4()), str(uuid4())
    payload = encode(
        {
            "owner": owner,
            "operation": operation,
            "report": {"type": "report", "title": "x", "summary": "42", "citations": []},
        }
    )
    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://demo") as client,
    ):
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        replies = await asyncio.gather(
            *(
                client.post(
                    "/reports",
                    content=payload,
                    headers={**headers, "X-Transmission-ID": str(uuid4())},
                )
                for _ in range(8)
            )
        )
        assert all(r.status_code == 200 for r in replies), [r.status_code for r in replies]
        assert all(r.content == replies[0].content for r in replies)
        assert replies[0].json()["payload_hash"] == hashlib.sha256(payload).hexdigest()
        lookup = await client.get(f"/operations/{owner}/{operation}", headers=headers)
        assert lookup.json() == replies[0].json()
        conflict = await client.post(
            "/reports",
            content=payload.replace(b'"summary":"42"', b'"summary":"99"'),
            headers={**headers, "X-Transmission-ID": str(uuid4())},
        )
        assert conflict.status_code == 409
        assert (
            await client.get(f"/operations/{uuid4()}/{operation}", headers=headers)
        ).status_code == 404
        assert (await client.post("/reports", content=payload)).status_code == 401
        assert (await client.get(f"/operations/{owner}/{operation}")).status_code == 401
        assert (await client.post("/fault/drop")).status_code == 404


async def test_physical_lost_response_then_probe_and_resend_keep_one_effect(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    import time

    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    from task_platform.execution import Permit
    from task_platform.report_transport import ReportTransport

    directory = Path(os.environ["TEST_SECRETS_DIR"])
    env = os.environ.copy()
    env["DEMO_DATABASE_URL_FILE"] = str(directory / "demo_database_url")
    env["DEMO_SERVICE_KEY_FILE"] = str(directory / "demo_service_key")
    env["DEMO_TEST_DROP"] = "1"
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.demo_server",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert process.stdout
    port = int(await asyncio.wait_for(process.stdout.readline(), 5))
    url = f"http://127.0.0.1:{port}"
    key = (directory / "demo_service_key").read_text().strip()
    transport = ReportTransport(url, key)
    owner, operation = str(uuid4()), str(uuid4())
    payload = encode(
        {
            "owner": owner,
            "operation": operation,
            "report": {"type": "report", "title": "x", "summary": "42", "citations": []},
        }
    )
    # Bounded readiness through the actual socket, no application request before ready.
    for _ in range(100):
        try:
            reader, writer = await asyncio.open_connection("127.0.0.1", port)
            writer.close()
            await writer.wait_closed()
            break
        except OSError:
            await asyncio.sleep(0.02)
    try:
        monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:1")
        assert await transport.send(Permit(uuid4(), payload, 5, time.monotonic())) is None
        proof = await transport.lookup(owner, operation, 5)
        assert proof is not None and proof["payload_hash"] == hashlib.sha256(payload).hexdigest()
        assert await transport.send(Permit(uuid4(), payload, 5, time.monotonic())) is None
        engine = create_async_engine((directory / "demo_database_url").read_text().strip())
        try:
            async with engine.connect() as c:
                assert (
                    await c.scalar(
                        text(
                            "SELECT count(*) FROM report_effects WHERE owner=:owner AND "
                            "operation=:operation"
                        ),
                        {"owner": owner, "operation": operation},
                    )
                    == 1
                )
                assert (
                    await c.scalar(
                        text(
                            "SELECT count(*) FROM invocation_journal WHERE owner=:owner AND "
                            "operation=:operation"
                        ),
                        {"owner": owner, "operation": operation},
                    )
                    == 2
                )
        finally:
            await engine.dispose()
    finally:
        await transport.close()
        process.terminate()
        await asyncio.wait_for(process.wait(), 5)
