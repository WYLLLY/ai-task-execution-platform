"""Responses checkpoints and real reservations through the M4 workflow."""

import json
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import sessions
from task_platform.execution import Executor
from task_platform.provider_admin import configure_provider
from task_platform.responses import estimate_input, parse_output, price, validate_profile
from task_platform.responses_workflow import ResponsesExecutor
from tests.unit.test_responses import profile_data, response

from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch

pytestmark = pytest.mark.integration


async def submit(client: AsyncClient, headers: dict[str, str], key: str = "responses") -> None:
    result = await client.post(
        "/v1/tasks",
        headers={**headers, "Idempotency-Key": key},
        json={
            "workflow": {"name": "analysis", "version": "2"},
            "input": {"texts": ["Synthetic task"]},
        },
    )
    assert result.status_code == 202, result.text


def worker(executor: Executor) -> ResponsesExecutor:
    return ResponsesExecutor(
        executor.factory, executor.control, executor.epoch, validate_profile(profile_data())
    )


async def test_versioned_registration_and_shared_margin(admin_engine: AsyncEngine) -> None:
    profile = validate_profile(profile_data())
    await configure_provider(sessions(admin_engine), profile)
    await configure_provider(sessions(admin_engine), profile)
    async with admin_engine.connect() as c:
        row = (
            await c.execute(
                text("SELECT requests_per_minute,tokens_per_minute FROM provider_admission")
            )
        ).one()
        assert row == (80, 800000)
        definition = await c.scalar(
            text("SELECT definition FROM workflow_versions WHERE version='2'")
        )
        assert definition["provider"] == profile_data()
    changed = profile_data()
    changed["project"] = "proj_other"
    with pytest.raises(ValueError, match="immutable"):
        await configure_provider(sessions(admin_engine), validate_profile(changed))


async def test_real_reservation_and_protocol_replay_after_worker_restart(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    profile = validate_profile(profile_data())
    await configure_provider(sessions(admin_engine), profile)
    await submit(client, command_owner)
    service = worker(executor)
    lease = await service.claim("responses-one")
    assert lease is not None
    payload = await service.context(lease)
    assert payload is not None
    call = uuid4()
    assert await service.authorize(lease, call, payload)
    async with admin_engine.connect() as c:
        row = (
            await c.execute(
                text("SELECT reserved_cost,reserved_tokens,tariff,tokenizer FROM call_costs")
            )
        ).one()
        assert row == (
            price(profile, estimate_input(payload), 2000, 0),
            estimate_input(payload) + 2000,
            profile.tariff,
            profile.tokenizer,
        )
    outcome = parse_output(profile, response())
    assert await service.complete(
        lease,
        call,
        outcome.outcome,
        cost=outcome.cost,
        tokens=outcome.tokens,
        result=outcome.response,
    )
    tool = await worker(executor).claim("tool-restarted")
    assert tool and await worker(executor).run_tool(tool)
    next_lease = await worker(executor).claim("model-restarted")
    assert next_lease
    next_payload = await worker(executor).context(next_lease)
    assert next_payload
    wire = json.loads(next_payload)
    assert wire["input"][1:3] == response()["output"]
    assert wire["input"][3]["type"] == "function_call_output"
    assert wire["input"][3]["call_id"] == "call_1"
    assert json.loads(wire["input"][3]["output"]) == {"excerpts": []}
    public = (await client.get(f"/v1/tasks/{lease.task_id}/steps", headers=command_owner)).text
    assert "opaque-do-not-decode" not in public and "protocol_items" not in public


async def test_m4_worker_cannot_substitute_new_handler(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    await configure_provider(sessions(admin_engine), validate_profile(profile_data()))
    await submit(client, command_owner)
    assert await executor.claim("old-worker") is None
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT reason FROM tasks")) == "handler_unavailable"


async def test_partial_usage_is_durable_and_full_provision_survives(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    await configure_provider(sessions(admin_engine), validate_profile(profile_data()))
    await submit(client, command_owner)
    service = worker(executor)
    lease = await service.claim("partial")
    assert lease
    payload = await service.context(lease)
    assert payload
    call = uuid4()
    assert await service.authorize(lease, call, payload)
    raw = response()
    raw["usage"] = {"input_tokens": 100}
    outcome = parse_output(validate_profile(profile_data()), raw)
    assert await service.complete(
        lease,
        call,
        outcome.outcome,
        cost=outcome.cost,
        tokens=outcome.tokens,
        result=outcome.response,
        observation=outcome.observation,
    )
    async with admin_engine.connect() as c:
        observation = await c.scalar(text("SELECT provider_observation FROM external_calls"))
        assert observation["usage"] == {"input_tokens": 100}
        assert (
            await c.execute(
                text("SELECT confirmed_cost, exposure_cost=reserved_cost, revision FROM call_costs")
            )
        ).one() == (0, True, 0)
    # A fresh worker sees the same durable checkpoint, not a regenerated model request.
    lease2 = await worker(executor).claim("after-restart")
    assert lease2 and await worker(executor).run_tool(lease2)


async def test_owner_explicit_regeneration_preserves_old_provider_exposure(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    await configure_provider(sessions(admin_engine), validate_profile(profile_data()))
    await submit(client, command_owner)
    service = worker(executor)
    lease = await service.claim("ambiguous")
    assert lease
    payload = await service.context(lease)
    assert payload
    call = uuid4()
    assert await service.authorize(lease, call, payload)
    assert await service.complete(lease, call, "unknown")
    assert await worker(executor).claim("never-automatically-regenerate") is None
    snapshot = (await client.get(f"/v1/tasks/{lease.task_id}", headers=command_owner)).json()
    body = {
        "action": "regenerate",
        "accept_overlap_cost_risk": True,
        "expected_task_version": snapshot["task_version"],
    }
    resolved = await client.post(
        f"/v1/tasks/{lease.task_id}/resolve",
        json=body,
        headers={**command_owner, "Idempotency-Key": "regenerate"},
    )
    assert resolved.status_code == 200, resolved.text
    replacement = await worker(executor).claim("replacement")
    assert replacement and replacement.generation > lease.generation
    assert not await service.complete(lease, call, "succeeded", cost=138, tokens=120)
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "running"
        assert await c.scalar(text("SELECT confirmed_cost FROM call_costs")) == 138


@pytest.mark.parametrize("version", ["1", "2"])
async def test_catalogue_advertises_implemented_available_workflows(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    version: str,
) -> None:
    await configure_provider(sessions(admin_engine), validate_profile(profile_data()))
    catalogue = await client.get("/v1/workflows", headers=command_owner)
    assert catalogue.status_code == 200
    items = {item["version"]: item for item in catalogue.json()["items"]}
    assert version in items
    assert items[version]["execution_implemented"] is True
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "UPDATE workflow_versions SET available=false WHERE name='analysis' AND version=:v"
            ),
            {"v": version},
        )
    hidden = await client.get("/v1/workflows", headers=command_owner)
    assert all(item["version"] != version for item in hidden.json()["items"])
