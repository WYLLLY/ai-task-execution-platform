"""Fixture orchestration: slow HTTP readiness must precede durable worker sends."""

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
from pydantic import SecretStr

from task_platform.config import Settings


async def test_saturation_waits_for_model_before_burst_and_workers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import tests.support.load_profile as load

    events: list[str] = []
    state = {"posts": 0, "workers": 0, "ready": False}
    await asyncio.to_thread((tmp_path / "model-journal.jsonl").write_text, "")
    monkeypatch.setenv("ATP_LOAD_PROFILE", "1")
    monkeypatch.setattr(load, "ROOT", tmp_path)
    config = Settings(
        role="admin",
        database_url=SecretStr("postgresql+psycopg://u:p@unused/db"),
        execution_epoch=UUID(int=1),
    )
    monkeypatch.setattr(load, "settings_for", lambda _: config)

    class Connection:
        async def __aenter__(self) -> Connection:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def execute(self, *args: object) -> None:
            pass

        async def scalar(self, statement: object) -> int:
            query = str(statement)
            if "sum(exposure_cost)" in query:
                return 1000
            if "('queued','running')" in query and state["workers"]:
                return 0
            return 100

    class Engine:
        def connect(self) -> Connection:
            return Connection()

        def begin(self) -> Connection:
            return Connection()

        async def dispose(self) -> None:
            pass

    class Response:
        def __init__(self, code: int) -> None:
            self.status_code = code

    class Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> Client:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

        async def get(self, *args: object, **kwargs: object) -> Response:
            events.append("http_ready")
            state["ready"] = True
            return Response(200)

        async def post(self, *args: object, **kwargs: object) -> Response:
            events.append("submission")
            state["posts"] += 1
            return Response(202 if state["posts"] <= 100 else 429)

    class Child:
        returncode: int | None = None

        def terminate(self) -> None:
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = -9

        async def wait(self) -> int:
            return self.returncode or 0

    async def spawn(*args: object, **kwargs: object) -> Child:
        if "worker" in args:
            events.append("worker_ready" if state["ready"] else "worker_too_early")
            if not state["workers"]:
                await asyncio.to_thread(
                    (tmp_path / "model-journal.jsonl").write_text, '{"call":"synthetic"}\n'
                )
            state["workers"] += 1
        else:
            events.append("model_spawn")
        return Child()

    async def sleep(_: float) -> None:
        pass

    monkeypatch.setattr(load, "create_engine", lambda _: Engine())
    monkeypatch.setattr(load, "AsyncClient", Client)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
    monkeypatch.setattr(asyncio, "sleep", sleep)
    # Twenty owners, as in the real workload, without any credentials/network.
    await asyncio.to_thread(
        (tmp_path / "owners.json").write_text, "[" + ",".join(["{}"] * 20) + "]"
    )
    result: dict[str, Any] = await load.overload_campaign()
    assert result["bounded"] is True and state["posts"] == 120
    assert "worker_too_early" not in events
    assert events.index("http_ready") < events.index("submission") < events.index("worker_ready")
