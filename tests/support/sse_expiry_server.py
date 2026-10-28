"""Real TCP fixture for the production stream's normal lifetime boundary."""

import socket
from uuid import uuid4

import uvicorn
from fastapi import FastAPI

from task_platform.event_reads import Batch, Event
from task_platform.identity import Identity
from task_platform.sse import AuthLease, Stream

app = FastAPI()
counts = {"opened": 0, "released": 0}


@app.get("/health")
async def health() -> dict[str, bool]:
    return {"ready": True}


@app.get("/counts")
async def observed() -> dict[str, int]:
    return counts


@app.get("/stream")
async def stream() -> Stream:
    counts["opened"] += 1

    async def check() -> None:
        return None

    def release() -> None:
        counts["released"] += 1

    return Stream(
        uuid4(),
        Identity(uuid4(), uuid4()),
        Batch(0, 1, 1, (Event(1, b"id: 1\ndata: synthetic\n\n"),)),
        AuthLease(check),
        None,
        release,
        lifetime=0.1,
        write_timeout=0.02,
    )


if __name__ == "__main__":
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1], flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level="critical", access_log=False)).run(sockets=[sock])
