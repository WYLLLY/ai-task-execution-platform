"""Real independent demo DB: trusted terminal evidence erases content, never dedup proof."""

import asyncio
import hashlib
import json
import os
import sys
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from task_platform.agent_protocol import encode
from task_platform.execution import Executor
from task_platform.report_transport import ReportTransport

from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch
from .test_workflow_reports import report_transport as report_transport

pytestmark = pytest.mark.integration


@pytest.fixture
async def demo_root() -> AsyncIterator[AsyncEngine]:
    directory = Path(os.environ["TEST_SECRETS_DIR"])
    base = make_url((directory / "demo_database_url").read_text().strip())
    url = base.set(
        username="postgres", password=(directory / "demo_postgres_password").read_text().strip()
    )
    engine = create_async_engine(url, hide_parameters=True)
    try:
        yield engine
    finally:
        await engine.dispose()


async def seed(engine: AsyncEngine) -> tuple[UUID, UUID, bytes, str]:
    owner, operation = uuid4(), uuid4()
    effect = uuid4()
    payload = encode(
        {
            "owner": str(owner),
            "operation": str(operation),
            "report": {
                "type": "report",
                "title": "Synthetic",
                "summary": "synthetic-private-derived-report",
                "citations": [],
            },
        }
    )
    digest = hashlib.sha256(payload).hexdigest()
    async with engine.begin() as c:
        await c.execute(
            text(
                "INSERT INTO report_effects"
                "(owner,operation,effect_id,payload_hash,payload,response) "
                "VALUES(:owner,:operation,:effect,:hash,:payload,:response)"
            ),
            {
                "owner": owner,
                "operation": operation,
                "effect": effect,
                "hash": digest,
                "payload": payload,
                "response": json.dumps(
                    {
                        "owner": str(owner),
                        "operation": str(operation),
                        "effect_id": str(effect),
                        "payload_hash": digest,
                    }
                ),
            },
        )
    return owner, operation, payload, digest


def evidence(owner: UUID, operation: UUID, digest: str, **changes: object) -> dict[str, object]:
    return {
        "evidence_id": "terminal-1",
        "owner": str(owner),
        "operation": str(operation),
        "payload_sha256": digest,
        "terminal_at": (datetime.now(UTC) - timedelta(days=31)).isoformat(),
        "proof_sha256": "a" * 64,
        "reason": "Verified terminal clock from platform records",
        **changes,
    }


async def operator_input(
    value: dict[str, object],
    directory: Path,
    *,
    runtime_role: bool = False,
) -> tuple[Path, dict[str, str]]:
    path = directory / f"evidence-{uuid4()}.json"
    await asyncio.to_thread(path.write_text, json.dumps(value))
    env = dict(os.environ)
    secrets = Path(env["TEST_SECRETS_DIR"])
    env["DATABASE_URL_FILE"] = str(secrets / "admin_database_url")
    url = make_url((secrets / "demo_database_url").read_text().strip())
    if not runtime_role:
        url = url.set(
            username="postgres", password=(secrets / "demo_postgres_password").read_text().strip()
        )
    env["DEMO_RETENTION_DATABASE_URL"] = url.render_as_string(hide_password=False)
    return path, env


async def invoke(
    value: dict[str, object],
    directory: Path,
    *,
    runtime_role: bool = False,
) -> tuple[int, str]:
    path, env = await operator_input(value, directory, runtime_role=runtime_role)
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-c",
        "from task_platform.cli import main; main()",
        "admin",
        "demo-retention",
        "--file",
        str(path),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(), 8)
        return int(process.returncode or 0), stdout.decode()
    finally:
        if process.returncode is None:
            process.terminate()
            await process.wait()


async def test_terminal_evidence_erases_demo_payload_and_replays_after_lost_ack(
    demo_root: AsyncEngine,
    tmp_path: Path,
) -> None:
    owner, operation, payload, digest = await seed(demo_root)
    proof = evidence(owner, operation, digest)
    first = await invoke(proof, tmp_path)
    async with demo_root.connect() as c:
        row = (
            await c.execute(
                text(
                    "SELECT payload,payload_hash,response,effect_id,created_at FROM report_effects "
                    "WHERE owner=:o AND operation=:p"
                ),
                {"o": owner, "p": operation},
            )
        ).one()
    assert row[0] == b"", "terminal evidence must erase the independent demo's sensitive payload"
    assert first[0] == 0 and json.loads(first[1]) == {"purged": True}
    assert row[1] == digest and json.loads(row[2])["payload_hash"] == digest
    assert payload not in row[0]
    # Discard the first acknowledgement, then recover through the same stable evidence.
    assert await invoke(proof, tmp_path) == first
    async with demo_root.connect() as c:
        assert (
            await c.scalar(
                text(
                    "SELECT count(*) FROM demo_retention_decisions WHERE owner=:o AND operation=:p"
                ),
                {"o": owner, "p": operation},
            )
            == 1
        )
        again = (
            await c.execute(
                text(
                    "SELECT payload_hash,response,effect_id,created_at FROM report_effects "
                    "WHERE owner=:o AND operation=:p"
                ),
                {"o": owner, "p": operation},
            )
        ).one()
        assert tuple(again) == tuple(row[1:])
    changed = {**proof, "proof_sha256": "b" * 64}
    assert (await invoke(changed, tmp_path))[0] != 0


@pytest.mark.parametrize(
    "change", ["young", "future", "offset", "naive", "hash", "foreign", "extra"]
)
async def test_unproven_or_young_terminal_evidence_never_erases_content(
    demo_root: AsyncEngine,
    tmp_path: Path,
    change: str,
) -> None:
    owner, operation, payload, digest = await seed(demo_root)
    proof = evidence(owner, operation, digest)
    if change == "young":
        proof["terminal_at"] = (datetime.now(UTC) - timedelta(days=29)).isoformat()
    elif change == "future":
        proof["terminal_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    elif change == "offset":
        proof["terminal_at"] = "2026-01-01T00:00:00+01:00"
    elif change == "naive":
        proof["terminal_at"] = "2026-01-01T00:00:00"
    elif change == "hash":
        proof["payload_sha256"] = "b" * 64
    elif change == "foreign":
        proof["owner"] = str(uuid4())
    else:
        proof["url"] = "http://forbidden"
    assert (await invoke(proof, tmp_path))[0] != 0
    async with demo_root.connect() as c:
        assert (
            await c.scalar(
                text("SELECT payload FROM report_effects WHERE owner=:o AND operation=:p"),
                {"o": owner, "p": operation},
            )
            == payload
        )


async def test_demo_runtime_cannot_erase_or_read_private_retention_audit(
    demo_root: AsyncEngine,
    tmp_path: Path,
) -> None:
    owner, operation, payload, digest = await seed(demo_root)
    assert (await invoke(evidence(owner, operation, digest), tmp_path, runtime_role=True))[0] != 0
    async with demo_root.begin() as c:
        await c.execute(text("SET LOCAL ROLE atp_demo"))
        with pytest.raises(DBAPIError) as denied:
            await c.execute(text("SELECT * FROM demo_retention_decisions"))
        assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    async with demo_root.connect() as c:
        assert (
            await c.scalar(
                text("SELECT payload FROM report_effects WHERE owner=:o AND operation=:p"),
                {"o": owner, "p": operation},
            )
            == payload
        )


async def test_concurrent_demo_erasure_waits_on_real_row_lock_and_audits_once(
    demo_root: AsyncEngine,
    tmp_path: Path,
) -> None:
    owner, operation, _, digest = await seed(demo_root)
    proof = evidence(owner, operation, digest)
    actors: list[asyncio.subprocess.Process] = []
    try:
        for _ in range(2):
            path, env = await operator_input(proof, tmp_path)
            actors.append(
                await asyncio.create_subprocess_exec(
                    sys.executable,
                    "-m",
                    "tests.support.demo_gc_actor",
                    str(path),
                    env=env,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            )
        async with asyncio.timeout(10):
            for actor in actors:
                assert actor.stdout is not None
                assert await actor.stdout.readline() == b"ready\n"
        # Imports/configuration finish before either the SQL lock or observation clock.
        async with demo_root.begin() as locked:
            await locked.execute(
                text("SELECT 1 FROM report_effects WHERE owner=:o AND operation=:p FOR UPDATE"),
                {"o": owner, "p": operation},
            )
            for actor in actors:
                assert actor.stdin is not None
                actor.stdin.write(b"run\n")
                await actor.stdin.drain()
            async with asyncio.timeout(5):
                while True:
                    async with demo_root.connect() as observer:
                        waiting = await observer.scalar(
                            text(
                                "SELECT count(*) FROM pg_stat_activity WHERE datname='demo' "
                                "AND wait_event_type='Lock' "
                                "AND cardinality(pg_blocking_pids(pid))>0 "
                                "AND query ILIKE '%purge_demo_content%'"
                            )
                        )
                    if waiting == 2:
                        break
                    await asyncio.sleep(0.01)
        for actor in actors:
            output, diagnostic = await asyncio.wait_for(actor.communicate(), 8)
            assert actor.returncode == 0, (actor.returncode, diagnostic.decode())
            assert json.loads(output) == {"purged": True}
        async with demo_root.connect() as c:
            assert (
                await c.scalar(
                    text(
                        "SELECT count(*) FROM demo_retention_decisions "
                        "WHERE owner=:o AND operation=:p"
                    ),
                    {"o": owner, "p": operation},
                )
                == 1
            )
    finally:
        for actor in actors:
            if actor.returncode is None:
                actor.terminate()
            await asyncio.wait_for(actor.communicate(), 8)


async def test_limited_demo_admin_can_only_erase_via_audited_function(
    demo_root: AsyncEngine,
) -> None:
    owner, operation, payload, digest = await seed(demo_root)
    proof = evidence(owner, operation, digest)
    async with demo_root.begin() as c:
        await c.execute(text("SET LOCAL ROLE atp_demo_retention"))
        assert (
            await c.scalar(
                text("SELECT public.purge_demo_content(CAST(:p AS jsonb))"),
                {"p": json.dumps(proof)},
            )
            is True
        )
    for query in [
        "UPDATE report_effects SET payload='forbidden'::bytea",
        "DELETE FROM report_effects",
        "INSERT INTO demo_retention_decisions SELECT * FROM demo_retention_decisions",
    ]:
        async with demo_root.begin() as c:
            await c.execute(text("SET LOCAL ROLE atp_demo_retention"))
            with pytest.raises(DBAPIError) as denied:
                await c.execute(text(query))
            assert getattr(denied.value.orig, "sqlstate", None) == "42501"
    async with demo_root.begin() as c:
        with pytest.raises(DBAPIError) as denied:
            await c.execute(
                text("UPDATE report_effects SET payload=:payload WHERE owner=:o AND operation=:p"),
                {"payload": payload, "o": owner, "p": operation},
            )
        assert getattr(denied.value.orig, "sqlstate", None) == "23514"


async def test_erasure_and_real_http_exact_resend_keep_original_dedup_proof(
    demo_root: AsyncEngine,
    tmp_path: Path,
) -> None:
    owner, operation, payload, digest = await seed(demo_root)
    directory = Path(os.environ["TEST_SECRETS_DIR"])
    headers = {
        "Authorization": "Bearer " + (directory / "demo_service_key").read_text().strip(),
        "X-Transmission-ID": str(uuid4()),
    }
    async with httpx.AsyncClient(
        base_url="http://demo:8000", trust_env=False, timeout=3
    ) as network:
        erased, replay = await asyncio.gather(
            invoke(evidence(owner, operation, digest), tmp_path),
            network.post("/reports", content=payload, headers=headers),
        )
        assert erased[0] == 0 and replay.status_code == 200
        original = replay.json()
        headers["X-Transmission-ID"] = str(uuid4())
        again = await network.post("/reports", content=payload, headers=headers)
        assert again.status_code == 200 and again.json() == original
        headers["X-Transmission-ID"] = str(uuid4())
        conflict = await network.post(
            "/reports",
            content=payload.replace(b"synthetic-private-derived-report", b"changed-report"),
            headers=headers,
        )
        assert conflict.status_code == 409
    async with demo_root.connect() as c:
        assert (
            await c.scalar(
                text("SELECT count(*) FROM report_effects WHERE owner=:o AND operation=:p"),
                {"o": owner, "p": operation},
            )
            == 1
        )
        assert (
            await c.scalar(
                text("SELECT payload FROM report_effects WHERE owner=:o AND operation=:p"),
                {"o": owner, "p": operation},
            )
            == b""
        )
        assert (
            await c.scalar(
                text("SELECT count(*) FROM invocation_journal WHERE owner=:o AND operation=:p"),
                {"o": owner, "p": operation},
            )
            == 3
        )


@pytest.mark.parametrize("boundary", ["deadline", "horizon"])
async def test_expired_remote_record_never_causes_a_blind_resend_and_keeps_exposure(
    client: httpx.AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
    report_transport: ReportTransport,
    demo_root: AsyncEngine,
    boundary: str,
) -> None:
    from task_platform.db import create_engine

    from .conftest import settings_for
    from .test_workflow_reports import REPORT
    from .test_workflows import analysis, dossier

    await dossier(client, command_owner, admin_engine)
    worker = analysis(executor)
    model = await worker.claim("usage-absent")
    assert model
    context = await worker.context(model)
    assert context
    model_call = uuid4()
    assert await worker.authorize(model, model_call, context)
    assert await worker.accept_model(model, model_call, REPORT)
    lease = await worker.claim("old-report")
    assert lease
    payload = await worker.report_payload(lease)
    assert payload
    value = json.loads(payload)
    call = uuid4()
    permit = await worker.authorize(lease, call, payload)
    assert permit
    assert await report_transport.send(permit) is None  # Real effect, lost acknowledgement.
    assert await worker.complete(lease, call, "unknown")
    params = {"o": value["owner"], "p": value["operation"]}
    async with demo_root.begin() as c:
        # Test-only age simulation of a remote operator's expired 90-day record.
        await c.execute(text("ALTER TABLE report_effects DISABLE TRIGGER USER"))
        await c.execute(
            text(
                "UPDATE report_effects SET created_at=clock_timestamp()-interval '91 days' "
                "WHERE owner=:o AND operation=:p"
            ),
            params,
        )
        await c.execute(text("ALTER TABLE report_effects ENABLE TRIGGER USER"))
        assert (
            await c.execute(
                text(
                    "DELETE FROM report_effects WHERE owner=:o AND operation=:p "
                    "AND created_at<clock_timestamp()-interval '90 days'"
                ),
                params,
            )
        ).rowcount == 1
        assert (
            await c.scalar(
                text("SELECT count(*) FROM invocation_journal WHERE owner=:o AND operation=:p"),
                params,
            )
            == 1
        )
    migrator = create_engine(settings_for("migrator"))
    try:
        async with migrator.begin() as c:
            if boundary == "horizon":
                await c.execute(text("ALTER TABLE steps DISABLE TRIGGER USER"))
                await c.execute(
                    text(
                        "UPDATE steps SET first_authorized_at="
                        "clock_timestamp()-interval '25 hours' "
                        "WHERE path='analysis/save-report'"
                    )
                )
                await c.execute(text("ALTER TABLE steps ENABLE TRIGGER USER"))
            else:
                await c.execute(text("ALTER TABLE tasks DISABLE TRIGGER USER"))
                await c.execute(
                    text(
                        "UPDATE tasks SET created_at=clock_timestamp()-interval '1 minute',"
                        "deadline_at=clock_timestamp()-interval '1 second'"
                    )
                )
                await c.execute(text("ALTER TABLE tasks ENABLE TRIGGER USER"))
        async with admin_engine.connect() as c:
            before = (
                await c.execute(
                    text("SELECT sum(exposure_cost),sum(exposure_tokens) FROM call_costs")
                )
            ).one()
            assert before[0] > 0 and before[1] > 0
        attempts: list[str] = []

        async def physical(request: httpx.Request) -> None:
            attempts.append(request.method)

        report_transport.client.event_hooks["request"].append(physical)
        assert not await worker.recover_reports(report_transport)
        assert await worker.claim("forbidden-resend") is None
        assert attempts == []
        async with admin_engine.connect() as c:
            assert (
                await c.execute(
                    text("SELECT sum(exposure_cost),sum(exposure_tokens) FROM call_costs")
                )
            ).one() == before
            assert (
                await c.scalar(text("SELECT count(*) FROM external_calls WHERE provider='demo-v1'"))
                == 1
            )
            assert (
                await c.scalar(
                    text("SELECT payload_hash FROM external_calls WHERE id=:id"), {"id": call}
                )
                == hashlib.sha256(payload).hexdigest()
            )
        async with demo_root.connect() as c:
            assert (
                await c.scalar(
                    text("SELECT count(*) FROM invocation_journal WHERE owner=:o AND operation=:p"),
                    params,
                )
                == 1
            )
    finally:
        await migrator.dispose()


async def test_demo_commit_ack_loss_recovers_the_same_durable_decision(
    demo_root: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.pool import PoolProxiedConnection

    from task_platform import demo_retention

    owner, operation, _, digest = await seed(demo_root)
    proof = evidence(owner, operation, digest)
    url = demo_root.url.render_as_string(hide_password=False)
    monkeypatch.setenv("DEMO_RETENTION_DATABASE_URL", url)
    engine = create_async_engine(url, hide_parameters=True)
    original = engine.sync_engine.dialect.do_commit

    def lost(connection: PoolProxiedConnection) -> None:
        original(connection)  # Actual server commit, before losing its local acknowledgement.
        raise OSError("synthetic commit acknowledgement lost")

    with monkeypatch.context() as patch:
        patch.setattr(engine.sync_engine.dialect, "do_commit", lost)
        patch.setattr(demo_retention, "create_async_engine", lambda *args, **kwargs: engine)
        with pytest.raises(OSError, match="synthetic commit acknowledgement lost"):
            await demo_retention.erase_demo_content(proof)
    async with demo_root.connect() as c:
        assert (
            await c.scalar(
                text("SELECT payload FROM report_effects WHERE owner=:o AND operation=:p"),
                {"o": owner, "p": operation},
            )
            == b""
        )
        assert (
            await c.scalar(
                text(
                    "SELECT count(*) FROM demo_retention_decisions WHERE owner=:o AND operation=:p"
                ),
                {"o": owner, "p": operation},
            )
            == 1
        )
    assert await demo_retention.erase_demo_content(proof)


async def test_terminal_retention_is_utc_even_when_operator_session_crosses_dst(
    demo_root: AsyncEngine,
) -> None:
    owner, operation, _, digest = await seed(demo_root)
    async with demo_root.begin() as c:
        now = await c.scalar(text("SELECT clock_timestamp()"))
        assert isinstance(now, datetime)
        # A POSIX timezone with a recent DST end makes this deterministic year-round.
        end = (now.timetuple().tm_yday - 3) % 365
        start = (end - 60) % 365
        zone = f"RSTD0RDST,{start}/0,{end}/0"
        await c.execute(text("SELECT set_config('TimeZone',:zone,true)"), {"zone": zone})
        calendar, elapsed = (
            await c.execute(
                text(
                    "SELECT clock_timestamp()-interval '30 days',"
                    "clock_timestamp()-interval '720 hours'"
                )
            )
        ).one()
        assert 3599 < (elapsed - calendar).total_seconds() < 3601
        proof = evidence(
            owner,
            operation,
            digest,
            terminal_at=(now - timedelta(hours=720, minutes=30)).isoformat(),
        )
        await c.execute(text("SET LOCAL ROLE atp_demo_retention"))
        assert (
            await c.scalar(
                text("SELECT public.purge_demo_content(CAST(:p AS jsonb))"),
                {"p": json.dumps(proof)},
            )
            is True
        )
