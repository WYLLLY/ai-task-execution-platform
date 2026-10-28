"""F19: real Redis container outage, 100 live SSE, 10 reads/s, independent worker DB pool."""

import asyncio
import json
import os
import time
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient, Limits
from redis.asyncio import Redis
from redis.exceptions import RedisError
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import sessions
from task_platform.execution import Executor
from task_platform.identity import issue_key, provision_principal
from task_platform.transport import SimulationTransport
from tests.support.degraded_readiness import wait_for_recovery

from .conftest import settings_for
from .test_commands import BODY
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch
from .test_execution_transport import Simulator
from .test_execution_transport import simulator as simulator
from .test_sse_network import publish_event

pytestmark = pytest.mark.integration


async def wait_file(path: Path, seconds: float) -> None:
    async with asyncio.timeout(seconds):
        for _ in range(int(seconds * 20) + 1):
            if await asyncio.to_thread(path.exists):
                return
            await asyncio.sleep(0.05)
        raise TimeoutError


async def test_f19_real_redis_outage_with_100_sse_and_sustained_reads(
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
    simulator: Simulator,
) -> None:
    root = os.environ.get("ATP_FAULT_COORDINATOR")
    assert root, "run this gate through scripts/run_verification.py; real fault controller required"
    marker = Path(root)
    settings = settings_for("api")
    assert settings.redis_url is not None
    redis = Redis.from_url(
        settings.redis_url.get_secret_value(), socket_connect_timeout=1, socket_timeout=1
    )
    ready: asyncio.Queue[None] = asyncio.Queue(maxsize=100)
    progress = asyncio.Condition()
    observed: list[int] = [0] * 100
    task_ids: list[UUID] = []
    owners: list[dict[str, str]] = []
    streams: list[asyncio.Task[None]] = []
    readers: list[asyncio.Task[None]] = []
    heartbeat: asyncio.Task[bool] | None = None
    latencies: list[float] = []
    try:
        async with AsyncClient(
            base_url="http://api:8000",
            trust_env=False,
            timeout=15,
            limits=Limits(max_connections=150, max_keepalive_connections=30),
        ) as network:
            for index in range(20):
                owner = await provision_principal(sessions(admin_engine), f"F19 synthetic {index}")
                _, key = await issue_key(sessions(admin_engine), owner)
                headers = {
                    "Authorization": f"Bearer {key}",
                    "X-Execution-Epoch": command_owner["X-Execution-Epoch"],
                }
                response = await network.post(
                    "/v1/tasks", json=BODY, headers={**headers, "Idempotency-Key": "f19-create"}
                )
                assert response.status_code == 202, response.text
                owners.append(headers)
                task_ids.append(UUID(response.json()["task_id"]))

            async def subscribe(index: int) -> None:
                task = task_ids[index // 5]
                expected = 1
                async with network.stream(
                    "GET", f"/v1/tasks/{task}/events", headers=owners[index // 5]
                ) as response:
                    assert response.status_code == 200
                    async for line in response.aiter_lines():
                        if line.startswith("id: "):
                            assert line == f"id: {task}:{expected}", "gap or cross-owner event"
                            observed[index] = expected
                            async with progress:
                                progress.notify_all()
                            if expected == 1:
                                ready.put_nowait(None)
                            expected += 1
                        assert line != "event: stream.reset"

            streams = [asyncio.create_task(subscribe(index)) for index in range(100)]
            async with asyncio.timeout(25):
                for _ in range(100):
                    await ready.get()
            # Both admission caps are also exercised deterministically by unit tests.
            rejected = await network.get(f"/v1/tasks/{task_ids[0]}/events", headers=owners[0])
            assert rejected.status_code == 429
            lease = await executor.claim("f19-independent-worker")
            assert lease is not None
            assert await executor.heartbeat(lease)

            async def renew() -> bool:
                await asyncio.sleep(10)  # Preserve the actual plan heartbeat interval.
                return await executor.heartbeat(lease)

            await asyncio.to_thread((marker / "ready").touch)
            await wait_file(marker / "stopped", 15)
            with pytest.raises((RedisError, OSError, TimeoutError)):
                await redis.ping()
            started = time.monotonic()
            heartbeat = asyncio.create_task(renew())

            async def read(index: int) -> None:
                await asyncio.sleep(max(0, started + index / 10 - time.monotonic()))
                before = time.monotonic()
                which = index % 20
                response = await network.get(f"/v1/tasks/{task_ids[which]}", headers=owners[which])
                assert response.status_code == 200
                latencies.append(time.monotonic() - before)

            readers = [asyncio.create_task(read(index)) for index in range(120)]
            fresh = await network.post(
                "/v1/tasks", json=BODY, headers={**owners[0], "Idempotency-Key": "unavailable"}
            )
            assert fresh.status_code == 503
            replay = await network.post(
                "/v1/tasks", json=BODY, headers={**owners[0], "Idempotency-Key": "f19-create"}
            )
            assert replay.status_code == 202 and UUID(replay.json()["task_id"]) == task_ids[0]
            snapshot = (await network.get(f"/v1/tasks/{task_ids[1]}", headers=owners[1])).json()
            cancel = await network.post(
                f"/v1/tasks/{task_ids[1]}/cancel",
                json={"expected_task_version": snapshot["task_version"]},
                headers={**owners[1], "Idempotency-Key": "f19-cancel"},
            )
            assert cancel.status_code == 200
            sequence = await publish_event(admin_engine, task_ids[0])
            async with asyncio.timeout(3), progress:
                await progress.wait_for(lambda: all(value >= sequence for value in observed[:5]))
            await asyncio.gather(*readers)
            assert await heartbeat
            # PostgreSQL can authorize and commit work while every Redis request fails.
            permit = await executor.authorize(lease, uuid4(), b"{}")
            assert permit
            async with SimulationTransport(simulator.url) as transport:
                outcome = await transport.send(permit)
            assert outcome.outcome == "succeeded"
            assert await executor.complete(
                lease, permit.call_id, outcome.outcome, cost=outcome.cost, tokens=outcome.tokens
            )
            assert simulator.count("request") == simulator.count("effect") == 1
            assert all(not stream.done() for stream in streams)
            metric = (await network.get("/metrics")).text
            assert "atp_sse_connections 100.0" in metric
            assert "atp_sse_followed_tasks 20.0" in metric
            ready_state = (await network.get("/health/ready")).json()
            assert ready_state["postgres"] and not ready_state["submission_available"]
            assert len(latencies) == 120
            (marker / "results.json").write_text(
                json.dumps(
                    {
                        "streams": 100,
                        "followed_tasks": 20,
                        "reads": len(latencies),
                        "scheduled_reads_per_second": 10,
                        "duration_seconds": round(time.monotonic() - started, 3),
                        "read_p95_seconds": round(sorted(latencies)[113], 4),
                        "new_creation": 503,
                        "replay": 202,
                        "control": 200,
                        "heartbeat_interval_seconds": 10,
                        "heartbeat_renewed": True,
                        "stream_errors": 0,
                        "physical_requests": 1,
                        "remote_effects": 1,
                        "committed_execution": True,
                    }
                )
            )
            await asyncio.to_thread((marker / "resume").touch)
            await wait_file(marker / "recovered", 20)

            async def ping() -> bool:
                return bool(await redis.ping())

            await wait_for_recovery(ping, network)
            accepted = await network.post(
                "/v1/tasks", json=BODY, headers={**owners[0], "Idempotency-Key": "after-recovery"}
            )
            assert accepted.status_code == 202
    finally:
        try:
            await asyncio.to_thread((marker / "resume").touch)
        finally:
            children = [*streams, *readers] + ([heartbeat] if heartbeat is not None else [])
            for child in children:
                child.cancel()
            await asyncio.gather(*children, return_exceptions=True)
            await redis.aclose()
