"""Thin identity and M2 command/read HTTP boundaries."""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select, tuple_
from sqlalchemy.exc import SQLAlchemyError

from task_platform.command_schemas import MAX_BODY, CommandError, CreateTask, parse_command
from task_platform.config import Settings, load_settings
from task_platform.db import create_engine, sessions
from task_platform.health import health
from task_platform.identity import (
    AuthenticationError,
    Identity,
    NotFoundError,
    authenticate,
    owned_key,
)
from task_platform.rate_limits import LocalLimits
from task_platform.resolution import resolve_task
from task_platform.task_models import Step, Task, WorkflowVersion
from task_platform.tasks import (
    PINNED_VERSIONS,
    cancel_task,
    owned_task,
    snapshot,
    submit_task,
    validate_key,
)

logger = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


class KeyMetadata(BaseModel):
    id: UUID
    principal_id: UUID
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or load_settings("api")
    if settings.role != "api" or settings.redis_url is None:
        raise ValueError("API role and Redis configuration required")
    read_engine, control_engine = create_engine(settings), create_engine(settings, control=True)
    factory = sessions(read_engine)
    control_factory = sessions(control_engine)
    limits = LocalLimits()
    redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=1,
        socket_timeout=1,
        max_connections=4,
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await redis.aclose()
            await read_engine.dispose()
            await control_engine.dispose()

    app = FastAPI(
        title="AI Task Execution Platform",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.middleware("http")
    async def request_boundary(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        try:
            response = await call_next(request)
        except Exception:
            # Last-resort HTTP boundary; no exception strings, bodies, headers or URLs are logged.
            logger.error("", extra={"event": "request.failed", "status": 500})
            response = JSONResponse({"detail": "Internal error"}, status_code=500)
        response.headers["Cache-Control"] = "no-store"
        logger.info("", extra={"event": "request.completed", "status": response.status_code})
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(_: Request, __: RequestValidationError) -> JSONResponse:
        return JSONResponse({"detail": "Invalid request"}, status_code=422)

    @app.exception_handler(SQLAlchemyError)
    async def database_error(_: Request, __: SQLAlchemyError) -> JSONResponse:
        return JSONResponse({"detail": "Service unavailable"}, status_code=503)

    @app.exception_handler(AuthenticationError)
    async def authentication_error(_: Request, __: AuthenticationError) -> JSONResponse:
        return JSONResponse(
            {"detail": "Invalid credentials"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(NotFoundError)
    async def not_found(_: Request, __: NotFoundError) -> JSONResponse:
        return JSONResponse({"detail": "Not found"}, status_code=404)

    def token(credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)]) -> str:
        if credentials is None:
            raise AuthenticationError
        return credentials.credentials

    async def current_identity(credential: Annotated[str, Depends(token)]) -> Identity:
        async with factory() as session:
            identity = await authenticate(session, credential)
        limits.check(f"read:{identity.key_id}", 120)
        return identity

    async def control_identity(credential: Annotated[str, Depends(token)]) -> Identity:
        async with control_factory() as session:
            identity = await authenticate(session, credential)
        limits.check(f"control:{identity.principal_id}", 30)
        return identity

    @app.exception_handler(CommandError)
    async def command_error(_: Request, error: CommandError) -> JSONResponse:
        return JSONResponse({"detail": error.detail}, status_code=error.status)

    async def command_body(request: Request) -> tuple[object, str]:
        data = bytearray()
        try:
            async with asyncio.timeout(5):
                async for chunk in request.stream():
                    if len(data) + len(chunk) > MAX_BODY:
                        raise CommandError(413, "Payload too large")
                    data.extend(chunk)
        except TimeoutError:
            raise CommandError(408, "Request timeout") from None
        return parse_command(bytes(data))

    @app.get("/v1/workflows")
    async def workflows(_: Annotated[Identity, Depends(current_identity)]) -> dict[str, object]:
        async with factory() as session:
            rows = (
                await session.scalars(
                    select(WorkflowVersion)
                    .where(WorkflowVersion.available.is_(True))
                    .order_by(WorkflowVersion.name, WorkflowVersion.version)
                )
            ).all()
            return {
                "items": [
                    {
                        "name": row.name,
                        "version": row.version,
                        "versions": row.definition,
                        "input_schema": CreateTask.model_json_schema(),
                        "execution_implemented": False,
                    }
                    for row in rows
                    if row.name == "analysis"
                    and row.version == "1"
                    and row.definition == PINNED_VERSIONS
                ]
            }

    @app.post("/v1/tasks")
    async def create_task(
        request: Request,
        _: Annotated[Identity, Depends(current_identity)],
        credential: Annotated[str, Depends(token)],
    ) -> Response:
        key = validate_key(request.headers.get("Idempotency-Key"))
        value, fingerprint = await command_body(request)
        result = await submit_task(
            factory,
            redis,
            settings,
            credential,
            key,
            value,
            fingerprint,
            request.headers.get("X-Execution-Epoch"),
        )
        return Response(
            content=result.body, status_code=result.status, media_type="application/json"
        )

    @app.post("/v1/tasks/{task_id}/cancel")
    async def cancel(
        task_id: UUID,
        request: Request,
        _: Annotated[Identity, Depends(control_identity)],
        credential: Annotated[str, Depends(token)],
    ) -> Response:
        key = validate_key(request.headers.get("Idempotency-Key"))
        value, fingerprint = await command_body(request)
        result = await cancel_task(control_factory, credential, task_id, key, value, fingerprint)
        return Response(
            content=result.body, status_code=result.status, media_type="application/json"
        )

    @app.post("/v1/tasks/{task_id}/resolve")
    async def resolve(
        task_id: UUID,
        request: Request,
        _: Annotated[Identity, Depends(control_identity)],
        credential: Annotated[str, Depends(token)],
    ) -> Response:
        key = validate_key(request.headers.get("Idempotency-Key"))
        value, fingerprint = await command_body(request)
        result = await resolve_task(control_factory, credential, task_id, key, value, fingerprint)
        return Response(
            content=result.body, status_code=result.status, media_type="application/json"
        )

    @app.get("/v1/tasks/{task_id}")
    async def get_task(
        task_id: UUID, identity: Annotated[Identity, Depends(current_identity)]
    ) -> dict[str, object]:
        async with factory() as session:
            # One row contains the coherent result/version/event watermark snapshot.
            return snapshot(await owned_task(session, identity, task_id))

    @app.get("/v1/tasks")
    async def list_tasks(
        identity: Annotated[Identity, Depends(current_identity)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: UUID | None = None,
    ) -> dict[str, object]:
        async with factory() as session:
            query = select(Task).where(Task.principal_id == identity.principal_id)
            if cursor is not None:
                anchor = await session.scalar(
                    select(Task).where(
                        Task.id == cursor, Task.principal_id == identity.principal_id
                    )
                )
                if anchor is None:
                    raise CommandError(400, "Invalid cursor")
                query = query.where(
                    tuple_(Task.created_at, Task.id) > (anchor.created_at, anchor.id)
                )
            rows = (
                await session.scalars(query.order_by(Task.created_at, Task.id).limit(limit + 1))
            ).all()
            return {
                "items": [snapshot(row) for row in rows[:limit]],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
            }

    @app.get("/v1/tasks/{task_id}/steps")
    async def list_steps(
        task_id: UUID,
        identity: Annotated[Identity, Depends(current_identity)],
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        cursor: UUID | None = None,
    ) -> dict[str, object]:
        async with factory() as session:
            await owned_task(session, identity, task_id)
            query = select(Step).where(
                Step.task_id == task_id, Step.principal_id == identity.principal_id
            )
            if cursor is not None:
                anchor = await session.scalar(query.where(Step.id == cursor))
                if anchor is None:
                    raise CommandError(400, "Invalid cursor")
                query = query.where(Step.id > anchor.id)
            rows = (await session.scalars(query.order_by(Step.id).limit(limit + 1))).all()
            return {
                "items": [
                    {"step_id": str(row.id), "path": row.path, "state": row.state}
                    for row in rows[:limit]
                ],
                "next_cursor": str(rows[limit - 1].id) if len(rows) > limit else None,
            }

    @app.get("/health/live")
    async def live() -> dict[str, bool]:
        return {"alive": True}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        result = await health(control_engine, redis, settings)
        return JSONResponse(
            result.model_dump(mode="json"), status_code=200 if result.ready else 503
        )

    @app.get("/v1/system")
    async def system() -> JSONResponse:
        return await ready()

    @app.get("/v1/me")
    async def me(identity: Annotated[Identity, Depends(current_identity)]) -> dict[str, UUID]:
        return {"principal_id": identity.principal_id, "key_id": identity.key_id}

    @app.get("/v1/api-keys/{key_id}")
    async def key_metadata(
        key_id: UUID, identity: Annotated[Identity, Depends(current_identity)]
    ) -> KeyMetadata:
        async with factory() as session:
            key = await owned_key(session, identity, key_id)
            return KeyMetadata(
                id=key.id,
                principal_id=key.principal_id,
                created_at=key.created_at,
                expires_at=key.expires_at,
                revoked_at=key.revoked_at,
            )

    return app
