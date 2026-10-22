# Independent M4 review

**Verdict: APPROVE for the reviewed M4 implementation.** No unresolved confirmed correctness,
security or reliability finding remains from this review. This is a scoped independent review,
not a claim that every milestone completion check has passed: the author must finish the
unfiltered M1–M4 rerun after the final corrections and record it in
[M4-verification.md](M4-verification.md) before publication. No merge or M5 work is authorized.

## Independence, revision and scope

Review performed by a separate reviewer agent on `feature/m4`, based on M3 commit
`046ebc4190c3b369551b8f081288ae57c654ebc7` and the uncommitted M4 working tree. The reviewer
did not edit application code or tests. Findings were returned to the implementation author;
the reviewer inspected the corrections and independently reran relevant tests. The existing
AGENTS.md modification is outside the implementation review.

Read AGENTS.md, the approved architecture plan and M4/F11–F13 gates, review-response tradeoffs,
and the `adversarial-review`, `execution-reliability-review`, `agent-security-review` and
`verification` skills. Inspected the corpus/protocol/workflow/analysis-worker/report-transport
and demo modules; M3 executor, accounting, resolution and worker integration; task input,
health and CLI changes; migration `0004_workflow`, grants, Compose, CI and tests.

The final targeted run includes the legacy-worker compatibility and model-C deadline
corrections. Final static/unit checks include the revised semantic fixtures. Subsequent
implementation changes require renewed review of their impact; the report itself does not
certify a future commit simply because the branch name is unchanged.

## Findings resolved during review

All findings below were reported before correction. The original coverage did not detect the
specific scenario; the named regressions now pass independently.

| Severity | Location and concrete failure | Correction and regression |
|---|---|---|
| High | `workflow.py`, local-tool progression: a read delayed beyond the task deadline could still commit a successful tool and a new ready step. Ownership was checked only before the read. | Fresh ownership/deadline checks before both successful and failed progression; `test_local_tool_cannot_progress_after_deadline_during_read`. |
| Medium | `workflow.py`, document search: matching owner documents were fully materialized before filtering pinned versions and limiting results. A large unpinned corpus could consume unnecessary memory while holding control locks. | Pinned `(id, version)` SQL predicate and `LIMIT 5` before materialization; `test_search_never_materializes_unpinned_document_bodies`. |
| Medium | `health.py`, worker preflight: missing corpus access or new checkpoint write privileges could still report compatible readiness, followed by runtime failures. | Required document reads and M4 column grants checked explicitly; three cases of `test_m4_worker_preflight_requires_corpus_and_checkpoint_privileges`. |
| Medium | `workflow.py`, report storage reservation: the exact payload was copied into the transmission, frozen step and terminal report, but only one copy was reserved before the external effect. | Conservative reservation of three payload copies plus metadata before first B; `test_save_reserves_storage_for_exact_payload_and_terminal_copy`. |
| Medium | `workflow.py`, report probe: a pause after the probe-reservation commit used a stale remaining deadline and could emit a late GET. | Monotonic anchor before the database clock sample, elapsed time deducted after commit; `test_probe_does_not_send_after_commit_delay_crosses_deadline`. |
| Medium | `execution.py`, retained M3 handler: `--simulate` could claim corpus-bearing or advanced M4 steps and finish them as a single simulated call. This required the explicitly enabled test profile but violated compatible-handler boundaries. | Legacy handler checks pinned versions, excludes corpus tasks and accepts only the initial model step; three cases of `test_legacy_simulation_handler_cannot_consume_m4_work`. |
| High | `execution.py` / `workflow.py`, model C: deadline expiry during asynchronous source validation could commit a new continuation after the earlier ownership check. | Business progression is isolated in a savepoint and checked against fresh database time before acceptance. Expiry rolls back the checkpoint/continuation/events while preserving settled usage and call evidence; `test_model_C_cannot_select_continuation_after_deadline_during_source_read`. |
| Medium, evidence gap | `test_semantic_fixtures.py`: the initial 40 arithmetic report strings lacked source dossiers, expected citations and documentary injection. They did not support the stated semantic-campaign coverage. | Forty scripted source/owner/version/span fixtures now distinguish authentic citations, foreign/forged references, false conclusions and instruction contamination. These are deterministic evaluator fixtures, not live-model accuracy evidence. |

Also checked the author's earlier corrections for no save-tool count/first authorization on
capacity waiting, document rights at owner resolution, unavailable handlers, output storage,
and rejection of LLM regeneration for unknown report effects. Their regressions are included
in the independent suites.

## Mechanisms and failure boundaries examined

- **F11:** PostgreSQL checkpoints reconstruct model/tool history and opaque protocol items;
  document versions are immutable and owner-scoped, rights are reread under the serialized
  admission/control prefix, and unavailable handlers suspend. Local tools create no invented
  external call. Tests restart workers and CLI processes at every unit, terminate a process
  with `os._exit(91)` after committed units, and race the same local-tool lease. The revocation
  race observes actual `pg_blocking_pids` contention before B.
- **F12:** report operation identity and exact bytes are frozen, while transmissions have
  distinct IDs. The separate demo PostgreSQL database atomically deduplicates operation/hash/
  effect/response. The physical lost-response test observes **two POST invocations and one
  effect** in its independent journal. Eight concurrent exact submissions return the same
  retained proof; changed bytes conflict. Recovery validates owner/operation/hash, selected
  call and generation, uses bounded probes, and does not equate missing evidence with no effect.
- **F13:** server-side schemas reject unknown tools, extra arguments and multiple actions;
  only the orchestrator can save reports. Fixed transport destinations, disabled redirects,
  retries and environment proxies prevent model-controlled networking. Nested foreign
  references, forged citation spans/hashes, forbidden tools and invalid evidence are rejected.
  Opaque reasoning remains internal protocol data rather than public report content.
- Source revocation before B prevents a new authorization. Proof of an already-authorized
  report effect may still complete that operation after revocation; this is not a new send.
  A delayed first send after committed B remains the documented external-effect limitation.
- Runtime roles have restricted grants; immutable corpus and report-operation data have SQL
  guards. The demo uses separate credentials/database and no production fault endpoint.
  Compose keeps private networking, non-root processes, dropped capabilities and resource
  bounds. No paid credentials or live provider calls were used.

## Independent verification

Environment: project Docker context `colima-ai-task-platform`, disposable Compose project
`atp-m1-test`, real application PostgreSQL/Redis and separate demo PostgreSQL. Source and
tests were mounted read-only so these results do not depend on a stale installed image.
Pinned image/runtime versions and the final installed-image checks are recorded separately
in the author verification report.

```sh
DOCKER_CONTEXT=colima-ai-task-platform docker-compose -p atp-m1-test \
  -f compose.yaml -f compose.demo.yaml run --rm --no-deps \
  -e PYTHONPATH=/app/src -v "$PWD/src:/app/src:ro" -v "$PWD/tests:/app/tests:ro" \
  test pytest -p no:cacheprovider \
  tests/integration/test_workflows.py \
  tests/integration/test_workflow_boundaries.py \
  tests/integration/test_workflow_corpus.py \
  tests/integration/test_workflow_reports.py \
  tests/integration/test_workflow_e2e.py \
  tests/integration/test_tools_demo.py \
  tests/unit/test_agent_protocol.py tests/unit/test_semantic_fixtures.py -q --tb=short
```

Result: **97 passed, 0 failed, 0 errors, 0 skipped in 49.78 seconds**. This was after the
initial five review fixes, before the final legacy/model-C regressions.

The final correction run used the identical Compose prefix with:

```sh
test pytest -p no:cacheprovider tests/integration/test_workflow_boundaries.py \
  tests/unit/test_semantic_fixtures.py -q --tb=short
```

Result: **59 passed, 0 failed, 0 errors, 0 skipped in 3.74 seconds**. This includes all
19 boundary cases and all 40 improved semantic fixtures. An earlier attempt after the
desktop restart could not connect to the absent Docker socket: no tests ran on that attempt.
It is an infrastructure interruption, superseded by this successful real-database rerun.

Final independent local checks:

| Command | Result |
|---|---|
| `.venv/bin/pytest tests/unit -q --tb=short` | PASS: 87 passed, 0 failed/errors/skipped, 0.49 seconds |
| `.venv/bin/ruff check .` | PASS |
| `.venv/bin/ruff format --check .` | PASS: 98 files |
| `.venv/bin/mypy` | PASS: 79 source files |
| `actionlint .github/workflows/ci.yml` | PASS |
| `git diff --check` | PASS |

The overlapping suite totals must not be added together as a unique-test total. The reviewer
did not independently rerun the entire M1–M4 suite or clean migration cycle: those final
mandatory checks belong to the author's coordinated verification run, not an implied PASS
from these focused results.

During that full run, the M3 lost-C-acknowledgement injector was found to fire on the newly
introduced savepoint release, before the outer transaction committed. The reviewer inspected
the installed SQLAlchemy transaction implementation and independently observed `after_commit`
callbacks with `in_nested_transaction()` values `[True, False]` for nested then outer commit
using a connection-free Session probe. Skipping only the nested callback in the test injector
restores its intended outer-COMMIT failure boundary; it does not change production behavior
or weaken the existing physical-effect, terminal-state, settlement or event assertions.
The author's targeted crash tests and full rerun remain required after this fixture correction.

## TDD evidence and limits

Inspected the test matrix and retained behavioral RED artifacts. The initial review cases
show assertion failures caused by the reported behavior, not imports or missing services.
The two latest RED artifacts for legacy handlers/model C are explicitly **transcribed
observed summaries**, because their original temporary logs were lost during desktop restart.
They report 3 and 1 behavioral failures respectively, followed by 4 passing focused tests.
This reviewer independently verified the final GREEN results, but did not witness every
original implementation RED run; historical test-before-code ordering rests on the author's
record, not on inference from a passing final suite.

Semantic fixture outcomes: 40 structurally valid reports with exact authentic citations;
20 expected-fact matches and 20 intentionally contaminated/contradictory conclusions. Foreign
and forged citations are independently rejected. The deliberate semantic failures are
published evaluator outcomes, not skipped pytest failures. This does not establish model
truthfulness or prompt-injection immunity.

SKIPPED / outside this review: paid OpenAI and real-provider protocol/tokenizer/billing
(M5, not authorized), SSE/degraded-load campaign (M6), full retention/disaster restoration
and production-load delivery (M7). No general throughput, production readiness, remote
cancellation or zero-loss guarantee follows from this M4 review.
