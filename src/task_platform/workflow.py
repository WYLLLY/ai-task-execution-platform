"""Sequential simulated analysis with PostgreSQL checkpoints between every unit."""

import time
from datetime import datetime, timedelta
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from pydantic import ValidationError
from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from task_platform.agent_protocol import (
    Citation,
    Excerpt,
    FunctionCall,
    Report,
    Search,
    build_context,
    encode,
    parse_response,
)
from task_platform.command_schemas import TaskInput
from task_platform.corpus import Document, authorized_documents
from task_platform.execution import (
    Executor,
    Lease,
    close_attempt,
    db_now,
    event,
    locked_lease,
    owns,
    prefix,
    stop_before_call,
)
from task_platform.execution_models import ExternalCall
from task_platform.identity import NotFoundError
from task_platform.models import APIKey, PlatformControl, Principal
from task_platform.report_transport import ReportTransport, validate_proof
from task_platform.task_models import Step, Task
from task_platform.tasks import PINNED_VERSIONS, retry_transaction


async def sources(session: AsyncSession, task: Task) -> list[Document]:
    return await authorized_documents(
        session, task.principal_id, TaskInput.model_validate(task.input).documents
    )


def excerpt_result(doc: Document, start: int, end: int) -> dict[str, object]:
    if not 0 <= start < end <= len(doc.content) or end - start > 2000:
        raise ValueError("invalid_tool_reference")
    return {
        "id": str(doc.id),
        "version": doc.version,
        "start": start,
        "end": end,
        "quote": doc.content[start:end],
        "content_hash": doc.content_hash,
    }


def check_citation(citation: Citation, docs: list[Document]) -> None:
    doc = next(
        (d for d in docs if str(d.id) == citation.id and d.version == citation.version), None
    )
    if doc is None or excerpt_result(doc, citation.start, citation.end) != citation.model_dump():
        raise ValueError("invalid_citation")


async def next_step(session: AsyncSession, task: Task, step: Step, path: str) -> None:
    task.state = "running"
    await session.flush()  # retire the old executable row before the partial unique index
    session.add(
        Step(
            id=uuid4(),
            task_id=task.id,
            principal_id=task.principal_id,
            path=path,
            state="ready",
            generation=0,
        )
    )


class AnalysisExecutor(Executor):
    handlers: frozenset[tuple[str, str]] = frozenset({("analysis", "1")})

    def handler_available(self, task: Task, step: Step | None = None) -> bool:
        return (
            task.workflow_name,
            task.workflow_version,
        ) in self.handlers and task.versions == PINNED_VERSIONS

    async def charge_checkpoint(self, session: AsyncSession, task: Task, value: object) -> bool:
        size = len(encode(value))
        # These objects are already locked by prefix(), before task and step.
        platform = await session.get(PlatformControl, 1)
        owner = await session.get(Principal, task.principal_id)
        if (
            platform is None
            or owner is None
            or owner.storage_bytes + size > owner.max_storage_bytes
            or platform.storage_bytes + size > platform.max_storage_bytes
        ):
            return False
        owner.storage_bytes += size
        platform.storage_bytes += size
        task.storage_bytes += size
        return True

    def payload_storage_charge(self, step: Step, payload: bytes) -> int:
        if step.path == "analysis/save-report" and step.payload is None:
            # Reserve the step payload and terminal report copy before the remote effect,
            # as well as the transmission payload and minimal proof/control metadata.
            return len(payload) * 3 + 4096
        return super().payload_storage_charge(step, payload)

    def call_kind(self, step: Step) -> str:
        return "demo-v1" if step.path == "analysis/save-report" else "simulator-v1"

    async def saved_report(self, session: AsyncSession, task: Task) -> dict[str, object]:
        results = (
            await session.scalars(
                select(Step.result)
                .where(
                    Step.task_id == task.id, Step.state == "succeeded", Step.path.like("%/model")
                )
                .order_by(Step.path.desc())
            )
        ).all()
        for result in results:
            if result is not None:
                validated = parse_response(result)
                items = validated["items"]
                if isinstance(items, list):
                    for item in items:
                        if item["type"] == "report":
                            report = Report.model_validate(item)
                            docs = await sources(session, task)
                            for citation in report.citations:
                                check_citation(citation, docs)
                            return report.model_dump(mode="json")
        raise ValueError("Missing validated report")

    async def authorization_reason(
        self, session: AsyncSession, task: Task, step: Step, payload: bytes
    ) -> str | None:
        if (
            not self.handler_available(task)
            or task.workflow_name != "analysis"
            or task.workflow_version != "1"
        ):
            return "handler_unavailable"
        try:
            await sources(session, task)
        except NotFoundError:
            return "permission_revoked"
        if step.path == "analysis/save-report" and payload:
            expected = step.payload or encode(
                {
                    "owner": str(task.principal_id),
                    "operation": str(uuid5(NAMESPACE_URL, f"{task.id}:save-report-v1")),
                    "report": await self.saved_report(session, task),
                }
            )
            now = await db_now(session)
            if payload != expected or (
                step.first_authorized_at is not None
                and (now - step.first_authorized_at).total_seconds() >= 86400
            ):
                return "task_limit"
        return None

    async def record_authorization(
        self, task: Task, step: Step, payload: bytes, now: datetime
    ) -> None:
        if step.path == "analysis/save-report":
            step.payload = payload
            step.operation_key = uuid5(NAMESPACE_URL, f"{task.id}:save-report-v1")
            if step.first_authorized_at is None:
                step.first_authorized_at = now
            task.tool_calls += 1

    async def context(self, lease: Lease) -> bytes | None:
        async with self.factory.begin() as session:
            platform, owner, _ = await prefix(session, lease.task_id)
            rows = await locked_lease(session, lease)
            if rows is None or not await owns(session, lease, rows):
                return None
            task, step, _ = rows
            reason = await self.authorization_reason(session, task, step, b"")
            if reason:
                stop_before_call(session, rows, platform, owner, await db_now(session), reason)
                return None
            docs = await sources(session, task)
            history: list[dict[str, object]] = []
            previous = (
                await session.scalars(
                    select(Step)
                    .where(Step.task_id == task.id, Step.state == "succeeded")
                    .order_by(Step.path)
                )
            ).all()
            try:
                for prior in previous:
                    if prior.result is None:
                        raise ValueError("invalid_checkpoint")
                    items = prior.result.get("items")
                    if not isinstance(items, list):
                        raise ValueError("invalid_checkpoint")
                    for item in items:
                        if not isinstance(item, dict):
                            raise ValueError("invalid_checkpoint")
                        if item.get("type") == "function_call_output":
                            output = item.get("output")
                            if not isinstance(output, dict) or not isinstance(
                                output.get("excerpts"), list
                            ):
                                raise ValueError("invalid_checkpoint")
                            for citation in output["excerpts"]:
                                check_citation(Citation.model_validate(citation), docs)
                        history.append(item)
                return build_context(TaskInput.model_validate(task.input).texts, history)
            except ValueError, ValidationError:
                if not await owns(session, lease, rows):
                    return None
                close_attempt(step, rows[2], await db_now(session), "failed", "context_limit")
                step.state = task.state = "failed"
                task.reason = "context_limit"
                task.task_version += 1
                owner.open_tasks -= 1
                platform.open_tasks -= 1
                event(session, task, "task.failed")
                return None

    async def accept_model(
        self,
        lease: Lease,
        call_id: UUID,
        value: object,
        *,
        cost: int | None = None,
        tokens: int | None = None,
    ) -> bool:
        return await self.complete(
            lease, call_id, "succeeded", cost=cost, tokens=tokens, result=value
        )

    async def advance(
        self, session: AsyncSession, task: Task, step: Step, call: ExternalCall, result: object
    ) -> None:
        if step.path == "analysis/save-report":
            try:
                if step.payload is None or step.operation_key is None:
                    raise ValueError("Missing operation identity")
                proof = validate_proof(
                    result, str(task.principal_id), str(step.operation_key), step.payload
                )
                import json

                report_payload = json.loads(step.payload)
                step.result = proof
                task.result = {
                    "simulated": True,
                    "report": report_payload["report"],
                    "operation": str(step.operation_key),
                    "effect_id": proof["effect_id"],
                }
            except ValueError, ValidationError:
                step.state = task.state = "failed"
                task.reason = "invalid_report_evidence"
            return
        try:
            response = parse_response(result)
            if not await self.charge_checkpoint(session, task, response):
                step.state = task.state = "failed"
                task.reason = "storage_limit"
                return
            docs = await sources(session, task)
            items = response["items"]
            if not isinstance(items, list):
                raise ValueError("invalid_model_output")
            action = next(item for item in items if item["type"] != "reasoning")
            step.result = response
            turn = int(step.path.split("/")[1].split("-")[1])
            if action["type"] == "function_call":
                # Call identities must remain unique throughout the durable protocol history.
                previous = (
                    await session.scalars(
                        select(Step.result).where(
                            Step.task_id == task.id, Step.id != step.id, Step.state == "succeeded"
                        )
                    )
                ).all()
                for prior in previous:
                    prior_items = prior.get("items", []) if prior else []
                    if isinstance(prior_items, list) and any(
                        isinstance(i, dict)
                        and i.get("type") == "function_call"
                        and i.get("call_id") == action["call_id"]
                        for i in prior_items
                    ):
                        raise ValueError("duplicate_call_id")
                await next_step(session, task, step, f"analysis/turn-{turn}/tool")
            else:
                report = Report.model_validate(action)
                for citation in report.citations:
                    check_citation(citation, docs)
                await next_step(session, task, step, "analysis/save-report")
        except ValueError, ValidationError, NotFoundError:
            step.state = task.state = "failed"
            step.result = None
            task.reason = "invalid_model_output"

    async def run_tool(self, lease: Lease) -> bool:
        async def operation() -> bool:
            async with self.factory.begin() as session:
                platform, owner, _ = await prefix(session, lease.task_id)
                rows = await locked_lease(session, lease)
                if rows is None or not await owns(session, lease, rows):
                    return False
                task, step, attempt = rows
                reason = await self.authorization_reason(session, task, step, b"")
                key = await session.get(APIKey, task.key_id)
                now = await db_now(session)
                if (
                    not owner.active
                    or key is None
                    or key.revoked_at is not None
                    or (key.expires_at is not None and key.expires_at <= now)
                ):
                    reason = "permission_revoked"
                if not platform.dispatch_open:
                    reason = "dispatch_closed"
                if reason:
                    stop_before_call(session, rows, platform, owner, now, reason)
                    return True
                if task.tool_calls >= int(str(task.limits["tool_calls"])):
                    stop_before_call(session, rows, platform, owner, now, "task_limit")
                    return True
                task.tool_calls += 1
                await session.flush()
                try:
                    model_path = step.path.removesuffix("/tool") + "/model"
                    prior = (
                        await session.scalars(
                            select(Step).where(
                                Step.task_id == task.id,
                                Step.path == model_path,
                                Step.state == "succeeded",
                            )
                        )
                    ).one()
                    if prior.result is None:
                        raise ValueError("invalid_tool_reference")
                    validated = parse_response(prior.result)
                    items = validated["items"]
                    if not isinstance(items, list):
                        raise ValueError("invalid_tool_reference")
                    action = FunctionCall.model_validate(
                        next(i for i in items if i["type"] == "function_call")
                    )
                    docs = await sources(session, task)
                    if action.name == "search_documents":
                        query = Search.model_validate(action.arguments).query
                        # PostgreSQL full-text rank, then immutable identifier/version tie-break.
                        query_expr = func.plainto_tsquery("simple", query)
                        vector = func.to_tsvector("simple", Document.content)
                        matching = (
                            await session.scalars(
                                select(Document)
                                .where(
                                    Document.principal_id == task.principal_id,
                                    Document.readable,
                                    tuple_(Document.id, Document.version).in_(
                                        [(d.id, d.version) for d in docs]
                                    ),
                                    vector.op("@@")(query_expr),
                                )
                                .order_by(
                                    func.ts_rank(vector, query_expr).desc(),
                                    Document.id,
                                    Document.version,
                                )
                                .limit(5)
                            )
                        ).all()
                        matches = matching
                        excerpts = [
                            excerpt_result(d, 0, min(len(d.content), 2000)) for d in matches
                        ]
                    else:
                        ref = Excerpt.model_validate(action.arguments)
                        doc = next(
                            (d for d in docs if str(d.id) == ref.id and d.version == ref.version),
                            None,
                        )
                        if doc is None:
                            raise ValueError("invalid_tool_reference")
                        excerpts = [excerpt_result(doc, ref.start, ref.end)]
                    checkpoint: dict[str, object] = {
                        "items": [
                            {
                                "type": "function_call_output",
                                "call_id": action.call_id,
                                "output": {"excerpts": excerpts},
                            }
                        ]
                    }
                    if len(encode(checkpoint)) > 65536:
                        raise ValueError("tool_result_limit")
                    if not await self.charge_checkpoint(session, task, checkpoint):
                        raise ValueError("storage_limit")
                    if not await owns(session, lease, rows):
                        return False
                    step.result = checkpoint
                    step.state = "succeeded"
                    close_attempt(step, attempt, await db_now(session), "succeeded", "local_tool")
                    task.task_version += 1
                    turn = int(step.path.split("/")[1].split("-")[1])
                    await next_step(session, task, step, f"analysis/turn-{turn + 1}/model")
                    event(session, task, "step.succeeded")
                except ValueError, ValidationError, NotFoundError:
                    if not await owns(session, lease, rows):
                        return False
                    close_attempt(
                        step, attempt, await db_now(session), "failed", "invalid_tool_reference"
                    )
                    step.state = task.state = "failed"
                    task.reason = "invalid_tool_reference"
                    task.task_version += 1
                    owner.open_tasks -= 1
                    platform.open_tasks -= 1
                    event(session, task, "task.failed")
                return True

        return await retry_transaction(operation)

    async def report_payload(self, lease: Lease) -> bytes | None:
        async with self.factory.begin() as session:
            platform, owner, _ = await prefix(session, lease.task_id)
            rows = await locked_lease(session, lease)
            if rows is None or not await owns(session, lease, rows):
                return None
            task, step, _ = rows
            if step.path != "analysis/save-report":
                return None
            reason = await self.authorization_reason(session, task, step, b"")
            if reason:
                stop_before_call(session, rows, platform, owner, await db_now(session), reason)
                return None
            if step.payload is not None:
                return step.payload
            return encode(
                {
                    "owner": str(task.principal_id),
                    "operation": str(uuid5(NAMESPACE_URL, f"{task.id}:save-report-v1")),
                    "report": await self.saved_report(session, task),
                }
            )

    async def recover_reports(self, transport: ReportTransport) -> bool:
        async with self.control() as session:
            candidates = (
                await session.scalars(
                    select(Step.task_id)
                    .join(Task, Task.id == Step.task_id)
                    .where(
                        Task.state == "suspended",
                        Task.reason == "external_unknown",
                        Step.path == "analysis/save-report",
                        Step.state == "unknown",
                        Step.available_at <= func.clock_timestamp(),
                    )
                    .limit(100)
                )
            ).all()
        processed = False
        for task_id in candidates:
            async with self.factory.begin() as session:
                platform, owner, _ = await prefix(session, task_id)
                task = (
                    await session.scalars(select(Task).where(Task.id == task_id).with_for_update())
                ).one()
                step = (
                    await session.scalars(
                        select(Step)
                        .where(Step.task_id == task_id, Step.path == "analysis/save-report")
                        .with_for_update()
                    )
                ).one()
                measured_at = time.monotonic()
                now = await db_now(session)
                if (
                    task.state != "suspended"
                    or task.reason != "external_unknown"
                    or step.state != "unknown"
                    or step.available_at > now
                    or task.deadline_at <= now
                ):
                    continue
                if (
                    step.payload is None
                    or step.operation_key is None
                    or step.first_authorized_at is None
                    or now >= step.first_authorized_at + timedelta(hours=24)
                ):
                    continue
                if (
                    not platform.dispatch_open
                    or task.execution_epoch != self.epoch
                    or platform.execution_epoch != self.epoch
                    or not owner.active
                ):
                    continue
                key = await session.get(APIKey, task.key_id)
                if (
                    key is None
                    or key.revoked_at is not None
                    or (key.expires_at is not None and key.expires_at <= now)
                ):
                    continue
                if await self.authorization_reason(session, task, step, b""):
                    continue
                if step.probe_count >= 3:
                    continue
                call_id, generation = step.selected_call_id, step.generation
                if call_id is None:
                    continue
                payload, operation = step.payload, step.operation_key
                owner_id = task.principal_id
                step.probe_count += 1
                step.available_at = now + timedelta(seconds=5)
                remaining = (task.deadline_at - now).total_seconds()
            # No application connection/transaction crosses this HTTP failure boundary.
            proof = await transport.lookup(
                str(owner_id), str(operation), remaining - (time.monotonic() - measured_at)
            )
            processed = True
            async with self.factory.begin() as session:
                platform, owner, _ = await prefix(session, task_id)
                task = (
                    await session.scalars(select(Task).where(Task.id == task_id).with_for_update())
                ).one()
                step = (
                    await session.scalars(
                        select(Step)
                        .where(Step.task_id == task_id, Step.path == "analysis/save-report")
                        .with_for_update()
                    )
                ).one()
                call = (
                    await session.scalars(
                        select(ExternalCall).where(ExternalCall.id == call_id).with_for_update()
                    )
                ).one()
                now = await db_now(session)
                accepted = (
                    task.state == "suspended"
                    and task.reason == "external_unknown"
                    and step.state == "unknown"
                    and step.selected_call_id == call_id
                    and step.generation == generation
                    and task.deadline_at > now
                    and platform.execution_epoch == self.epoch == task.execution_epoch
                )
                if proof is not None:
                    try:
                        proof = validate_proof(proof, str(owner_id), str(operation), payload)
                    except ValueError:
                        proof = None
                if proof is not None:
                    if call.state in {"unknown", "authorized"}:
                        call.state = "succeeded"
                    if accepted:
                        step.state = task.state = "succeeded"
                        task.task_version += 1
                        task.reason = None
                        with session.no_autoflush:
                            await self.advance(session, task, step, call, proof)
                        owner.open_tasks -= 1
                        platform.open_tasks -= 1
                        event(session, task, "call.reconciled")
                        event(session, task, "task.succeeded")
                elif (
                    accepted
                    and step.probe_count == 3
                    and step.failures < 2
                    and task.tool_calls < int(str(task.limits["tool_calls"]))
                ):
                    # Absence is not proof of no effect. Same business identity and bytes
                    # make a later bounded resend safe against a delayed first send.
                    step.failures += 1
                    step.selected_call_id = None
                    step.generation += 1
                    step.state = "ready"
                    task.state = "queued"
                    task.reason = None
                    task.task_version += 1
                    event(session, task, "task.resumed")
        return processed
