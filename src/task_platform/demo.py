"""Authenticated fixed-purpose report service with an independent PostgreSQL journal."""

import hashlib
import hmac
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

from fastapi import FastAPI, HTTPException, Request, Response
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from task_platform.agent_protocol import Report, encode
from task_platform.command_schemas import CommandError, StrictSchema, parse_command
from task_platform.config import secret_setting


class SaveReport(StrictSchema):
    owner: str
    operation: str
    report: Report


def create_demo_app() -> FastAPI:
    url = secret_setting("DEMO_DATABASE_URL").get_secret_value()
    service_key = secret_setting("DEMO_SERVICE_KEY").get_secret_value()
    if len(service_key) < 32 or not url.startswith("postgresql+psycopg://"):
        raise ValueError("Invalid demo configuration")
    engine = create_async_engine(
        url,
        pool_size=4,
        max_overflow=0,
        pool_timeout=2,
        hide_parameters=True,
        connect_args={"options": "-c statement_timeout=3000 -c lock_timeout=1000"},
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        try:
            async with engine.connect() as connection:
                await connection.execute(text("SELECT 1 FROM report_effects LIMIT 0"))
            yield
        finally:
            await engine.dispose()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def authenticate(request: Request) -> None:
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), f"Bearer {service_key}".encode()):
            raise HTTPException(401, "Unauthorized")

    @app.post("/reports")
    async def save(request: Request) -> Response:
        authenticate(request)
        body = bytearray()
        async for chunk in request.stream():
            if len(body) + len(chunk) > 65536:
                raise HTTPException(413, "Report too large")
            body.extend(chunk)
        try:
            value, _ = parse_command(bytes(body))
            command = SaveReport.model_validate(value)
            owner, operation = UUID(command.owner), UUID(command.operation)
            transmission = UUID(request.headers.get("x-transmission-id", ""))
        except ValueError, ValidationError, CommandError:
            raise HTTPException(422, "Invalid report") from None
        fingerprint = hashlib.sha256(body).hexdigest()
        params = {
            "owner": owner,
            "operation": operation,
            "hash": fingerprint,
            "transmission": transmission,
        }
        # Physical invocation survives rollback of the business transaction and application DB.
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO invocation_journal(transmission,owner,operation,payload_hash) "
                    "VALUES(:transmission,:owner,:operation,:hash)"
                ),
                params,
            )
        effect_id = uuid4()
        result = encode(
            {
                "owner": str(owner),
                "operation": str(operation),
                "payload_hash": fingerprint,
                "effect_id": str(effect_id),
            }
        ).decode()
        async with engine.begin() as connection:
            await connection.execute(
                text(
                    "INSERT INTO "
                    "report_effects(owner,operation,effect_id,payload_hash,payload,response) "
                    "VALUES(:owner,:operation,:effect,:hash,:payload,:response) ON "
                    "CONFLICT(owner,operation) DO NOTHING"
                ),
                {**params, "effect": effect_id, "payload": bytes(body), "response": result},
            )
            row = (
                await connection.execute(
                    text(
                        "SELECT payload_hash,response FROM report_effects WHERE owner=:owner "
                        "AND operation=:operation"
                    ),
                    params,
                )
            ).one()
            if row[0] != fingerprint:
                raise HTTPException(409, "Operation conflict")
            result = row[1]
        return Response(result, media_type="application/json")

    @app.get("/operations/{owner}/{operation}")
    async def lookup(owner: UUID, operation: UUID, request: Request) -> Response:
        authenticate(request)
        async with engine.connect() as connection:
            result = await connection.scalar(
                text(
                    "SELECT response FROM report_effects WHERE owner=:owner AND "
                    "operation=:operation"
                ),
                {"owner": owner, "operation": operation},
            )
        if result is None:
            raise HTTPException(404, "Not found")
        return Response(result, media_type="application/json")

    return app
