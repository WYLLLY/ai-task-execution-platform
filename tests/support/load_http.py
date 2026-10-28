"""Private benchmark-only HTTP boundaries with independent physical journals."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from starlette.responses import StreamingResponse
from starlette.types import Receive, Scope, Send

from scripts.load_metrics import model_response
from task_platform.demo import create_demo_app


class DelayedDemo:
    def __init__(self) -> None:
        self.app = create_demo_app()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST":
            await asyncio.sleep(0.1)
        await self.app(scope, receive, send)


def model_app() -> FastAPI:
    app = FastAPI()
    journal = Path("/drill/model-journal.jsonl")

    @app.post("/complete", response_model=None)
    async def complete(request: Request) -> dict[str, Any] | StreamingResponse:
        def record() -> None:
            with journal.open("a") as stream:
                stream.write(json.dumps({"call": request.headers["x-transmission-id"]}) + "\n")
                stream.flush()
                os.fsync(stream.fileno())

        await asyncio.to_thread(record)
        context = await request.json()
        if os.environ.get("ATP_LOAD_SLOW") == "1":
            body = json.dumps(model_response(context)).encode()

            async def drip() -> AsyncIterator[bytes]:
                for byte in body:
                    yield bytes([byte])
                    await asyncio.sleep(0.03)

            return StreamingResponse(drip(), media_type="application/json")
        await asyncio.sleep(0.5)
        return model_response(context)

    return app


if __name__ == "__main__":
    demo = os.environ.get("ATP_LOAD_DEMO") == "1"
    uvicorn.run(
        DelayedDemo() if demo else model_app(),
        host="0.0.0.0" if demo else "127.0.0.1",  # noqa: S104 -- isolated private fixture
        port=8000 if demo else 18080,
        access_log=False,
        log_level="critical",
    )
