"""Actual 1000-task reference campaign; never run implicitly in ordinary CI.

Independent worker processes, physical HTTP simulator, real PostgreSQL/Redis,
100 reconnecting SSE sessions and 10 reads/s. No paid-provider network exists.
"""

import asyncio
import json
import os
import signal
import sys
import time
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from httpx import AsyncClient, HTTPError, Limits, Timeout
from sqlalchemy import text

from scripts.load_metrics import corpus_content, percentiles, reference_profile
from task_platform.analysis_worker import AnalysisWorker
from task_platform.corpus import import_document
from task_platform.db import create_engine, sessions
from task_platform.identity import issue_key, provision_principal
from task_platform.report_transport import ReportTransport
from task_platform.task_models import Step
from task_platform.transport import SimulationTransport
from task_platform.workflow import AnalysisExecutor
from tests.integration.conftest import settings_for

ROOT = Path("/drill")


class LoadExecutor(AnalysisExecutor):
    def reservation(self, step: Step, payload: bytes) -> tuple[int, int, str, str]:
        if self.call_kind(step) == "demo-v1":
            return 0, 0, "simulated-micro-usd-v1", "simulated-bytes-v1"
        return 1000, 1200, "simulated-micro-usd-v1", "simulated-bytes-v1"


async def worker() -> None:
    settings = settings_for("worker")
    engine, control = create_engine(settings), create_engine(settings, control=True)
    stop = asyncio.Event()
    asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, stop.set)
    key = (await asyncio.to_thread(Path("/run/secrets/demo_service_key").read_text)).strip()
    try:
        async with SimulationTransport("http://127.0.0.1:18080") as model:
            reports = ReportTransport("http://demo:8000", key)
            try:
                await AnalysisWorker(
                    LoadExecutor(sessions(engine), sessions(control), settings.execution_epoch),
                    model,
                    reports,
                    holder=f"load-{os.getpid()}",
                ).run(stop)
            finally:
                await reports.client.aclose()
    finally:
        await engine.dispose()
        await control.dispose()


async def campaign() -> dict[str, Any]:
    assert os.environ.get("ATP_LOAD_PROFILE") == "1", "isolated host coordinator required"
    profile = reference_profile()
    settings = settings_for("admin")
    engine = create_engine(settings)
    owners: list[dict[str, str]] = []
    documents: list[list[dict[str, object]]] = []
    tasks: list[list[str]] = [[] for _ in range(20)]
    accepted: dict[str, float] = {}
    admissions: list[float] = []
    reads: list[float] = []
    rejection: dict[str, int] = {}
    errors: list[str] = []
    observations: list[tuple[str, int, float, str, float]] = []
    stream_sessions = [0] * 100
    connected: set[int] = set()
    active = 0
    peak = 0
    workers_alive = True
    all_connected = asyncio.Event()
    stop = asyncio.Event()
    children: list[asyncio.subprocess.Process] = []
    subscriptions: list[asyncio.Task[None]] = []
    measured = False
    async with AsyncClient(
        trust_env=False,
        timeout=Timeout(15, read=310),
        limits=Limits(max_connections=150, max_keepalive_connections=30),
    ) as client:
        try:
            factory = sessions(engine)
            for index in range(20):
                owner = await provision_principal(factory, f"Load synthetic {index}")
                _, token = await issue_key(factory, owner)
                owners.append(
                    {
                        "Authorization": "Bearer " + token,
                        "X-Execution-Epoch": str(settings.execution_epoch),
                    }
                )
                refs: list[dict[str, object]] = []
                for document_index in range(20):
                    doc = uuid4()
                    content = corpus_content(document_index)
                    await import_document(factory, owner, doc, content)
                    refs.append({"id": str(doc), "version": 1})
                documents.append(refs)

            def save_owners() -> None:
                private = ROOT / "owners.json"
                private.write_text(json.dumps(owners))
                private.chmod(0o600)

            await asyncio.to_thread(save_owners)
            async with engine.begin() as c:
                await c.execute(
                    text("UPDATE platform_control SET admission_open=true,dispatch_open=true")
                )
                await c.execute(
                    text(
                        "UPDATE provider_admission SET requests_per_minute=600,"
                        "tokens_per_minute=1000000"
                    )
                )
            for args in [
                ("tests.support.load_http",),
                ("tests.support.load_profile", "worker"),
                ("tests.support.load_profile", "worker"),
            ]:
                children.append(
                    await asyncio.create_subprocess_exec(
                        sys.executable,
                        "-m",
                        *args,
                        stdout=asyncio.subprocess.DEVNULL,
                        stderr=None,
                    )
                )
            # Read-only readiness, never an automatic paid or business resend.
            async with asyncio.timeout(15):
                while True:
                    try:
                        await client.get("http://127.0.0.1:18080/openapi.json")
                        break
                    except HTTPError:
                        await asyncio.sleep(0.1)

            async def submit(index: int, key: str, record: bool) -> None:
                started = time.monotonic()
                response = await client.post(
                    "http://api:8000/v1/tasks",
                    headers={**owners[index], "Idempotency-Key": key},
                    json={
                        "workflow": {"name": "analysis", "version": "1"},
                        "input": {
                            "texts": [
                                "Analyze revenue. " + "x" * (4096 - len("Analyze revenue. "))
                            ],
                            "documents": documents[index],
                        },
                    },
                )
                if record:
                    admissions.append(time.monotonic() - started)
                if response.status_code == 202:
                    task = response.json()["task_id"]
                    tasks[index].append(task)
                    if record:
                        accepted[task] = started
                elif record:
                    code = str(response.status_code)
                    rejection[code] = rejection.get(code, 0) + 1

            # Separate 60-second warmup, one creation per second, excluded from nominal metrics.
            warm = time.monotonic()
            for index in range(60):
                await asyncio.sleep(max(0, warm + index - time.monotonic()))
                await submit(index % 20, f"warm-{index}", False)

            async def subscribe(number: int) -> None:
                nonlocal active, peak
                owner = number // 5
                lane = "direct" if number % 2 == 0 else "proxy"
                url = "http://api:8000" if lane == "direct" else "http://proxy:8080"
                while not stop.is_set():
                    if not tasks[owner]:
                        await asyncio.sleep(0.1)
                        continue
                    task = tasks[owner][-1]
                    stream_sessions[number] += 1
                    opened = time.time()
                    try:
                        async with client.stream(
                            "GET", f"{url}/v1/tasks/{task}/events", headers=owners[owner]
                        ) as response:
                            if response.status_code != 200:
                                errors.append(f"sse:{response.status_code}")
                                await asyncio.sleep(1)
                                continue
                            connected.add(number)
                            active += 1
                            peak = max(peak, active)
                            if active == 100:
                                all_connected.set()
                            try:
                                async for line in response.aiter_lines():
                                    if line.startswith("id: ") and measured:
                                        identifier, seq = line[4:].rsplit(":", 1)
                                        observations.append(
                                            (identifier, int(seq), time.time(), lane, opened)
                                        )
                                    if stop.is_set():
                                        return
                            finally:
                                active -= 1
                    except HTTPError:
                        if not stop.is_set():
                            errors.append("sse:transport")
                    await asyncio.sleep(0.1)

            async def read_loop() -> None:
                index = 0
                started = time.monotonic()
                while not stop.is_set():
                    await asyncio.sleep(max(0, started + index / 10 - time.monotonic()))
                    owner = index % 20
                    before = time.monotonic()
                    response = await client.get(
                        "http://api:8000/v1/tasks?limit=10", headers=owners[owner]
                    )
                    if measured:
                        reads.append(time.monotonic() - before)
                        if response.status_code != 200:
                            errors.append(f"read:{response.status_code}")
                    index += 1

            subscriptions = [asyncio.create_task(subscribe(n)) for n in range(100)]
            subscriptions.append(asyncio.create_task(read_loop()))
            async with asyncio.timeout(20):
                await all_connected.wait()
            measured = True
            started = time.monotonic()

            def pressure_alive() -> None:
                nonlocal workers_alive
                if any(child.returncode is not None for child in children):
                    workers_alive = False
                    errors.append("benchmark child exited")
                for subscription in subscriptions:
                    if subscription.done():
                        exception = subscription.exception()
                        errors.append("pressure task stopped:" + type(exception).__name__)

            for index in range(1000):
                await asyncio.sleep(max(0, started + index - time.monotonic()))
                await submit(index % 20, f"nominal-{index}", True)
                pressure_alive()
                if index % 100 == 0:
                    print(json.dumps({"progress": index, "accepted": len(accepted)}), flush=True)
            draining = time.monotonic()
            while time.monotonic() - draining < 900:
                pressure_alive()
                async with engine.connect() as c:
                    pending = await c.scalar(
                        text("SELECT count(*) FROM tasks WHERE state IN ('queued','running')")
                    )
                if not pending:
                    break
                await asyncio.sleep(1)
            pressure_alive()
            measured_end = time.monotonic()
            stop.set()
            async with engine.connect() as c:
                rows = (
                    await c.execute(
                        text(
                            "SELECT id,state,extract(epoch FROM created_at)::float8 AS created,"
                            "extract(epoch FROM terminal_at)::float8 AS terminal FROM tasks"
                        )
                    )
                ).all()
                events = (
                    await c.execute(
                        text(
                            "SELECT task_id,sequence,extract(epoch FROM "
                            "pg_xact_commit_timestamp(xmin))::float8 AS committed FROM task_events"
                        )
                    )
                ).all()
                queue = (
                    (
                        await c.execute(
                            text(
                                "SELECT extract(epoch FROM "
                                "(min(a.started_at)-t.created_at))::float8 "
                                "FROM tasks t JOIN attempts a ON a.task_id=t.id "
                                "WHERE t.id=ANY(CAST(:ids AS uuid[])) GROUP BY t.id,t.created_at"
                            ),
                            {"ids": list(accepted)},
                        )
                    )
                    .scalars()
                    .all()
                )
            commit = {(str(r.task_id), r.sequence): r.committed for r in events}
            sse: dict[str, list[float]] = {"direct": [], "proxy": []}
            for task, seq, seen, lane, opened in observations:
                stamp = commit.get((task, seq))
                if stamp is not None and opened <= stamp <= seen and task in accepted:
                    sse[lane].append(seen - stamp)
            states: dict[str, int] = {}
            complete: list[float] = []
            for row in rows:
                if str(row.id) in accepted:
                    states[row.state] = states.get(row.state, 0) + 1
                    if row.terminal is not None:
                        complete.append(row.terminal - row.created)
            model_calls = [
                json.loads(line)["call"]
                for line in (ROOT / "model-journal.jsonl").read_text().splitlines()
            ]
            result = {
                "profile": profile,
                "admission_seconds": percentiles(admissions),
                "queue_seconds": percentiles(list(queue)),
                "completion_seconds": percentiles(complete),
                "read_seconds": percentiles(reads),
                "read_count": len(reads),
                "sse_commit_seconds": {lane: percentiles(values) for lane, values in sse.items()},
                "sse_samples": {lane: len(values) for lane, values in sse.items()},
                "sse_sessions": sum(stream_sessions),
                "sse_connected_slots": len(connected),
                "sse_peak": peak,
                "workers_alive": workers_alive,
                "rejections": rejection,
                "errors": errors,
                "states": states,
                "accepted": len(accepted),
                "model_physical_requests": len(model_calls),
                "distinct_model_calls": len(set(model_calls)),
                "elapsed_seconds": round(measured_end - started, 3),
                "completed_at": datetime.now(UTC).isoformat(),
            }
            (ROOT / "load-result.json").write_text(json.dumps(result, sort_keys=True))
            return result
        finally:
            stop.set()
            for subscription in subscriptions:
                subscription.cancel()
            await asyncio.gather(*subscriptions, return_exceptions=True)
            for child in children:
                with suppress(ProcessLookupError):
                    child.terminate()
            for child in children:
                try:
                    await asyncio.wait_for(child.wait(), 10)
                except TimeoutError:
                    child.kill()
                    await child.wait()
            await engine.dispose()


async def overload_campaign() -> dict[str, Any]:
    assert os.environ.get("ATP_LOAD_PROFILE") == "1"
    owners = json.loads(await asyncio.to_thread((ROOT / "owners.json").read_text))
    engine = create_engine(settings_for("admin"))
    try:
        # A distinct saturation window, with no truncation/reset of durable calls.
        # Let the nominal provider window expire naturally before tiny limits.
        await asyncio.sleep(30.5)
        await asyncio.sleep(30.5)
        before_calls = len(
            (await asyncio.to_thread((ROOT / "model-journal.jsonl").read_text)).splitlines()
        )
        async with AsyncClient(
            trust_env=False, timeout=15, limits=Limits(max_connections=125)
        ) as client:
            started = time.monotonic()
            responses = await asyncio.gather(
                *(
                    client.post(
                        "http://api:8000/v1/tasks",
                        headers={**owners[index % 20], "Idempotency-Key": f"overload-{index}"},
                        json={
                            "workflow": {"name": "analysis", "version": "1"},
                            "input": {"texts": ["Synthetic overload, workers stopped."]},
                            "limits": {"deadline_seconds": 6},
                        },
                    )
                    for index in range(120)
                )
            )
            codes: dict[str, int] = {}
            for response in responses:
                code = str(response.status_code)
                codes[code] = codes.get(code, 0) + 1
            async with engine.connect() as c:
                opened = int(await c.scalar(text("SELECT open_tasks FROM platform_control")) or 0)
                executable = int(
                    await c.scalar(
                        text("SELECT count(*) FROM tasks WHERE state IN ('queued','running')")
                    )
                    or 0
                )
            assert all(r.status_code in (202, 429, 503) for r in responses)
            assert codes.get("202", 0) <= 100 and sum(r.status_code != 202 for r in responses) >= 20
            async with engine.connect() as c:
                nonterminal = int(
                    await c.scalar(
                        text(
                            "SELECT count(*) FROM tasks WHERE state NOT IN "
                            "('succeeded','failed','cancelled','timed_out')"
                        )
                    )
                    or 0
                )
            assert opened == nonterminal <= 100
            assert executable <= opened
            async with engine.begin() as c:
                await c.execute(
                    text(
                        "UPDATE provider_admission SET requests_per_minute=1,"
                        "tokens_per_minute=1200,max_active=1"
                    )
                )
            children: list[asyncio.subprocess.Process] = []
            try:
                for args in [
                    ("tests.support.load_http",),
                    ("tests.support.load_profile", "worker"),
                    ("tests.support.load_profile", "worker"),
                ]:
                    children.append(
                        await asyncio.create_subprocess_exec(
                            sys.executable,
                            "-m",
                            *args,
                            env={**os.environ, "ATP_LOAD_SLOW": "1"},
                            stdout=asyncio.subprocess.DEVNULL,
                            stderr=None,
                        )
                    )
                await asyncio.sleep(9)
                assert all(child.returncode is None for child in children)
                async with engine.connect() as c:
                    remaining = int(
                        await c.scalar(
                            text("SELECT count(*) FROM tasks WHERE state IN ('queued','running')")
                        )
                        or 0
                    )
                    exposure = int(
                        await c.scalar(
                            text("SELECT coalesce(sum(exposure_cost),0) FROM call_costs")
                        )
                        or 0
                    )
                assert remaining == 0
                assert exposure <= 1000
                after_calls = len(
                    (await asyncio.to_thread((ROOT / "model-journal.jsonl").read_text)).splitlines()
                )
                assert after_calls - before_calls == 1
            finally:
                for child in children:
                    with suppress(ProcessLookupError):
                        child.terminate()
                for child in children:
                    try:
                        await asyncio.wait_for(child.wait(), 10)
                    except TimeoutError:
                        child.kill()
                        await child.wait()
            return {
                "requests": 120,
                "statuses": codes,
                "open_tasks": opened,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "bounded": True,
                "slow_timeout_remaining_executable": remaining,
                "slow_timeout_retained_exposure_micro_usd": exposure,
                "slow_physical_requests": after_calls - before_calls,
                "provider_window_idle_seconds": 61,
                "provider_requests_per_minute": 1,
                "provider_tokens_per_minute": 1200,
            }
    finally:
        await engine.dispose()


if __name__ == "__main__":
    if sys.argv[1:] == ["worker"]:
        asyncio.run(worker())
    elif sys.argv[1:] == ["overload"]:
        print(json.dumps(asyncio.run(overload_campaign()), sort_keys=True))
    else:
        print(json.dumps(asyncio.run(campaign()), sort_keys=True))
