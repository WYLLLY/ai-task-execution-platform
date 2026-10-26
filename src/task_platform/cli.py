"""One image, explicit API / administrative / worker-preflight commands."""

import argparse
import asyncio
import json
import logging
import sys
from uuid import UUID

import uvicorn
from sqlalchemy.exc import SQLAlchemyError

from task_platform.catalog import set_admission, set_workflow_availability
from task_platform.config import load_settings
from task_platform.db import create_engine, sessions
from task_platform.health import check_database
from task_platform.identity import (
    NotFoundError,
    disable_principal,
    initialize_platform,
    issue_key,
    provision_principal,
    revoke_key,
)
from task_platform.observability import configure_logging


async def run_admin(args: argparse.Namespace) -> None:
    settings = load_settings("admin")
    engine = create_engine(settings)
    factory = sessions(engine)
    try:
        match args.action:
            case "init":
                await initialize_platform(factory, settings.execution_epoch)
                print(
                    json.dumps(
                        {"initialized": True, "execution_epoch": str(settings.execution_epoch)}
                    )
                )
            case "admission":
                await set_admission(factory, args.enabled == "true")
                print(json.dumps({"admission_open": args.enabled == "true"}))
            case "workflow-availability":
                await set_workflow_availability(
                    factory, args.name, args.version, args.enabled == "true"
                )
                print(json.dumps({"available": args.enabled == "true"}))
            case "principal-create":
                principal_id = await provision_principal(factory, args.name)
                print(json.dumps({"principal_id": str(principal_id)}))
            case "key-create":
                key_id, token = await issue_key(factory, UUID(args.principal))
                # Explicit one-time provisioning output, not a log or HTTP response.
                print(json.dumps({"key_id": str(key_id), "api_key": token}))
            case "key-revoke":
                await revoke_key(factory, UUID(args.principal), UUID(args.key))
                print(json.dumps({"revoked": True}))
            case "principal-disable":
                await disable_principal(factory, UUID(args.principal))
                print(json.dumps({"disabled": True}))
            case _:
                raise ValueError("Unknown administrative command")
    finally:
        await engine.dispose()


async def worker_check() -> None:
    settings = load_settings("worker")
    engine = create_engine(settings, control=True)
    try:
        postgres, schema, epoch = await check_database(engine, settings)
        if not postgres or not schema or epoch != settings.execution_epoch:
            raise ValueError("Worker preflight failed")
        print(json.dumps({"ready": True, "execution_implemented": False, "handlers": []}))
    finally:
        await engine.dispose()


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(prog="atp")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("api")
    worker = commands.add_parser("worker", help="M1 compatibility preflight only")
    worker.add_argument("--check", action="store_true", required=True)
    admin = commands.add_parser("admin")
    actions = admin.add_subparsers(dest="action", required=True)
    actions.add_parser("init")
    create = actions.add_parser("principal-create")
    create.add_argument("--name", required=True)
    for action in ("key-create", "key-revoke", "principal-disable"):
        sub = actions.add_parser(action)
        sub.add_argument("--principal", required=True)
        if action == "key-revoke":
            sub.add_argument("--key", required=True)
    admission = actions.add_parser("admission")
    admission.add_argument("--enabled", choices=["true", "false"], required=True)
    workflow = actions.add_parser("workflow-availability")
    workflow.add_argument("--name", required=True)
    workflow.add_argument("--version", required=True)
    workflow.add_argument("--enabled", choices=["true", "false"], required=True)
    args = parser.parse_args()
    try:
        if args.command == "api":
            uvicorn.run(
                "task_platform.api:create_app",
                factory=True,
                host="0.0.0.0",  # noqa: S104 -- container port, host publication is loopback-only
                port=8000,
                access_log=False,
                log_config=None,
                proxy_headers=False,
            )
        elif args.command == "worker":
            asyncio.run(worker_check())
        else:
            asyncio.run(run_admin(args))
    except ValueError, KeyError, OSError, SQLAlchemyError, NotFoundError:
        logging.getLogger(__name__).error("", extra={"event": "command.failed"})
        print(
            "Command failed; check configuration, permissions and service availability.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
