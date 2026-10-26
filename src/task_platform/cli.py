"""One image, explicit API / administrative / worker-preflight commands."""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
from functools import partial
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from task_platform.catalog import set_admission, set_workflow_availability
from task_platform.config import load_settings, secret_setting
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
from task_platform.metrics import execution_metrics
from task_platform.observability import configure_logging
from task_platform.supervisor import supervise


def read_corpus_file(filename: str) -> str:
    with Path(filename).open("rb") as source:
        return source.read(1048577).decode("utf-8")


async def run_admin(args: argparse.Namespace) -> None:
    settings = load_settings("admin")
    engine = create_engine(settings)
    factory = sessions(engine)
    try:
        match args.action:
            case "provider-configure" | "accounting":
                from task_platform.provider_admin import configure_provider, reconcile
                from task_platform.provider_commands import read_json_file
                from task_platform.responses import validate_profile

                value = await asyncio.to_thread(read_json_file, args.file)
                if args.action == "provider-configure":
                    await configure_provider(factory, validate_profile(value))
                else:
                    await reconcile(factory, args.call, value)
                print(json.dumps({"applied": True}))
            case "corpus":
                from task_platform.corpus import import_document, revoke_document

                if args.corpus_action == "import":
                    content = await asyncio.to_thread(read_corpus_file, args.file)
                    version = await import_document(
                        factory, UUID(args.principal), UUID(args.document), content
                    )
                    print(json.dumps({"document_id": args.document, "version": version}))
                else:
                    await revoke_document(
                        factory, UUID(args.principal), UUID(args.document), args.version
                    )
                    print(json.dumps({"revoked": True}))
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
        print(
            json.dumps(
                {
                    "ready": True,
                    "execution_implemented": True,
                    "simulation_only": True,
                    "handlers": ["simulation-v1"],
                    "metrics": await execution_metrics(sessions(engine)),
                }
            )
        )
    finally:
        await engine.dispose()


async def worker_run(once: bool, analysis_mode: bool = False) -> None:
    from task_platform.analysis_worker import AnalysisWorker
    from task_platform.execution import Executor
    from task_platform.report_transport import ReportTransport
    from task_platform.transport import SimulationTransport
    from task_platform.worker import Worker
    from task_platform.workflow import AnalysisExecutor

    settings = load_settings("worker")
    if os.environ.get("ATP_TEST_PROFILE") != "1" or not settings.dispatch_enabled:
        raise ValueError("Simulation requires explicit isolated test profile and dispatch opt-in")
    destination = os.environ.get("SIMULATOR_URL", "http://simulator:8001")
    engine, control = create_engine(settings), create_engine(settings, control=True)
    reports: ReportTransport | None = None
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    try:
        ready, schema, epoch = await check_database(control, settings)
        if not ready or not schema or epoch != settings.execution_epoch:
            raise ValueError("Worker preflight failed")
        executor = Executor(sessions(engine), sessions(control), settings.execution_epoch)
        async with SimulationTransport(destination) as transport:
            worker: Worker
            if analysis_mode:
                reports = ReportTransport(
                    os.environ.get("DEMO_URL", "http://demo:8000"),
                    secret_setting("DEMO_SERVICE_KEY").get_secret_value(),
                )
                workflow_executor = AnalysisExecutor(
                    sessions(engine), sessions(control), settings.execution_epoch
                )
                worker = AnalysisWorker(
                    workflow_executor, transport, reports, holder=f"analysis-{os.getpid()}"
                )
            else:
                worker = Worker(executor, transport, holder=f"worker-{os.getpid()}")
            if once:
                await worker.maintenance()
                await worker.run_once()
            else:
                await worker.run(stop)
    finally:
        if reports is not None:
            await reports.close()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)
        await engine.dispose()
        await control.dispose()


async def openai_run(once: bool) -> None:
    from task_platform.provider_commands import worker_openai

    await worker_openai(once)


def main() -> None:
    configure_logging()
    parser = argparse.ArgumentParser(prog="atp")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("api")
    commands.add_parser("demo")
    commands.add_parser("smoke-openai")
    worker = commands.add_parser("worker", help="Durable executor; M3/M4 simulation profiles only")
    worker_mode = worker.add_mutually_exclusive_group(required=True)
    worker_mode.add_argument("--check", action="store_true")
    worker_mode.add_argument("--simulate", action="store_true")
    worker_mode.add_argument("--analysis", action="store_true")
    worker_mode.add_argument("--openai", action="store_true")
    worker.add_argument("--once", action="store_true")
    admin = commands.add_parser("admin")
    actions = admin.add_subparsers(dest="action", required=True)
    actions.add_parser("init")
    for action in ("provider-configure", "accounting"):
        sub = actions.add_parser(action)
        sub.add_argument("--file", required=True)
        if action == "accounting":
            sub.add_argument("--call", required=True)
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
    corpus = actions.add_parser("corpus")
    corpus_actions = corpus.add_subparsers(dest="corpus_action", required=True)
    for action in ("import", "revoke"):
        sub = corpus_actions.add_parser(action)
        sub.add_argument("--principal", required=True)
        sub.add_argument("--document", required=True)
        if action == "import":
            sub.add_argument("--file", required=True)
        else:
            sub.add_argument("--version", type=int, required=True)
    args = parser.parse_args()
    try:
        if args.command == "api":
            import uvicorn

            uvicorn.run(
                "task_platform.api:create_app",
                factory=True,
                host="0.0.0.0",  # noqa: S104 -- container port, host publication is loopback-only
                port=8000,
                access_log=False,
                log_config=None,
                proxy_headers=False,
            )
        elif args.command == "demo":
            import uvicorn

            uvicorn.run(
                "task_platform.demo:create_demo_app",
                factory=True,
                host="0.0.0.0",  # noqa: S104 -- private Compose service binding
                port=8000,
                access_log=False,
                log_config=None,
                proxy_headers=False,
            )  # noqa: S104 -- private Compose network only
        elif args.command == "smoke-openai":
            from task_platform.provider_commands import smoke_openai

            asyncio.run(smoke_openai())
        elif args.command == "worker":
            if args.check:
                asyncio.run(worker_check())
            elif args.openai:
                raise SystemExit(supervise(partial(openai_run, args.once)))
            else:
                raise SystemExit(supervise(partial(worker_run, args.once, args.analysis)))
        else:
            asyncio.run(run_admin(args))
    except ValueError, KeyError, OSError, SQLAlchemyError, NotFoundError:
        logging.getLogger(__name__).error("", extra={"event": "command.failed"})
        print(
            "Command failed; check configuration, permissions and service availability.",
            file=sys.stderr,
        )
        raise SystemExit(1) from None
