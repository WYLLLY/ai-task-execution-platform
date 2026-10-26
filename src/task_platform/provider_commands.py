"""Worker-only provider credentials and separately authorized synthetic smoke."""

import asyncio
import json
import os
import signal
import time
from pathlib import Path
from uuid import uuid4

from task_platform.agent_protocol import encode
from task_platform.analysis_worker import AnalysisWorker
from task_platform.command_schemas import parse_command
from task_platform.config import load_settings, secret_setting
from task_platform.db import create_engine, sessions
from task_platform.execution import Permit
from task_platform.health import check_database
from task_platform.openai_transport import ResponsesTransport
from task_platform.report_transport import ReportTransport
from task_platform.responses import (
    ProviderProfile,
    build_request,
    estimate_input,
    price,
    validate_profile,
)
from task_platform.responses_workflow import ResponsesExecutor


def read_json_file(filename: str) -> object:
    with Path(filename).open("rb") as source:
        data = source.read(16385)
    if len(data) > 16384:
        raise ValueError("Configuration/evidence file too large")
    return parse_command(data)[0]


def load_profile() -> ProviderProfile:
    return validate_profile(read_json_file(os.environ["OPENAI_PROFILE_FILE"]))


async def worker_openai(once: bool) -> None:
    from task_platform.notifications import publishing

    settings = load_settings("worker")
    if not settings.dispatch_enabled:
        raise ValueError("Explicit dispatch opt-in required")
    profile = load_profile()
    # Loaded only in the worker command; neither API settings nor API mounts use this key.
    key = secret_setting("OPENAI_API_KEY")
    engine, control = create_engine(settings), create_engine(settings, control=True)
    factory, control_factory = sessions(engine), sessions(control)
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for signum in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(signum, stop.set)
    reports = ReportTransport(
        os.environ.get("DEMO_URL", "http://demo:8000"),
        secret_setting("DEMO_SERVICE_KEY").get_secret_value(),
    )
    try:
        ready, schema, epoch = await check_database(control, settings)
        if not ready or not schema or epoch != settings.execution_epoch:
            raise ValueError("Worker preflight failed")
        async with (
            publishing(settings, factory, control_factory),
            ResponsesTransport(
                profile, key, test_url=os.environ.get("RESPONSES_TEST_URL")
            ) as model,
        ):
            worker = AnalysisWorker(
                ResponsesExecutor(factory, control_factory, settings.execution_epoch, profile),
                model,
                reports,
                holder=f"responses-{os.getpid()}",
            )
            if once:
                await worker.maintenance()
                await worker.run_once()
            else:
                await worker.run(stop)
    finally:
        await reports.close()
        for signum in (signal.SIGINT, signal.SIGTERM):
            loop.remove_signal_handler(signum)
        await engine.dispose()
        await control.dispose()


async def smoke_openai() -> None:
    if os.environ.get("OPENAI_SMOKE_AUTHORIZED") != "yes":
        print("SKIPPED: separate paid OpenAI smoke authorization is absent")
        return
    cap = os.environ.get("OPENAI_SMOKE_CAP_MICRO_USD", "")
    if not cap.isascii() or not cap.isdigit() or not 0 < int(cap) <= 1000000:
        print("SKIPPED: explicit positive spend cap (maximum USD 1) is required")
        return
    if not os.environ.get("OPENAI_PROFILE_FILE") or not (
        os.environ.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY_FILE")
    ):
        print("SKIPPED: explicit provider profile and worker credential are required")
        return
    configured = load_profile().model_dump()
    configured.update(output_tokens=128, reasoning_effort="none")
    profile = validate_profile(configured)
    payload = build_request(
        profile,
        [
            "Synthetic smoke: summarize the words red, green, blue. "
            "No documents. Return a short report with no citations."
        ],
        [],
    )
    # Reserve both possible turns before the first send, including the maximum
    # permitted second context. Unknown usage stops the smoke; it never retries.
    reservation = 2 * price(profile, profile.context_tokens, profile.output_tokens, 0)
    if reservation > int(cap):
        print("SKIPPED: two-turn conservative reservation exceeds the spending cap")
        return
    first = json.loads(payload)
    first["tool_choice"] = {"type": "function", "name": "search_documents"}
    payload = encode(first)
    attempts, cost, tokens = 0, 0, 0
    passed = False
    started = time.monotonic()
    async with ResponsesTransport(profile, secret_setting("OPENAI_API_KEY")) as transport:
        for turn in range(2):
            input_tokens = estimate_input(payload)
            turn_reservation = price(profile, input_tokens, profile.output_tokens, 0)
            if (
                input_tokens > profile.context_tokens
                or cost + turn_reservation > int(cap)
                or time.monotonic() - started >= 60
            ):
                break
            remaining = 60 - (time.monotonic() - started)
            attempts += 1
            outcome = await transport.send(Permit(uuid4(), payload, remaining, time.monotonic()))
            if outcome.cost is None or outcome.tokens is None:
                break
            cost += outcome.cost
            tokens += outcome.tokens
            if cost > int(cap) or outcome.outcome != "succeeded" or outcome.response is None:
                break
            canonical = outcome.response.get("canonical")
            if not isinstance(canonical, dict):
                break
            items = canonical.get("items")
            if not isinstance(items, list) or len(items) != 1:
                break
            action = items[0]
            if turn == 1:
                passed = action.get("type") == "report"
                break
            if action.get("type") != "function_call" or action.get("name") != "search_documents":
                break
            history = outcome.response.get("protocol_items")
            if not isinstance(history, list):
                break
            history = [
                *history,
                {
                    "type": "function_call_output",
                    "call_id": action["call_id"],
                    "output": encode({"excerpts": []}).decode(),
                },
            ]
            second = json.loads(
                build_request(profile, ["Synthetic colors: red, green, blue."], history)
            )
            second["tool_choice"] = "none"
            payload = encode(second)
    print(
        json.dumps(
            {
                "status": "PASS" if passed else "FAIL",
                "request_attempts": attempts,
                "confirmed_cost_micro_usd": cost,
                "confirmed_tokens": tokens,
                "cap_micro_usd": int(cap),
                "stateless_roundtrip": passed,
            }
        )
    )
    if not passed:
        raise SystemExit(1)
