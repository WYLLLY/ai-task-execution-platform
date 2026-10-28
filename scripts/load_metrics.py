"""Versioned synthetic reference profile and honest missing-sample metrics."""

import math
from typing import Any


def corpus_content(index: int) -> str:
    fact = "Revenue is 42. " if index == 0 else f"Inventory record {index}. "
    return fact + "x" * (4096 - len(fact))


def reference_profile() -> dict[str, int]:
    return {
        "owners": 20,
        "documents_per_owner": 20,
        "document_bytes": 4096,
        "input_bytes": 4096,
        "warmup_seconds": 60,
        "tasks": 1000,
        "interval_seconds": 1,
        "drain_seconds": 900,
        "sse_connections": 100,
        "reads_per_second": 10,
        "worker_processes": 2,
        "slots_per_worker": 4,
        "requests_per_minute": 600,
        "tokens_per_minute": 1000000,
        "reservation_micro_usd": 1000,
        "input_tokens": 1000,
        "output_tokens": 200,
        "model_milliseconds": 500,
        "demo_milliseconds": 100,
    }


def percentiles(values: list[float]) -> dict[str, float | None]:
    ordered = sorted(values)
    return {
        f"p{p}": ordered[max(0, math.ceil(len(ordered) * p / 100) - 1)] if ordered else None
        for p in (50, 95, 99)
    }


def model_response(context: dict[str, Any]) -> dict[str, object]:
    outputs = [item for item in context["history"] if item.get("type") == "function_call_output"]
    action = (
        {
            "type": "report",
            "title": "Synthetic load report",
            "summary": "Revenue is 42.",
            "citations": outputs[-1]["output"]["excerpts"][:1],
        }
        if outputs
        else {
            "type": "function_call",
            "call_id": "load-read-1",
            "name": "search_documents",
            "arguments": {"query": "Revenue"},
        }
    )
    return {
        "outcome": "succeeded",
        "cost": 1000,
        "tokens": 1200,
        "observation": {"input_tokens": 1000, "output_tokens": 200},
        "response": {"status": "completed", "items": [action]},
    }


def reference_targets(values: dict[str, Any]) -> dict[str, bool]:
    return {
        "reference_pressure": values["sse_connected_slots"] == values["sse_peak"] == 100
        and values["read_count"] >= int(values["elapsed_seconds"] * 10) - 10
        and values["workers_alive"] is True
        and not values["errors"],
        "all_1000_succeeded": values["states"] == {"succeeded": 1000},
        "create_p95_under_300ms": values["admission_seconds"]["p95"] < 0.3,
        "sse_p95_under_2s": all(
            p["p95"] is not None and p["p95"] < 2 for p in values["sse_commit_seconds"].values()
        ),
        "no_duplicate_effects": values["remote_effects"] == values["demo_physical_writes"] == 1060,
        "no_duplicate_model_requests": values["model_physical_requests"]
        == values["distinct_model_calls"]
        == 2120,
    }
