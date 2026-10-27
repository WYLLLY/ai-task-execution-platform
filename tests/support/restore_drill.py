"""Synthetic F21 fixture driver; only run in the isolated host-controlled restore project.

The private /drill state is not an application acceptance journal and is never
restored with PostgreSQL. It is the external test oracle/client receipt.
"""

import argparse
import asyncio
import json
import os
import socket
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from httpx import ASGITransport, AsyncClient, ConnectError
from pydantic import SecretStr
from sqlalchemy import make_url, text
from sqlalchemy.exc import OperationalError

from task_platform.analysis_worker import AnalysisWorker
from task_platform.api import create_app
from task_platform.config import Role, Settings
from task_platform.corpus import import_document
from task_platform.db import create_engine, sessions
from task_platform.execution import Executor, Lease
from task_platform.health import check_database
from task_platform.identity import initialize_platform, issue_key, provision_principal
from task_platform.recovery import begin_restore, reopen, revise_reserve
from task_platform.report_transport import ReportTransport
from task_platform.transport import SimulationTransport
from task_platform.workflow import AnalysisExecutor
from tests.integration.conftest import settings_for

ROOT = Path("/drill")
STATE = ROOT / "private-state.json"


def read_state() -> dict[str, Any]:
    return dict(json.loads(STATE.read_text())) if STATE.exists() else {}


def write_state(value: dict[str, Any]) -> None:
    STATE.write_text(json.dumps(value, default=str))
    STATE.chmod(0o600)


def settings(role: Role, database: str = "atp", *, rotated: bool = True) -> Settings:
    base = settings_for(role)
    url = make_url(base.database_url.get_secret_value()).set(database=database)
    if role == "worker" and rotated and database != "atp":
        url = url.set(password=read_state()["worker_password"])
    return base.model_copy(
        update={"database_url": SecretStr(url.render_as_string(hide_password=False))}
    )


def body(state: dict[str, Any]) -> dict[str, object]:
    return {
        "workflow": {"name": "analysis", "version": "1"},
        "input": {
            "texts": ["Synthetic disaster dossier"],
            "documents": [{"id": state["document"], "version": 1}],
        },
    }


@asynccontextmanager
async def reports(key: str) -> AsyncIterator[ReportTransport]:
    transport = ReportTransport("http://demo:8000", key)
    try:
        yield transport
    finally:
        await transport.close()


async def baseline() -> None:
    engine = create_engine(settings("admin"))
    try:
        factory = sessions(engine)
        await initialize_platform(factory, settings("admin").execution_epoch)
        owner = await provision_principal(factory, "Isolated disaster owner")
        key, token = await issue_key(factory, owner)
        document = uuid4()
        await import_document(factory, owner, document, "Revenue is 42. Synthetic only.")
        async with engine.begin() as c:
            await c.execute(
                text("UPDATE platform_control SET admission_open=true,dispatch_open=true")
            )
        write_state(
            {
                "owner": owner,
                "key": key,
                "token": token,
                "document": document,
                "original_epoch": settings("admin").execution_epoch,
                "old_demo_key": (
                    await asyncio.to_thread(Path("/run/secrets/demo_service_key").read_text)
                ).strip(),
            }
        )
    finally:
        await engine.dispose()


async def prepare() -> None:
    state = read_state()
    api = create_app(settings("api"))
    worker, control = (
        create_engine(settings("worker")),
        create_engine(settings("worker"), control=True),
    )
    executor = AnalysisExecutor(
        sessions(worker), sessions(control), settings("worker").execution_epoch
    )
    simulator = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "tests.support.simulator",
        "--journal",
        str(ROOT / "physical-model.jsonl"),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.DEVNULL,
    )
    assert simulator.stdout
    try:
        port = int(await asyncio.wait_for(simulator.stdout.readline(), 5))
        async with (
            api.router.lifespan_context(api),
            AsyncClient(transport=ASGITransport(app=api), base_url="http://fixture") as client,
            SimulationTransport(f"http://127.0.0.1:{port}") as model,
            reports(state["old_demo_key"]) as report_client,
        ):
            accepted = await client.post(
                "/v1/tasks",
                json=body(state),
                headers={
                    "Authorization": "Bearer " + state["token"],
                    "X-Execution-Epoch": state["original_epoch"],
                    "Idempotency-Key": "accepted-before-loss",
                },
            )
            assert accepted.status_code == 202, "synthetic acceptance failed"
            state["task"] = accepted.json()["task_id"]
            runtime = AnalysisWorker(executor, model, report_client, holder="before-backup")
            for _ in range(3):
                assert await runtime.run_once()
            lease = await executor.claim("report-before-B")
            assert lease
            payload = await executor.report_payload(lease)
            assert payload
            # The operation becomes durable in B; this earlier client oracle
            # captures the deterministic payload without fabricating a B row.
            state["operation"] = json.loads(payload)["operation"]
            async with worker.connect() as c:
                assert (
                    await c.scalar(
                        text("SELECT count(*) FROM external_calls WHERE provider='demo-v1'")
                    )
                    == 0
                )
            state["lease"] = asdict(lease)
            write_state(state)
    finally:
        simulator.terminate()
        await asyncio.wait_for(simulator.wait(), 5)
        await worker.dispose()
        await control.dispose()


async def effect() -> None:
    state = read_state()
    item = state["lease"]
    lease = Lease(
        task_id=UUID(item["task_id"]),
        step_id=UUID(item["step_id"]),
        attempt_id=UUID(item["attempt_id"]),
        holder=item["holder"],
        generation=item["generation"],
        epoch=UUID(item["epoch"]),
    )
    worker, control = (
        create_engine(settings("worker")),
        create_engine(settings("worker"), control=True),
    )
    executor = AnalysisExecutor(
        sessions(worker), sessions(control), settings("worker").execution_epoch
    )
    try:
        payload = await executor.report_payload(lease)
        assert payload
        permit = await executor.authorize(lease, uuid4(), payload)
        assert permit
        async with reports(state["old_demo_key"]) as report_client:
            assert await report_client.send(permit)
        # Deliberately no C: an independent remote effect survives both restores.
        async with worker.connect() as c:
            assert (
                await c.scalar(text("SELECT state FROM external_calls WHERE provider='demo-v1'"))
                == "authorized"
            )
    finally:
        await worker.dispose()
        await control.dispose()


async def check(database: str, point: str, incident: str) -> None:
    state = read_state()
    epoch = settings("admin").execution_epoch
    admin = create_engine(settings("admin", database))
    worker = create_engine(settings("worker", database))
    control = create_engine(settings("worker", database), control=True)
    old_worker = create_engine(settings("worker", database, rotated=False))
    started = datetime.now(UTC)
    assert not settings("worker", database).dispatch_enabled
    assert "OPENAI_API_KEY" not in os.environ and "OPENAI_API_KEY_FILE" not in os.environ
    try:
        try:
            async with old_worker.connect() as c:
                await c.execute(text("SELECT 1"))
        except OperationalError as error:
            assert "password authentication failed" in str(error.orig)
        else:
            raise AssertionError("old worker credentials still usable")
        try:
            connection = socket.create_connection(("1.1.1.1", 443), timeout=1)
        except OSError:
            pass
        else:
            connection.close()
            raise AssertionError("live network egress is open")
        factory = sessions(admin)
        evidence = {
            "evidence_id": "isolated-restore",
            "proof_sha256": state["isolation_proof"],
            "restored_at": point,
            "incident_at": incident,
            "old_workers_isolated": True,
            "credentials_revoked": True,
            "live_egress_blocked": True,
        }
        result = await begin_restore(factory, epoch, evidence)
        assert result["quarantined"] == (1 if database.endswith("before_b") else 0)
        executor = Executor(sessions(worker), sessions(control), epoch)
        assert await executor.claim("restored-worker") is None
        async with admin.connect() as c:
            assert await c.scalar(text("SELECT count(*) FROM attempts WHERE state='active'")) == 0
            assert (
                await c.scalar(text("SELECT count(*) FROM external_calls WHERE state='authorized'"))
                == 0
            )
            present = await c.scalar(
                text("SELECT count(*) FROM tasks WHERE id=:id"), {"id": state["task"]}
            )
            assert present == (1 if database.endswith("before_b") else 0)
        assert (await check_database(control, settings("worker", database)))[0:2] == (True, True)
        _, token = await issue_key(factory, UUID(state["owner"]))
        api = create_app(settings("api", database))
        async with (
            api.router.lifespan_context(api),
            AsyncClient(transport=ASGITransport(app=api), base_url="http://restored") as client,
            AsyncClient(trust_env=False, timeout=3) as remote,
        ):
            assert (
                await client.get("/v1/me", headers={"Authorization": "Bearer " + state["token"]})
            ).status_code == 401
            headers = {
                "Authorization": "Bearer " + token,
                "Idempotency-Key": "accepted-before-loss",
            }
            for supplied, code in ((None, 428), (state["original_epoch"], 409)):
                request_headers = {**headers, "Idempotency-Key": "fresh-epoch-guard"}
                if supplied:
                    request_headers["X-Execution-Epoch"] = supplied
                assert (
                    await client.post("/v1/tasks", json=body(state), headers=request_headers)
                ).status_code == code
            if present:
                retained = await client.post("/v1/tasks", json=body(state), headers=headers)
                assert retained.status_code == 202 and retained.json()["task_id"] == state["task"]
            system = (await client.get("/v1/system")).json()
            assert system["recovery"]["acceptance_history_may_be_lost"] is True
            # Recreating the independent HTTP process is asynchronous. Only poll
            # a read-only lookup; never resend its physical write for readiness.
            async with asyncio.timeout(20):
                while True:
                    try:
                        await remote.get("http://demo:8000/operations/invalid/invalid")
                        break
                    except OSError, ConnectError:
                        await asyncio.sleep(0.1)
            # The old service key is actually rejected after rotation.
            assert (
                await remote.get(
                    f"http://demo:8000/operations/{state['owner']}/{state['operation']}",
                    headers={"Authorization": "Bearer " + state["old_demo_key"]},
                )
            ).status_code == 401
            current_demo = (
                await asyncio.to_thread(Path("/run/secrets/demo_service_key").read_text)
            ).strip()
            lookup = await remote.get(
                f"http://demo:8000/operations/{state['owner']}/{state['operation']}",
                headers={"Authorization": "Bearer " + current_demo},
            )
            assert lookup.status_code == 200
            # No fake B/call is created for history absent from the restored snapshot.
            lost = database.endswith("before_creation")
            await revise_reserve(
                factory,
                epoch,
                {
                    "evidence_id": "bounded-loss",
                    "revision": 0,
                    "proof_sha256": state["isolation_proof"],
                    "cost": 40 if lost else 0,
                    "tokens": 20 if lost else 0,
                    "owners": [
                        {
                            "principal_id": state["owner"],
                            "cost": 40 if lost else 0,
                            "tokens": 20 if lost else 0,
                        }
                    ],
                    "reason": "Synthetic independent invocation/effect records bound missing usage",
                },
            )
            await reopen(
                factory,
                epoch,
                {"evidence_id": "safe-open", "proof_sha256": state["isolation_proof"]},
            )
            if present:
                old = (await client.get(f"/v1/tasks/{state['task']}", headers=headers)).json()
                denied = await client.post(
                    f"/v1/tasks/{state['task']}/resolve",
                    headers={
                        **headers,
                        "X-Execution-Epoch": str(epoch),
                        "Idempotency-Key": "no-blind-resume",
                    },
                    json={"expected_task_version": old["task_version"], "action": "resume"},
                )
                assert denied.status_code == 409
                cancelled = await client.post(
                    f"/v1/tasks/{state['task']}/cancel",
                    headers={**headers, "Idempotency-Key": "abandon-uncertain"},
                    json={"expected_task_version": old["task_version"]},
                )
                assert cancelled.status_code == 200
                async with admin.connect() as c:
                    assert (
                        await c.scalar(
                            text("SELECT restore_uncertain FROM tasks WHERE id=:id"),
                            {"id": state["task"]},
                        )
                        is True
                    )
            new = await client.post(
                "/v1/tasks",
                json=body(state),
                headers={
                    **headers,
                    "X-Execution-Epoch": str(epoch),
                    "Idempotency-Key": "explicit-new-synthetic-decision",
                },
            )
            assert new.status_code == 202 and new.json()["task_id"] != state["task"]
        print(
            json.dumps(
                {
                    "database": database,
                    "quarantined": result["quarantined"],
                    "lost_accepted_task": not bool(present),
                    "old_worker_rejected": True,
                    "old_api_key_rejected": True,
                    "old_demo_key_rejected": True,
                    "live_egress_blocked": True,
                    "blind_resume": False,
                    "safe_reopen_verified": True,
                    "verification_seconds": (datetime.now(UTC) - started).total_seconds(),
                }
            )
        )
    finally:
        await old_worker.dispose()
        await worker.dispose()
        await control.dispose()
        await admin.dispose()


def main() -> None:
    if os.environ.get("ATP_RESTORE_DRILL") != "1":
        raise SystemExit("Isolated restore profile required")
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=["baseline", "prepare", "effect", "check"])
    parser.add_argument(
        "--database", choices=["atp_restore_before_b", "atp_restore_before_creation"]
    )
    parser.add_argument("--point")
    parser.add_argument("--incident")
    args = parser.parse_args()
    if args.phase == "check":
        if not args.database or not args.point or not args.incident:
            raise SystemExit("Restore metadata required")
        asyncio.run(check(args.database, args.point, args.incident))
    else:
        asyncio.run({"baseline": baseline, "prepare": prepare, "effect": effect}[args.phase]())


if __name__ == "__main__":
    main()
