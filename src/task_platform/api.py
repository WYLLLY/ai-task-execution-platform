"""Thin identity and M2 command/read HTTP boundaries."""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, cast
from uuid import UUID

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel
from redis.asyncio import Redis
from sqlalchemy import select, tuple_
from sqlalchemy.exc import SQLAlchemyError

from task_platform import runtime_signals
from task_platform.command_schemas import MAX_BODY, CommandError, CreateTask, parse_command
from task_platform.config import Settings, load_settings
from task_platform.db import ObservedPool, create_engine, sessions
from task_platform.event_cursor import HistoryExpired
from task_platform.event_hub import Hub
from task_platform.event_reads import EventInvariantError, read_events, task_snapshot, usage
from task_platform.health import health
from task_platform.http_boundary import RequestBoundary
from task_platform.identity import (
    AuthenticationError,
    Identity,
    NotFoundError,
    authenticate,
    owned_key,
)
from task_platform.notifications import Notifications
from task_platform.operations import prometheus
from task_platform.rate_limits import LocalLimits
from task_platform.resolution import resolve_task
from task_platform.sse import AuthLease, Connections, Stream
from task_platform.task_models import Step, Task, WorkflowVersion
from task_platform.tasks import (
    cancel_task,
    owned_task,
    snapshot,
    submit_task,
    supported_workflow,
    validate_key,
)

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
    connections = Connections()
    hub = Hub(factory)
    redis = Redis.from_url(
        settings.redis_url.get_secret_value(),
        socket_connect_timeout=1,
        socket_timeout=1,
        max_connections=4,
    )
    notifier = Notifications(redis)
    notifier.attach(factory)
    notifier.attach(control_factory)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        background = [asyncio.create_task(notifier.run()), asyncio.create_task(hub.listen(redis))]
        try:
            yield
        finally:
            for child in background:
                child.cancel()
            await asyncio.gather(*background, return_exceptions=True)
            await hub.close()
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

    app.add_middleware(RequestBoundary)

    @app.get("/metrics")
    async def process_metrics() -> Response:
        read, control = (
            cast(ObservedPool, read_engine.pool),
            cast(ObservedPool, control_engine.pool),
        )
        values = {
            "sse_connections": connections.active,
            "sse_followed_tasks": hub.followed_tasks,
            "read_pool_in_use": read.checkedout(),
            "control_pool_in_use": control.checkedout(),
            "event_invariants": runtime_signals.event_invariants,
            "renewal_failures": runtime_signals.renewal_failures,
            "pool_acquire_milliseconds": max(
                read.max_recent_acquire_milliseconds, control.max_recent_acquire_milliseconds
            ),
            "pool_utilization_percent": max(
                read.checkedout() * 100 // read.size(), control.checkedout() * 100 // control.size()
            ),
        }
        return Response(prometheus(values), media_type="text/plain; version=0.0.4; charset=utf-8")

    @app.exception_handler(EventInvariantError)
    async def event_invariant(request: Request, _: EventInvariantError) -> JSONResponse:
        task_id = UUID(str(request.path_params["task_id"]))
        runtime_signals.invariant(task_id)
        return JSONResponse(
            {"detail": "Event history unavailable", "snapshot_url": f"/v1/tasks/{task_id}"},
            status_code=410,
        )

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
        body = {"detail": error.detail}
        if isinstance(error, HistoryExpired):
            body["snapshot_url"] = error.snapshot_url
        return JSONResponse(body, status_code=error.status)

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
                        "execution_implemented": True,
                    }
                    for row in rows
                    if supported_workflow(row)
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
            return await task_snapshot(session, identity, task_id)

    @app.get("/v1/usage")
    async def get_usage(
        identity: Annotated[Identity, Depends(current_identity)],
    ) -> dict[str, object]:
        async with factory() as session:
            return await usage(session, identity)

    @app.get("/v1/tasks/{task_id}/events")
    async def events(
        task_id: UUID,
        request: Request,
        identity: Annotated[Identity, Depends(current_identity)],
        credential: Annotated[str, Depends(token)],
        after: str | None = None,
    ) -> Response:
        async def check() -> None:
            async with factory() as session:
                await authenticate(session, credential)

        auth = AuthLease(check)
        await auth.ensure()
        initial = await read_events(
            factory,
            identity,
            task_id,
            after=after,
            last_event_id=request.headers.get("Last-Event-ID"),
        )
        release_connection = connections.acquire(identity.principal_id)
        try:
            release_subscription = hub.subscribe(task_id, identity, initial)
        except BaseException:
            release_connection()
            raise

        def release() -> None:
            release_subscription()
            release_connection()

        return Stream(task_id, identity, initial, auth, hub, release)

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
