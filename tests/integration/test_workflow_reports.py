"""Report B/C and proof-based continuation, bounded by durable state and time."""

import hashlib
import json
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.execution import Executor
from task_platform.report_transport import ReportTransport

from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch
from .test_workflows import analysis, dossier, model_turn

pytestmark = pytest.mark.integration
REPORT = {
    "status": "completed",
    "items": [
        {
            "type": "report",
            "title": "Synthetic report",
            "summary": "Revenue is 42.",
            "citations": [],
        }
    ],
}


async def test_final_report_is_frozen_and_only_matching_proof_completes(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    await dossier(client, command_owner, admin_engine)
    await model_turn(analysis(executor), REPORT)
    lease = await analysis(executor).claim("save")
    assert lease
    payload = await analysis(executor).report_payload(lease)
    assert payload
    value = json.loads(payload)
    call = uuid4()
    permit = await analysis(executor).authorize(lease, call, payload)
    assert permit and permit.payload == payload
    async with admin_engine.connect() as c:
        assert (
            await c.scalar(text("SELECT payload FROM steps WHERE path='analysis/save-report'"))
            == payload
        )
        assert await c.scalar(text("SELECT tool_calls FROM tasks")) == 1
        assert (
            await c.scalar(
                text("SELECT reserved_cost FROM call_costs WHERE call_id=:call"), {"call": call}
            )
            == 0
        )
        assert (
            await c.scalar(
                text("SELECT provider FROM external_calls WHERE id=:call"), {"call": call}
            )
            == "demo-v1"
        )
    proof = {
        "owner": value["owner"],
        "operation": value["operation"],
        "payload_hash": hashlib.sha256(payload).hexdigest(),
        "effect_id": str(uuid4()),
    }
    assert await analysis(executor).complete(
        lease, call, "succeeded", cost=0, tokens=0, result=proof
    )
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "succeeded"
        result = await c.scalar(text("SELECT result FROM tasks"))
        assert result["report"]["summary"] == "Revenue is 42."
        assert "items" not in result
    assert await analysis(executor).complete(
        lease, call, "succeeded", cost=0, tokens=0, result=proof
    )


async def test_forged_save_evidence_never_reports_success(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    await dossier(client, command_owner, admin_engine)
    await model_turn(analysis(executor), REPORT)
    lease = await analysis(executor).claim("save")
    assert lease
    payload = await analysis(executor).report_payload(lease)
    assert payload
    call = uuid4()
    assert await analysis(executor).authorize(lease, call, payload)
    assert await analysis(executor).complete(
        lease, call, "succeeded", result={"effect_id": str(uuid4())}
    )
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) != "succeeded"


@pytest.fixture
async def report_transport() -> AsyncIterator[ReportTransport]:
    import asyncio
    import os
    import sys
    from pathlib import Path

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
    transport = ReportTransport(
        f"http://127.0.0.1:{port}", (directory / "demo_service_key").read_text().strip()
    )
    try:
        for _ in range(100):
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                break
            except OSError:
                await asyncio.sleep(0.02)
        else:
            pytest.fail("Demo process did not become ready")
        yield transport
    finally:
        await transport.close()
        process.terminate()
        await asyncio.wait_for(process.wait(), 5)


async def test_lost_response_reconciles_selected_call_once_after_restart(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
    report_transport: ReportTransport,
) -> None:
    import asyncio

    await dossier(client, command_owner, admin_engine)
    await model_turn(analysis(executor), REPORT)
    lease = await analysis(executor).claim("save-before-crash")
    assert lease
    payload = await analysis(executor).report_payload(lease)
    assert payload
    call = uuid4()
    permit = await analysis(executor).authorize(lease, call, payload)
    assert permit
    assert await report_transport.send(permit) is None
    assert await analysis(executor).complete(lease, call, "unknown")
    results = await asyncio.gather(
        analysis(executor).recover_reports(report_transport),
        analysis(executor).recover_reports(report_transport),
    )
    assert any(results)
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "succeeded"
        assert (
            await c.scalar(text("SELECT probe_count FROM steps WHERE path='analysis/save-report'"))
            == 1
        )
        assert (
            await c.scalar(text("SELECT count(*) FROM external_calls WHERE provider='demo-v1'"))
            == 1
        )
        assert (
            await c.scalar(
                text("SELECT open_tasks FROM principals WHERE id=(SELECT principal_id FROM tasks)")
            )
            == 0
        )
    assert not await analysis(executor).recover_reports(report_transport)


async def test_absent_probes_are_bounded_and_resend_keeps_payload_and_operation(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
    report_transport: ReportTransport,
) -> None:
    from task_platform.db import create_engine

    from .conftest import settings_for

    await dossier(client, command_owner, admin_engine)
    await model_turn(analysis(executor), REPORT)
    engine = analysis(executor)
    lease = await engine.claim("save")
    assert lease
    payload = await engine.report_payload(lease)
    assert payload
    first_call = uuid4()
    assert await engine.authorize(lease, first_call, payload)
    assert await engine.complete(lease, first_call, "unknown")
    migrator = create_engine(settings_for("migrator"))
    try:
        for index in range(3):
            assert await engine.recover_reports(report_transport)
            async with admin_engine.connect() as c:
                assert (
                    await c.scalar(
                        text("SELECT probe_count FROM steps WHERE path='analysis/save-report'")
                    )
                    == index + 1
                )
                if index < 2:
                    assert await c.scalar(text("SELECT state FROM tasks")) == "suspended"
            assert not await engine.recover_reports(report_transport)
            async with migrator.begin() as c:
                await c.execute(
                    text(
                        "UPDATE steps SET available_at=clock_timestamp() WHERE "
                        "path='analysis/save-report'"
                    )
                )
        replacement = await engine.claim("resend")
        assert replacement
        assert await engine.report_payload(replacement) == payload
        second_call = uuid4()
        assert await engine.authorize(replacement, second_call, payload)
        async with admin_engine.connect() as c:
            assert (
                await c.scalar(
                    text(
                        "SELECT count(DISTINCT payload_hash) FROM external_calls WHERE "
                        "provider='demo-v1'"
                    )
                )
                == 1
            )
            assert (
                await c.scalar(text("SELECT count(*) FROM external_calls WHERE provider='demo-v1'"))
                == 2
            )
    finally:
        await migrator.dispose()


async def test_probe_does_not_send_after_commit_delay_crosses_deadline(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
    report_transport: ReportTransport,
) -> None:
    import time

    from httpx import Request
    from sqlalchemy import event
    from sqlalchemy.engine import Connection

    from task_platform.db import create_engine

    from .conftest import settings_for

    await dossier(client, command_owner, admin_engine)
    worker = analysis(executor)
    await model_turn(worker, REPORT)
    lease = await worker.claim("save")
    assert lease
    payload = await worker.report_payload(lease)
    assert payload
    call = uuid4()
    assert await worker.authorize(lease, call, payload)
    assert await worker.complete(lease, call, "unknown")
    migrator = create_engine(settings_for("migrator"))
    requests: list[str] = []

    async def observe(request: Request) -> None:
        requests.append(request.method)

    report_transport.client.event_hooks["request"].append(observe)
    bind = executor.factory.kw["bind"]
    delayed = False

    def delay_commit(connection: Connection) -> None:
        nonlocal delayed
        if not delayed:
            delayed = True
            time.sleep(0.3)

    try:
        async with migrator.begin() as c:
            await c.execute(
                text("UPDATE tasks SET deadline_at=clock_timestamp()+interval '200 milliseconds'")
            )
        event.listen(bind.sync_engine, "commit", delay_commit)
        assert await worker.recover_reports(report_transport)
        assert requests == []
    finally:
        event.remove(bind.sync_engine, "commit", delay_commit)
        await migrator.dispose()
