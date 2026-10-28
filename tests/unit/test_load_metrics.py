"""The reference profile cannot be silently reduced to make a benchmark pass."""

from pathlib import Path

import pytest

from scripts.load_metrics import model_response, percentiles, reference_profile


def test_reference_corpus_fits_real_context_limit_for_second_turn() -> None:
    from scripts.load_metrics import corpus_content
    from task_platform.agent_protocol import build_context

    docs = [corpus_content(index) for index in range(20)]
    assert all(len(doc.encode()) == 4096 for doc in docs)
    matches = [doc for doc in docs if "Revenue" in doc][:5]
    history: list[dict[str, object]] = [
        {
            "type": "function_call_output",
            "call_id": "load-read-1",
            "output": {"excerpts": [{"quote": doc[:2000]} for doc in matches]},
        }
    ]
    assert len(build_context(["x" * 4096], history)) <= 12000


def test_exact_approved_reference_profile() -> None:
    assert reference_profile() == {
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


@pytest.mark.parametrize("values,expected", [([], None), ([3.0], 3.0), ([1.0, 2.0, 3.0], 3.0)])
def test_percentiles_never_fabricate_zero_for_missing_samples(
    values: list[float], expected: float | None
) -> None:
    assert percentiles(values)["p95"] == expected
    assert values == sorted(values)


def test_model_profile_preserves_calls_citations_and_billable_usage() -> None:
    first = model_response({"history": []})
    assert first["cost"] == 1000 and first["tokens"] == 1200
    response = first["response"]
    assert isinstance(response, dict)
    assert response["items"] == [
        {
            "type": "function_call",
            "call_id": "load-read-1",
            "name": "search_documents",
            "arguments": {"query": "Revenue"},
        }
    ]
    citation = {"id": "synthetic-pinned-reference", "quote": "Revenue is 42."}
    second = model_response(
        {"history": [{"type": "function_call_output", "output": {"excerpts": [citation]}}]}
    )
    report = second["response"]
    assert isinstance(report, dict)
    assert report["items"] == [
        {
            "type": "report",
            "title": "Synthetic load report",
            "summary": "Revenue is 42.",
            "citations": [citation],
        }
    ]


@pytest.mark.parametrize(
    "broken", ["errors", "sse_connected_slots", "sse_peak", "read_count", "workers_alive"]
)
def test_reference_gate_rejects_missing_pressure_or_dead_children(broken: str) -> None:
    from typing import Any

    from scripts.load_metrics import reference_targets

    values: dict[str, Any] = {
        "states": {"succeeded": 1000},
        "admission_seconds": {"p95": 0.01},
        "sse_commit_seconds": {"direct": {"p95": 0.01}, "proxy": {"p95": 0.02}},
        "remote_effects": 1060,
        "demo_physical_writes": 1060,
        "model_physical_requests": 2120,
        "distinct_model_calls": 2120,
        "errors": [],
        "sse_connected_slots": 100,
        "sse_peak": 100,
        "read_count": 10000,
        "elapsed_seconds": 1000,
        "workers_alive": True,
    }
    assert all(reference_targets(values).values())
    values[broken] = ["lost pressure"] if broken == "errors" else 0
    assert not all(reference_targets(values).values())


def test_failed_later_phase_still_publishes_unfavorable_nominal_metrics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import json
    import subprocess

    from scripts.run_load_profile import LoadDrill

    monkeypatch.chdir(tmp_path)
    (tmp_path / "reviews" / "evidence").mkdir(parents=True)
    (tmp_path / "load-result.json").write_text(
        json.dumps({"states": {"suspended": 1}, "nominal_metric": 42})
    )
    drill = object.__new__(LoadDrill)
    drill.private = tmp_path
    drill.prefix = ["unused-test-controller"]

    def compose(*args: str, **kwargs: object) -> bytes:
        if args[0] == "run":
            raise RuntimeError("controlled overload phase failure")
        if args[0] == "exec":
            return b"1060"
        return b""

    monkeypatch.setattr(drill, "compose", compose)
    monkeypatch.setattr(
        subprocess, "run", lambda *args, **kwargs: subprocess.CompletedProcess([], 0)
    )
    with pytest.raises(RuntimeError, match="controlled overload"):
        drill.main()
    result = json.loads((tmp_path / "reviews/evidence/m7-reference-load.json").read_text())
    assert result["nominal_metric"] == 42 and result["states"] == {"suspended": 1}
    assert result["status"] == "FAIL"
