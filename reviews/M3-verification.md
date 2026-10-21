# M3 verification

Scope: only M3, based on M1/M2 commit `9cc6627`, branch `feature/m3`.
Verification date: 2026-10-22. PostgreSQL/Redis and HTTP simulations use disposable resources;
no live provider requests or credentials. M4 was not started and no branch was merged.

## Environment and commands

- Python 3.14.8; uv 0.13.1 in the image; locked `uv.lock` dependencies.
- PostgreSQL 17.11 and Redis 8.2.2, pinned image digests; SQLAlchemy async/psycopg.
- Docker context `colima-ai-task-platform`, Compose project `atp-m1-test`.
- Ruff 0.17.0, mypy 2.4.0, pytest 9.1.1. Local static commands use the existing `.venv`.

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
.venv/bin/pytest tests/unit -q
actionlint .github/workflows/ci.yml
git diff --check

export DOCKER_CONTEXT=colima-ai-task-platform
export COMPOSE=docker-compose
docker-compose -p atp-m1-test build api test
docker-compose -p atp-m1-test up --wait api
docker-compose -p atp-m1-test run --rm --no-deps test pytest -p no:cacheprovider tests -q
make test-execution test-failures test-concurrency
.venv/bin/python scripts/smoke_compose.py
```

The complete run includes M1/M2 identity, API, role/constraint, command/idempotency, concurrency,
state matrix and migration regressions alongside M3. Migration checks downgrade to base and
upgrade cleanly, preserve M1 identity/audit/epoch on upgrade, repeat head upgrade and test M3→M2
privileges before returning to head. No SQLite or mocked database proves these properties.

## Results

The final unfiltered suite produced **PASS: 403 passed, 0 failed, 0 errors, 0 skipped in
106.36s**. This includes 25 unit tests and 378 integration tests, using real PostgreSQL and
Redis. The final focused gates and Compose results are recorded below.

| Final check | Status / observed result |
|---|---|
| `make test-execution` | **PASS: 75 passed**, 0 failed/errors/skipped, 19.21s |
| `make test-failures` | **PASS: 22 passed**, 0 failed/errors/skipped, 29.36s |
| `make test-concurrency` | **PASS: 20 passed**, 358 deselected, 0 failed/errors/skipped, 4.27s |
| Compose smoke | **PASS**, exit 0: HTTP response loss, PostgreSQL/API restart, actual Redis outage/recovery, replay/cancel, revocation, redaction, isolation, limits and worker PostgreSQL claim/heartbeat during outage |

Focused selections do not omit tests from the unfiltered 403-case run. No required local M3
acceptance check remains failed or skipped. GitHub CI is verified separately against the
published PR head; its current status is available on the PR rather than inferred from local tests.

The first full image run produced **402 passed, 1 failed, 0 errors, 0 skipped in 109.43s**.
Its sole failure was the M1/M2 exact table-set assertion: the four approved M3 tables were
missing from its expected set. That expectation was updated without replacing equality by
subset matching, dropping a table assertion, or changing the four-role/no-escalation checks.
The complete suite was rerun after this test-contract update.

Ruff lint/format (78 files), strict mypy (61 checked source files), actionlint and whitespace
checks pass. Independent review is **PASS**, with **131 cases passed**, no failures/errors/skips
and no unresolved significant finding (see the separate reviewer report).
The separate local unit run produced **25 passed, 0 failed, 0 errors, 0 skipped in 0.35s**.

## Implementation and review evidence

`0003_execution` adds durable external calls, integer money/token provisions, immutable evidence
and shared provider admission. Step eligibility/selection/results and attempt lease metadata
support A/B/C, retry/recovery and terminal cleanup. A guarded first-response timestamp makes
Retry-After observation idempotent even when first evidence arrives after reaping.

The supervised simulation worker separates execution and heartbeat pools, bounds local HTTP,
continues PostgreSQL polling without Redis and never automatically regenerates unknown calls.
Owner resolution checks current authentication, ownership, task version, explicit overlap risk,
deadline, epoch and remaining limits. B charges retained payload storage and shared limits;
late C may settle exposure but cannot restore an old continuation or mutate a terminal result.

See [test matrix and recorded Red → Green → Refactor](M3-test-matrix.md) and
[independent reliability/security review](M3-review.md). Review findings were returned to the
author and reproduced before correction. The baseline-only M1/M2 historical TDD exception
remains documented separately; it does not apply to M3. Transient early RED logs were lost
across desktop resumptions; their observed commands/counts remain in the task and test matrix.

## Explicit boundaries

- SKIPPED / not applicable to M3: paid OpenAI smoke (not authorized), real provider billing
  and Responses protocol (M5), corpus/model tool/report workflow (M4), SSE (M6), full
  retention/restore/load delivery campaign (M7). These are not reported as passed.
- F04–F10 and simulated accounting portions of F14–F16 are exercised. F01–F03 regressions
  remain enabled. Later gates retain their milestone scope as mapped in the test matrix.
- No production-readiness, exactly-once physical execution, prompt-injection immunity or
  remote cancellation guarantee is claimed. The external journal records delayed first sends
  after committed B independently of stale local progression.
- Public dispatch remains closed; executable synthetic completion is explicitly marked
  `simulated`. The analysis handler remains unavailable until its authorized milestone.
- The pre-existing user change to AGENTS.md is preserved outside the M3 commits.
