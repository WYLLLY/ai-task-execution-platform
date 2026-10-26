"""Transparent ASGI boundary: preserve transport backpressure and redact diagnostics."""

import logging

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

# Preserve the established API operational logger across the middleware refactor.
logger = logging.getLogger("task_platform.api")


class RequestBoundary:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        started = False
        status = 500

        async def forward(message: Message) -> None:
            nonlocal started, status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = [
                    (key, value)
                    for key, value in message["headers"]
                    if key.lower() != b"cache-control"
                ]
                message = {**message, "headers": [*headers, (b"cache-control", b"no-store")]}
                started = True
            # No queues/tasks/body iteration: Stream's timeout reaches the actual send.
            await send(message)

        try:
            await self.app(scope, receive, forward)
        except Exception:
            logger.error("", extra={"event": "request.failed", "status": 500})
            if not started:
                await JSONResponse({"detail": "Internal error"}, status_code=500)(
                    scope, receive, forward
                )
        logger.info("", extra={"event": "request.completed", "status": status})
