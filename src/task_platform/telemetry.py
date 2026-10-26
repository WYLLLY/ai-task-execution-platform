"""Small OpenTelemetry spans with fixed names and a safe, local-only exporter."""

import logging
from collections.abc import Sequence
from uuid import UUID

from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import ReadableSpan, TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor, SpanExporter, SpanExportResult
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Link, NonRecordingSpan, SpanContext, TraceFlags

logger = logging.getLogger(__name__)


class SafeExporter(SpanExporter):
    def export(self, spans: Sequence[ReadableSpan]) -> SpanExportResult:
        for span in spans:
            context = span.get_span_context()
            if context is None:
                continue
            fields = {
                name: value
                for name, value in (span.attributes or {}).items()
                if name in {"task_id", "step_id", "attempt_id", "call_id"}
            }
            fields.update(
                event="trace.completed",
                trace_id=f"{context.trace_id:032x}",
                span_id=f"{context.span_id:016x}",
            )
            if span.links:
                fields["previous_span_id"] = f"{span.links[0].context.span_id:016x}"
            logger.info("", extra=fields)
        return SpanExportResult.SUCCESS


provider = TracerProvider(
    resource=Resource({"service.name": "ai-task-platform"}),
    sampler=ParentBased(TraceIdRatioBased(0.1)),
)
provider.add_span_processor(SimpleSpanProcessor(SafeExporter()))
tracer = provider.get_tracer("task_platform.execution", "1")


def attempt_context(
    task: UUID, step: UUID, attempt: UUID, previous: dict[str, str] | None
) -> dict[str, str]:
    parent = None
    links: list[Link] = []
    if previous:
        context = SpanContext(
            int(previous["trace_id"], 16),
            int(previous["span_id"], 16),
            is_remote=False,
            trace_flags=TraceFlags(int(previous.get("flags", "01"), 16)),
        )
        parent = trace.set_span_in_context(NonRecordingSpan(context))
        links = [Link(context)]
    # Only this short local operation is spanned; no task-lifetime span stays open.
    with tracer.start_as_current_span(
        "execution.claim",
        context=parent,
        links=links,
        attributes={"task_id": str(task), "step_id": str(step), "attempt_id": str(attempt)},
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        current = span.get_span_context()
        result = {
            "trace_id": f"{current.trace_id:032x}",
            "span_id": f"{current.span_id:016x}",
            "flags": f"{int(current.trace_flags):02x}",
        }
        if previous:
            result["previous_span_id"] = previous["span_id"]
        return result


def call_progress(
    context: dict[str, str] | None, task: UUID, step: UUID, attempt: UUID, call: UUID
) -> None:
    if context is None:
        return  # Legacy pre-M6 attempt: do not invent a historical trace parent.
    parent = SpanContext(
        int(context["trace_id"], 16),
        int(context["span_id"], 16),
        is_remote=False,
        trace_flags=TraceFlags(int(context.get("flags", "01"), 16)),
    )
    identifiers = {
        "task_id": str(task),
        "step_id": str(step),
        "attempt_id": str(attempt),
        "call_id": str(call),
    }
    with tracer.start_as_current_span(
        "execution.authorized",
        context=trace.set_span_in_context(NonRecordingSpan(parent)),
        attributes=identifiers,
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        current = span.get_span_context()
        logger.info(
            "",
            extra={
                **identifiers,
                "event": "execution.progress",
                "trace_id": f"{current.trace_id:032x}",
                "span_id": f"{current.span_id:016x}",
            },
        )
