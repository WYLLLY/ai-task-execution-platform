"""F21: restoration is a new, fenced incident, never an ordinary worker retry."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from task_platform.db import sessions
from task_platform.execution import Executor
from task_platform.recovery import begin_restore, reopen, revise_reserve

from .test_commands import create
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch

pytestmark = pytest.mark.integration


def proof(**updates: object) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "evidence_id": "restore-incident-1",
        "restored_at": (now - timedelta(hours=2)).isoformat(),
        "incident_at": now.isoformat(),
        "old_workers_isolated": True,
        "credentials_revoked": True,
        "live_egress_blocked": True,
        "proof_sha256": "b" * 64,
        **updates,
    }


async def reset_recovery(engine: AsyncEngine, epoch: str) -> None:
    from task_platform.db import create_engine

    from .conftest import settings_for

    migrator = create_engine(settings_for("migrator"))
    try:
        async with migrator.begin() as c:
            exists = await c.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM information_schema.columns "
                    "WHERE table_name='platform_control' AND column_name='recovery')"
                )
            )
            if exists:
                await c.execute(
                    text(
                        "UPDATE platform_control SET recovery=NULL, "
                        "reserve_cost=0,reserve_tokens=0,reserve_revision=0, "
                        "execution_epoch=:epoch,admission_open=false,dispatch_open=false"
                    ),
                    {"epoch": epoch},
                )
                await c.execute(text("UPDATE principals SET reserve_cost=0,reserve_tokens=0"))
                await c.execute(text("TRUNCATE restore_decisions"))
    finally:
        await migrator.dispose()


async def test_restore_quarantines_all_tasks_revokes_keys_and_preserves_exposure(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    admin_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISPATCH_ENABLED", "false")
    old_epoch = command_owner["X-Execution-Epoch"]
    first = await create(client, command_owner)
    lease = await executor.claim("before-restore")
    assert lease
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    second = await create(client, command_owner, "without-B")
    evidence = proof()
    epoch = uuid4()
    try:
        assert await begin_restore(sessions(admin_engine), epoch, evidence) == {"quarantined": 2}
        assert await begin_restore(sessions(admin_engine), epoch, evidence) == {"quarantined": 2}
        with pytest.raises(ValueError, match="conflict"):
            await begin_restore(
                sessions(admin_engine), epoch, {**evidence, "proof_sha256": "c" * 64}
            )
        assert not await executor.heartbeat(lease)
        assert await executor.claim("obsolete-worker") is None
        assert (await client.get("/v1/me", headers=command_owner)).status_code == 401
        async with admin_engine.connect() as c:
            rows = (
                await c.execute(
                    text(
                        "SELECT state,reason,to_jsonb(t)->>'restore_uncertain' "
                        "FROM tasks t ORDER BY id"
                    )
                )
            ).all()
            assert [tuple(row) for row in rows] == [("suspended", "restore_quarantine", "true")] * 2
            assert await c.scalar(text("SELECT count(*) FROM attempts WHERE state='active'")) == 0
            assert await c.scalar(text("SELECT state FROM external_calls")) == "unknown"
            assert await c.scalar(text("SELECT exposure_cost FROM call_costs")) == 4004
        system = (await client.get("/v1/system")).json()
        assert system["recovery"]["restored_at"] == evidence["restored_at"]
        assert system["recovery"]["acceptance_history_may_be_lost"] is True
        assert str(first["task_id"]) != str(second["task_id"])
    finally:
        await reset_recovery(admin_engine, old_epoch)


@pytest.mark.parametrize(
    "field", ["old_workers_isolated", "credentials_revoked", "live_egress_blocked"]
)
async def test_restore_rejects_missing_isolation_evidence(
    admin_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    monkeypatch.setenv("DISPATCH_ENABLED", "false")
    with pytest.raises(ValueError):
        await begin_restore(sessions(admin_engine), uuid4(), proof(**{field: False}))


async def test_restore_requires_external_dispatch_lock(
    admin_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISPATCH_ENABLED", "true")
    with pytest.raises(ValueError, match="external dispatch lock"):
        await begin_restore(sessions(admin_engine), uuid4(), proof())


async def test_unbounded_disaster_exposure_blocks_reopen_and_reserve_is_revisioned(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DISPATCH_ENABLED", "false")
    epoch = uuid4()
    factory = sessions(admin_engine)
    try:
        await begin_restore(factory, epoch, proof())
        with pytest.raises(ValueError, match="unbounded"):
            await reopen(factory, epoch, {"evidence_id": "open-1", "proof_sha256": "d" * 64})
        owner = (await provision_current_key(client, command_owner, admin_engine))[0]
        decision = {
            "evidence_id": "reserve-1",
            "revision": 0,
            "proof_sha256": "e" * 64,
            "cost": 100000,
            "tokens": 10000,
            "owners": [{"principal_id": str(owner), "cost": 50000, "tokens": 5000}],
            "reason": "Independent provider records bound all missing exposure",
        }
        assert await revise_reserve(factory, epoch, decision)
        assert await revise_reserve(factory, epoch, decision)
        with pytest.raises(ValueError, match="conflict"):
            await revise_reserve(factory, epoch, {**decision, "cost": 90000})
        with pytest.raises(ValueError, match="revision"):
            await revise_reserve(factory, epoch, {**decision, "evidence_id": "stale"})
        async with admin_engine.connect() as c:
            assert (
                await c.execute(
                    text(
                        "SELECT reserve_cost,reserve_tokens,reserve_revision FROM platform_control"
                    )
                )
            ).one() == (100000, 10000, 1)
        assert await reopen(factory, epoch, {"evidence_id": "open-1", "proof_sha256": "d" * 64})
        async with admin_engine.connect() as c:
            assert await c.scalar(
                text("SELECT admission_open AND dispatch_open FROM platform_control")
            )
            assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 0
    finally:
        await reset_recovery(admin_engine, command_owner["X-Execution-Epoch"])


async def provision_current_key(
    client: AsyncClient, headers: dict[str, str], engine: AsyncEngine
) -> tuple[UUID, tuple[UUID, str]]:
    from task_platform.identity import issue_key

    async with engine.connect() as c:
        from task_platform.identity import key_digest

        owner = await c.scalar(
            text("SELECT principal_id FROM api_keys WHERE digest=:digest"),
            {"digest": key_digest(headers["Authorization"].removeprefix("Bearer "))},
        )
        assert isinstance(owner, UUID)
    return owner, await issue_key(sessions(engine), owner)


@pytest.mark.parametrize("epoch", [None, "obsolete"])
async def test_regeneration_requires_current_execution_epoch(
    client: AsyncClient,
    command_owner: dict[str, str],
    executor: Executor,
    epoch: str | None,
) -> None:
    from .test_accounting_admin import pending

    await pending(client, command_owner, executor)
    async with executor.factory() as s:
        task = (await s.execute(text("SELECT id,task_version FROM tasks"))).one()
    headers = {k: v for k, v in command_owner.items() if k != "X-Execution-Epoch"}
    if epoch is not None:
        headers["X-Execution-Epoch"] = epoch
    response = await client.post(
        f"/v1/tasks/{task[0]}/resolve",
        headers={**headers, "Idempotency-Key": "missing-epoch"},
        json={
            "expected_task_version": task[1],
            "action": "regenerate",
            "accept_overlap_cost_risk": True,
        },
    )
    assert response.status_code == (428 if epoch is None else 409)


@pytest.mark.parametrize(
    "scope",
    ["unbounded", "global_cost", "owner_cost", "unallocated_cost", "global_tokens", "owner_tokens"],
)
async def test_disaster_reserve_participates_in_admission_without_double_counting(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
) -> None:
    from task_platform.accounting import admission_reason
    from task_platform.execution import db_now, prefix
    from task_platform.task_models import Task

    monkeypatch.setenv("DISPATCH_ENABLED", "false")
    item = await create(client, command_owner)
    task_id = UUID(str(item["task_id"]))
    epoch = uuid4()
    factory = sessions(admin_engine)
    try:
        await begin_restore(factory, epoch, proof())
        async with admin_engine.connect() as c:
            owner = await c.scalar(
                text("SELECT principal_id FROM tasks WHERE id=:id"), {"id": task_id}
            )
        if scope != "unbounded":
            money = scope.endswith("cost")
            allocated = scope.startswith("owner")
            decision = {
                "evidence_id": "reserve-admission",
                "revision": 0,
                "proof_sha256": "e" * 64,
                "cost": 100 if money else 0,
                "tokens": 0 if money else 100,
                "owners": [
                    {
                        "principal_id": str(owner),
                        "cost": 50 if money else 0,
                        "tokens": 0 if money else 50,
                    }
                ]
                if allocated
                else [],
                "reason": "Bounded missing external exposure; independent evidence",
            }
            assert await revise_reserve(factory, epoch, decision)
        async with admin_engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE provider_admission SET global_daily_cost=:gc, "
                    "owner_daily_cost=:oc,global_daily_tokens=:gt,owner_daily_tokens=:ot"
                ),
                {
                    "gc": 100 if scope == "global_cost" else 100000000,
                    "oc": 50
                    if scope == "owner_cost"
                    else 100
                    if scope == "unallocated_cost"
                    else 10000000,
                    "gt": 100 if scope == "global_tokens" else 4000000,
                    "ot": 50 if scope == "owner_tokens" else 400000,
                },
            )
        async with factory.begin() as s:
            _, _, provider = await prefix(s, task_id)
            task = await s.get(Task, task_id)
            assert task
            reason = await admission_reason(s, task, provider, await db_now(s), 1, 1)
            assert reason == "capacity_wait", f"{scope} exposure was omitted from admission"
        assert await revise_reserve(
            factory,
            epoch,
            {
                "evidence_id": "cleared",
                "revision": 0 if scope == "unbounded" else 1,
                "proof_sha256": "f" * 64,
                "cost": 0,
                "tokens": 0,
                "owners": [],
                "reason": "Conclusive final provider evidence, no remaining missing exposure",
            },
        )
        async with factory.begin() as s:
            _, _, provider = await prefix(s, task_id)
            task = await s.get(Task, task_id)
            assert task
            assert await admission_reason(s, task, provider, await db_now(s), 1, 1) is None
            assert await s.scalar(text("SELECT count(*) FROM external_calls")) == 0
    finally:
        async with admin_engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE provider_admission SET global_daily_cost=100000000, "
                    "owner_daily_cost=10000000,global_daily_tokens=4000000,owner_daily_tokens=400000"
                )
            )
        await reset_recovery(admin_engine, command_owner["X-Execution-Epoch"])


async def test_release_requires_proof_and_associates_current_owner_authorization(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from task_platform.recovery import release_task

    monkeypatch.setenv("DISPATCH_ENABLED", "false")
    item = await create(client, command_owner)
    task_id = UUID(str(item["task_id"]))
    epoch = uuid4()
    factory = sessions(admin_engine)
    try:
        await begin_restore(factory, epoch, proof())
        owner, (key, token) = await provision_current_key(client, command_owner, admin_engine)
        await revise_reserve(
            factory,
            epoch,
            {
                "evidence_id": "zero",
                "revision": 0,
                "proof_sha256": "e" * 64,
                "cost": 0,
                "tokens": 0,
                "owners": [],
                "reason": "Independent evidence bounds all restored and missing effects",
            },
        )
        await reopen(factory, epoch, {"evidence_id": "open", "proof_sha256": "f" * 64})
        async with admin_engine.connect() as c:
            version = await c.scalar(
                text("SELECT task_version FROM tasks WHERE id=:id"), {"id": task_id}
            )
        decision = {
            "evidence_id": "release-1",
            "proof_sha256": "c" * 64,
            "task_id": str(task_id),
            "key_id": str(key),
            "expected_task_version": version,
            "no_unrecorded_effect": True,
            "action": "resume",
            "reason": "Independent records establish no lost authorization/effect",
        }
        with pytest.raises(ValueError):
            await release_task(factory, epoch, {**decision, "no_unrecorded_effect": False})
        assert await release_task(factory, epoch, decision)
        assert await release_task(factory, epoch, decision)
        async with admin_engine.connect() as c:
            row = (
                await c.execute(
                    text(
                        "SELECT state,key_id,execution_epoch,restore_uncertain "
                        "FROM tasks WHERE id=:id"
                    ),
                    {"id": task_id},
                )
            ).one()
            assert row == ("queued", key, epoch, True)
            assert (
                await c.scalar(text("SELECT principal_id FROM tasks WHERE id=:id"), {"id": task_id})
                == owner
            )
        assert token.startswith("atp_")
    finally:
        await reset_recovery(admin_engine, command_owner["X-Execution-Epoch"])


async def test_second_disaster_preserves_prior_terminal_quarantine_without_duplicate_events(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from task_platform.db import create_engine

    from .conftest import settings_for

    monkeypatch.setenv("DISPATCH_ENABLED", "false")
    expired = await create(client, command_owner)
    await create(client, command_owner, "surviving-nonterminal")
    migrator = create_engine(settings_for("migrator"))
    try:
        async with migrator.begin() as c:
            await c.execute(
                text(
                    "UPDATE tasks SET created_at=clock_timestamp()-interval '2 minutes', "
                    "deadline_at=clock_timestamp()-interval '1 second' WHERE id=:id"
                ),
                {"id": expired["task_id"]},
            )
        first_epoch = uuid4()
        assert await begin_restore(sessions(admin_engine), first_epoch, proof()) == {
            "quarantined": 2
        }
        async with admin_engine.connect() as c:
            before = (
                await c.execute(
                    text(
                        "SELECT state,task_version,event_sequence,restore_uncertain "
                        "FROM tasks WHERE id=:id"
                    ),
                    {"id": expired["task_id"]},
                )
            ).one()
            assert before[0] == "timed_out" and before[3]
        assert await begin_restore(
            sessions(admin_engine), uuid4(), proof(evidence_id="second-disaster")
        ) == {"quarantined": 1}
        async with admin_engine.connect() as c:
            assert (
                await c.execute(
                    text(
                        "SELECT state,task_version,event_sequence,restore_uncertain "
                        "FROM tasks WHERE id=:id"
                    ),
                    {"id": expired["task_id"]},
                )
            ).one() == before
            assert (
                await c.scalar(text("SELECT count(*) FROM tasks WHERE reason='restore_quarantine'"))
                == 2
            )
    finally:
        await migrator.dispose()
        await reset_recovery(admin_engine, command_owner["X-Execution-Epoch"])


@pytest.mark.parametrize("scope", ["global_cost", "owner_cost", "global_tokens", "owner_tokens"])
async def test_recovery_reopen_checks_restored_budgets_before_accepting_new_work(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
    scope: str,
) -> None:
    monkeypatch.setenv("DISPATCH_ENABLED", "false")
    owner = UUID((await client.get("/v1/me", headers=command_owner)).json()["principal_id"])
    epoch = uuid4()
    factory = sessions(admin_engine)
    try:
        await begin_restore(factory, epoch, proof())
        await revise_reserve(
            factory,
            epoch,
            {
                "evidence_id": "bounded",
                "revision": 0,
                "proof_sha256": "d" * 64,
                "cost": 100000001 if scope.endswith("cost") else 0,
                "tokens": 4000001 if scope.endswith("tokens") else 0,
                "owners": [
                    {
                        "principal_id": str(owner),
                        "cost": 100000001 if scope.endswith("cost") else 0,
                        "tokens": 4000001 if scope.endswith("tokens") else 0,
                    }
                ],
                "reason": "Independent records bound exposure above restored budget",
            },
        )
        async with admin_engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE provider_admission SET global_daily_cost=:gc, "
                    "owner_daily_cost=:oc,global_daily_tokens=:gt,owner_daily_tokens=:ot"
                ),
                {
                    "gc": 1000000000 if scope.startswith("owner") else 100000000,
                    "oc": 1000000000 if scope.startswith("global") else 10000000,
                    "gt": 10000000 if scope.startswith("owner") else 4000000,
                    "ot": 10000000 if scope.startswith("global") else 400000,
                },
            )
        with pytest.raises(ValueError, match="budget|capacity"):
            await reopen(factory, epoch, {"evidence_id": "unsafe-open", "proof_sha256": "e" * 64})
        async with admin_engine.connect() as c:
            assert not await c.scalar(
                text("SELECT admission_open OR dispatch_open FROM platform_control")
            )
    finally:
        async with admin_engine.begin() as c:
            await c.execute(
                text(
                    "UPDATE provider_admission SET global_daily_cost=100000000, "
                    "owner_daily_cost=10000000,global_daily_tokens=4000000,owner_daily_tokens=400000"
                )
            )
        await reset_recovery(admin_engine, command_owner["X-Execution-Epoch"])


async def test_ordinary_worker_transitions_do_not_require_private_restore_evidence(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    async with admin_engine.connect() as c:
        for role in ("atp_api", "atp_worker"):
            assert not await c.scalar(
                text("SELECT has_table_privilege(:role,'restore_decisions','SELECT')"),
                {"role": role},
            )
    async with executor.factory.begin() as s:
        # Exercise PostgreSQL's generic expression plan deterministically. The
        # full suite reaches this path after repeated trigger executions.
        await s.execute(text("SET plan_cache_mode=force_generic_plan"))
    await create(client, command_owner)
    lease = await executor.claim("ordinary-worker")
    assert lease
    call = uuid4()
    assert await executor.authorize(lease, call, b"{}")
    # Unknown transitions must still work without disclosing private administrator evidence.
    assert await executor.complete(lease, call, "unknown")
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT reason FROM tasks")) == "external_unknown"


async def test_reopen_checks_global_budget_when_backup_has_no_owners(
    admin_engine: AsyncEngine,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from task_platform.db import create_engine

    from .conftest import settings_for

    monkeypatch.setenv("DISPATCH_ENABLED", "false")
    migrator = create_engine(settings_for("migrator"))
    try:
        async with migrator.begin() as c:
            # Test-only definer fixture; all truncation is rolled back below.
            await c.execute(
                text(
                    "CREATE FUNCTION m7_empty_restore_fixture() RETURNS void "
                    "LANGUAGE plpgsql SECURITY DEFINER SET search_path=pg_catalog,public AS $$ "
                    "BEGIN TRUNCATE public.principals CASCADE; "
                    "TRUNCATE public.restore_decisions; END $$"
                )
            )
            await c.execute(text("REVOKE ALL ON FUNCTION m7_empty_restore_fixture() FROM PUBLIC"))
            await c.execute(
                text("GRANT EXECUTE ON FUNCTION m7_empty_restore_fixture() TO atp_admin")
            )
        async with admin_engine.connect() as connection:
            outer = await connection.begin()
            try:
                await connection.execute(text("SELECT m7_empty_restore_fixture()"))
                assert await connection.scalar(text("SELECT count(*) FROM principals")) == 0
                factory = async_sessionmaker(
                    connection, expire_on_commit=False, join_transaction_mode="create_savepoint"
                )
                epoch = uuid4()
                assert await begin_restore(factory, epoch, proof()) == {"quarantined": 0}
                cap = await connection.scalar(
                    text("SELECT global_daily_cost FROM provider_admission")
                )
                assert await revise_reserve(
                    factory,
                    epoch,
                    {
                        "evidence_id": "no-owner-reserve",
                        "revision": 0,
                        "proof_sha256": "d" * 64,
                        "cost": int(cap) + 1,
                        "tokens": 0,
                        "owners": [],
                        "reason": "Bounded global exposure with lost owner identities",
                    },
                )
                with pytest.raises(ValueError, match="budget|capacity"):
                    await reopen(
                        factory, epoch, {"evidence_id": "no-owner-open", "proof_sha256": "e" * 64}
                    )
            finally:
                await outer.rollback()
    finally:
        async with migrator.begin() as c:
            await c.execute(text("DROP FUNCTION IF EXISTS m7_empty_restore_fixture()"))
        await migrator.dispose()
