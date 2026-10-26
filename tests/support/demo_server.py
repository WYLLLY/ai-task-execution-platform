"""Test process around the production demo app; drops a response after committed effect."""

import os
import socket

import uvicorn
from starlette.types import Message, Receive, Scope, Send

from task_platform.demo import create_demo_app


class DropResponse:
    def __init__(self) -> None:
        self.app = create_demo_app()
        self.drop = os.environ.get("DEMO_TEST_DROP") == "1"

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" and self.drop:

            async def lost(message: Message) -> None:
                if message["type"] == "http.response.start":
                    await send({"type": "http.response.start", "status": 502, "headers": []})
                elif message["type"] == "http.response.body":
                    await send({"type": "http.response.body", "body": b""})

            await self.app(scope, receive, lost)
        else:
            await self.app(scope, receive, send)


if __name__ == "__main__":
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    print(sock.getsockname()[1], flush=True)
    uvicorn.Server(uvicorn.Config(DropResponse(), log_level="critical", access_log=False)).run(
        sockets=[sock]
    )
