"""M4 safety regressions: handlers, storage, authorization, immutable sources."""

from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from task_platform.corpus import Document, revoke_document
from task_platform.db import create_engine, sessions
from task_platform.execution import Executor
from task_platform.task_models import Task

from .conftest import settings_for
from .test_execution import executor as executor
from .test_execution_calls import dispatch as dispatch
from .test_workflow_reports import REPORT
from .test_workflows import analysis, dossier, model_turn, response

pytestmark = pytest.mark.integration


async def test_unavailable_handler_suspends_at_claim_without_attempt(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    await dossier(client, command_owner, admin_engine)
    worker = analysis(executor)
    worker.handlers = frozenset()
    assert await worker.claim("missing") is None
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT reason FROM tasks")) == "handler_unavailable"
        assert await c.scalar(text("SELECT state FROM tasks")) == "suspended"
        assert await c.scalar(text("SELECT count(*) FROM attempts")) == 0


async def test_report_response_storage_is_bounded_before_continuation(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    await dossier(client, command_owner, admin_engine)
    worker = analysis(executor)
    lease = await worker.claim("model")
    assert lease
    payload = await worker.context(lease)
    assert payload
    call = uuid4()
    assert await worker.authorize(lease, call, payload)
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "UPDATE principals SET max_storage_bytes=storage_bytes WHERE id=(SELECT "
                "principal_id FROM tasks)"
            )
        )
    assert await worker.accept_model(lease, call, REPORT, cost=2, tokens=1)
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "failed"
        assert await c.scalar(text("SELECT reason FROM tasks")) == "storage_limit"
        assert await c.scalar(text("SELECT confirmed_cost FROM call_costs")) == 2


async def test_report_unknown_cannot_use_llm_regenerate_command(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    await dossier(client, command_owner, admin_engine)
    await model_turn(analysis(executor), REPORT)
    worker = analysis(executor)
    lease = await worker.claim("save")
    assert lease
    payload = await worker.report_payload(lease)
    assert payload
    call = uuid4()
    assert await worker.authorize(lease, call, payload)
    assert await worker.complete(lease, call, "unknown")
    async with admin_engine.connect() as c:
        task_id, version = (await c.execute(text("SELECT id,task_version FROM tasks"))).one()
    resolved = await client.post(
        f"/v1/tasks/{task_id}/resolve",
        headers={**command_owner, "Idempotency-Key": "wrong-resolution"},
        json={
            "expected_task_version": version,
            "action": "regenerate",
            "accept_overlap_cost_risk": True,
        },
    )
    assert resolved.status_code == 409


async def test_revoked_source_cannot_execute_local_tool(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    owner, doc = await dossier(client, command_owner, admin_engine)
    await model_turn(analysis(executor), response())
    lease = await analysis(executor).claim("tool")
    assert lease
    await revoke_document(sessions(admin_engine), owner, doc, 1)
    assert await analysis(executor).run_tool(lease)
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT state FROM tasks")) == "suspended"
        assert await c.scalar(text("SELECT tool_calls FROM tasks")) == 0


async def test_documents_are_immutable_even_for_admin_and_runtime_cannot_import(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
) -> None:
    _, doc = await dossier(client, command_owner, admin_engine)
    migrator = create_engine(settings_for("migrator"))
    try:
        for statement in (
            "UPDATE documents SET content='changed' WHERE id=:id",
            "DELETE FROM documents WHERE id=:id",
        ):
            async with migrator.begin() as c:
                with pytest.raises(DBAPIError):
                    await c.execute(text(statement), {"id": doc})
    finally:
        await migrator.dispose()
    for role in ("api", "worker"):
        engine = create_engine(settings_for(role))
        try:
            async with engine.begin() as c:
                with pytest.raises(DBAPIError):
                    await c.execute(
                        text("UPDATE documents SET readable=false WHERE id=:id"), {"id": doc}
                    )
        finally:
            await engine.dispose()


async def test_save_capacity_wait_does_not_consume_tool_or_first_authorization(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    await dossier(client, command_owner, admin_engine)
    await model_turn(analysis(executor), REPORT)
    worker = analysis(executor)
    lease = await worker.claim("save")
    assert lease
    payload = await worker.report_payload(lease)
    assert payload
    async with admin_engine.begin() as c:
        await c.execute(
            text(
                "UPDATE principals SET max_storage_bytes=storage_bytes WHERE id=(SELECT "
                "principal_id FROM tasks)"
            )
        )
    assert await worker.authorize(lease, uuid4(), payload) is None
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT tool_calls FROM tasks")) == 0
        assert (
            await c.scalar(
                text("SELECT first_authorized_at FROM steps WHERE path='analysis/save-report'")
            )
            is None
        )


async def test_resolution_revalidates_revoked_document_rights(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    owner, doc = await dossier(client, command_owner, admin_engine)
    lease = await analysis(executor).claim("read")
    assert lease
    await revoke_document(sessions(admin_engine), owner, doc, 1)
    assert await analysis(executor).context(lease) is None
    async with admin_engine.connect() as c:
        task_id, version = (await c.execute(text("SELECT id,task_version FROM tasks"))).one()
    result = await client.post(
        f"/v1/tasks/{task_id}/resolve",
        headers={**command_owner, "Idempotency-Key": "resume-revoked"},
        json={"expected_task_version": version, "action": "resume"},
    )
    assert result.status_code == 409


async def test_local_tool_cannot_progress_after_deadline_during_read(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import time

    import task_platform.workflow as workflow

    await dossier(client, command_owner, admin_engine)
    await model_turn(analysis(executor), response())
    worker = analysis(executor)
    lease = await worker.claim("slow-local")
    assert lease
    migrator = create_engine(settings_for("migrator"))
    try:
        async with migrator.begin() as c:
            await c.execute(
                text("UPDATE tasks SET deadline_at=clock_timestamp()+interval '150 milliseconds'")
            )
        original = workflow.excerpt_result

        def slow_read(doc: Document, start: int, end: int) -> dict[str, object]:
            time.sleep(0.25)
            return original(doc, start, end)

        monkeypatch.setattr(workflow, "excerpt_result", slow_read)
        assert not await worker.run_tool(lease)
        await worker.maintenance()
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT state FROM tasks")) == "timed_out"
            assert (
                await c.scalar(
                    text(
                        "SELECT count(*) FROM steps WHERE state IN "
                        "('ready','running','unknown','retry_wait')"
                    )
                )
                == 0
            )
    finally:
        await migrator.dispose()


async def test_search_never_materializes_unpinned_document_bodies(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    from sqlalchemy import event
    from sqlalchemy.orm import QueryContext

    from task_platform.corpus import Document, import_document

    owner, pinned = await dossier(client, command_owner, admin_engine)
    for _ in range(8):
        await import_document(sessions(admin_engine), owner, uuid4(), "Revenue " * 100)
    await model_turn(analysis(executor), response())
    lease = await analysis(executor).claim("bounded-search")
    assert lease
    loaded: list[object] = []

    def capture(doc: Document, context: QueryContext) -> None:
        loaded.append(doc.id)

    event.listen(Document, "load", capture)
    try:
        assert await analysis(executor).run_tool(lease)
    finally:
        event.remove(Document, "load", capture)
    assert loaded and set(loaded) == {pinned}


@pytest.mark.parametrize(
    "privilege,target",
    [("SELECT", "documents"), ("UPDATE(tool_calls)", "tasks"), ("UPDATE(payload)", "steps")],
)
async def test_m4_worker_preflight_requires_corpus_and_checkpoint_privileges(
    privilege: str, target: str
) -> None:
    from task_platform.health import check_database

    worker = create_engine(settings_for("worker"))
    migrator = create_engine(settings_for("migrator"))
    try:
        async with migrator.begin() as c:
            await c.execute(text(f"REVOKE {privilege} ON {target} FROM atp_worker"))
        _, compatible, _ = await check_database(worker, settings_for("worker"))
        assert not compatible
    finally:
        async with migrator.begin() as c:
            await c.execute(text(f"GRANT {privilege} ON {target} TO atp_worker"))
        await worker.dispose()
        await migrator.dispose()


async def test_save_reserves_storage_for_exact_payload_and_terminal_copy(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    await dossier(client, command_owner, admin_engine)
    await model_turn(analysis(executor), REPORT)
    worker = analysis(executor)
    lease = await worker.claim("save-storage")
    assert lease
    payload = await worker.report_payload(lease)
    assert payload
    async with admin_engine.connect() as c:
        before = await c.scalar(text("SELECT storage_bytes FROM tasks"))
    assert await worker.authorize(lease, uuid4(), payload)
    async with admin_engine.connect() as c:
        after = await c.scalar(text("SELECT storage_bytes FROM tasks"))
    assert after - before >= 3 * len(payload) + 4096


async def test_document_revocation_committed_while_B_waits_prevents_dispatch(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    import asyncio

    _, doc = await dossier(client, command_owner, admin_engine)
    worker = analysis(executor)
    lease = await worker.claim("waiting-B")
    assert lease
    payload = await worker.context(lease)
    assert payload
    async with admin_engine.begin() as blocker:
        await blocker.execute(text("SELECT id FROM platform_control FOR UPDATE"))
        await blocker.execute(text("UPDATE documents SET readable=false WHERE id=:id"), {"id": doc})
        waiting = asyncio.create_task(worker.authorize(lease, uuid4(), payload))
        async with admin_engine.connect() as observer:
            await observer.execution_options(isolation_level="AUTOCOMMIT")
            for _ in range(100):
                blocked = await observer.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity WHERE usename='atp_worker' "
                        "AND cardinality(pg_blocking_pids(pid))>0"
                    )
                )
                if blocked:
                    break
                await asyncio.sleep(0.005)
            assert blocked
    assert await waiting is None
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT count(*) FROM external_calls")) == 0
        assert await c.scalar(text("SELECT reason FROM tasks")) == "permission_revoked"


async def test_concurrent_same_tool_lease_accepts_one_progression(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    import asyncio

    await dossier(client, command_owner, admin_engine)
    await model_turn(analysis(executor), response())
    lease = await analysis(executor).claim("tool")
    assert lease
    accepted = await asyncio.gather(
        analysis(executor).run_tool(lease), analysis(executor).run_tool(lease)
    )
    assert accepted.count(True) == 1
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT tool_calls FROM tasks")) == 1
        assert (
            await c.scalar(text("SELECT count(*) FROM steps WHERE path='analysis/turn-2/model'"))
            == 1
        )


@pytest.mark.parametrize("stage", ["corpus", "tool", "report"])
async def test_legacy_simulation_handler_cannot_consume_m4_work(
    stage: str,
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
) -> None:
    from .test_commands import create

    if stage == "corpus":
        await dossier(client, command_owner, admin_engine)
    else:
        await create(client, command_owner)
    if stage in ("tool", "report"):
        await model_turn(analysis(executor), response() if stage == "tool" else REPORT)
    assert await executor.claim("legacy") is None
    async with admin_engine.connect() as c:
        assert await c.scalar(text("SELECT reason FROM tasks")) == "handler_unavailable"
        assert await c.scalar(text("SELECT count(*) FROM attempts WHERE state='active'")) == 0


async def test_model_C_cannot_select_continuation_after_deadline_during_source_read(
    client: AsyncClient,
    command_owner: dict[str, str],
    admin_engine: AsyncEngine,
    executor: Executor,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    import task_platform.workflow as workflow

    await dossier(client, command_owner, admin_engine)
    worker = analysis(executor)
    lease = await worker.claim("slow-C")
    assert lease
    payload = await worker.context(lease)
    assert payload
    call = uuid4()
    assert await worker.authorize(lease, call, payload)
    original = workflow.sources

    async def delayed_sources(session: AsyncSession, task: Task) -> list[Document]:
        await asyncio.sleep(0.3)
        return await original(session, task)

    monkeypatch.setattr(workflow, "sources", delayed_sources)
    migrator = create_engine(settings_for("migrator"))
    try:
        async with migrator.begin() as c:
            await c.execute(
                text("UPDATE tasks SET deadline_at=clock_timestamp()+interval '150 milliseconds'")
            )
        assert not await worker.accept_model(lease, call, REPORT, cost=2, tokens=1)
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT count(*) FROM steps")) == 1
            assert await c.scalar(text("SELECT confirmed_cost FROM call_costs")) == 2
        await worker.maintenance()
        async with admin_engine.connect() as c:
            assert await c.scalar(text("SELECT state FROM tasks")) == "timed_out"
    finally:
        await migrator.dispose()
