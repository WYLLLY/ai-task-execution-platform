# M2 test matrix and TDD evidence

Scope: M2 commands/state foundation, plus requested M1 audit follow-ups. No executor, provider calls, SSE, accounting settlement or M3 implementation. The M1 base is `24669e1`; branch `feature/m2`. User's local AGENTS.md TDD update is preserved separately.

| Layer/risk | Planned evidence |
|---|---|
| Unit/boundaries | Strict bounded JSON, duplicate keys, decimal canonicalization, omitted/null distinction, invalid schemas and lower-only limits; secret generation races |
| API/security | Current authentication, owner-scoped task/step/cursor/cancel, epoch required for new creation, no input/secret echo, HTTP limiting |
| PostgreSQL/integration | Atomic task/step/event/idempotency/counter creation, pins, structural constraints, restricted SQL roles, M1→M2 and empty-DB migrations |
| Concurrency | Actual pg_blocking_pids observations, duplicate keys, different keys on last owner/global slot, collision, cancel serialization and revocation ordering |
| Resilience | Stored response replay after response loss, withdrawal/full quota/closed admission; real Redis outage/restart, transaction rollback and process-death persistence |
| States/F03 | SQL legal arcs, terminal immutability, partial unique indexes, cancellation/deadline cleanup across all locally representable nonterminal task/step/attempt states |
| E2E | Real Compose HTTP/CLI, PostgreSQL restart, Redis outage/recovery, create/replay/cancel; no paid calls |
| Regression | Entire M1 + M2 unit/integration/static/Compose suite after corrections; independent review and CI |
| Deferred | F04–F22 executor/provider/workflow/SSE/recovery gates; no workers execute work in M2. External-call/cost tables and uncertain remote exposure tests begin in M3. |

## Recorded Red → Green

1. M1 regression, before changing `scripts/dev_secrets.py`: `.venv/bin/python -m pytest tests/unit/test_secret_creation_race.py -q` → **2 failed**, both `DID NOT RAISE SystemExit`: concurrently created `.env` and dangling symlink were overwritten/followed. Changed only creation to exclusive mode and refused symlinks; focused **2 passed**, complete unit **12 passed**. Ruff formatting preserved behavior. No existing assertion removed.
2. M2 command tests were written against M1 before any M2 application/migration implementation. `docker-compose -p atp-m1-test run --rm --no-deps test pytest -p no:cacheprovider tests/integration/test_commands.py tests/integration/test_task_states.py -q` → **38 failed in 1.74 s**, no errors/skips, assertions on missing routes (404), not setup/import errors. Additional CLI test against the unchanged M1 image → **1 failed** because the requested admission command was absent (exit 2).

Historical M1 test-first chronology is not available; it is **unverified**, not reconstructed from green tests. The current regression and M2 changes use recorded test-first evidence.

3. Independent M1 additional suite before readiness correction: **43 passed, 2 failed**, no errors/skips. Revoking SELECT on each identity table made `/v1/me` fail but readiness incorrectly return 200. Readiness now checks required schema columns/SELECT rights through zero-row queries.

Existing M1 assertions adapted to the approved M2 surface: exact table-set assertion is extended (not changed to a subset); revision assertion now expects 0002_commands; POST /v1/tasks now requires401 without authentication instead of absence404. No checks were removed.

4. JSON NUL validation: `python -m pytest tests/unit/test_command_boundaries.py -q` → **2 failed, 8 passed** before rejecting NUL in keys/strings; **10 passed** after the correction. Exact Decimal canonicalization is covered beyond the default decimal context precision.
5. Review regressions against the pre-correction image: `pytest tests/integration/test_command_regressions.py -q` → **4 failed** (deadline crossed during child lock wait; fresh-key terminal cancellation; revoked INSERT/column UPDATE privileges falsely ready). Corrections move the clock sample after all locks, reject new terminal commands with409 and check required write grants. All four pass in the full final suite.

## Expanded audit and harness corrections

Permanent M1 adversarial coverage replaces the earlier prose-only harness. M2 additions cover all distinct task/step/attempt state pairs, terminal immutability, real foreign-owner/task references, duplicate active attempts, global last-slot races, different-body same-key races, competing cancellation, revoke-before-command ordering, storage limits, transaction rollback, bounded SQLSTATE40001/40P01 retries and nonretryable failure. PostgreSQL sequences count attempts independently of transaction rollback. Lost commit-ack simulation wraps a real committed command and fresh PostgreSQL lookup; its assertion is exactly one mutation invocation.

The first complete expanded run was **117 failed, 158 passed**:116 failures were a SQL fixture parameter inferred as both text/varchar, and one foreign-owner probe collided with its fixture's existing command key before reaching the FK. Explicit fixture casts and a distinct command key corrected the harness; assertions and application constraints were not relaxed. The next complete integration run was **278 passed, 0 failed/errors/skipped**. Lock-observation connections use autocommit to avoid PostgreSQL statistics snapshots hiding a newly blocked connection. One intermediate focused run used the old installed wheel despite a source bind mount; it is not evidence for the corrected implementation.

The Makefile unit command exposed a test import path error (`scripts` is not an installed package); loading the actual script with `runpy` fixed the test harness. `make test-unit` then returned **25 passed**, no collection errors/skips. These setup failures are not counted as meaningful TDD Red.

## TDD traceability limitation

Recorded Red runs establish the initial M2 command/state contracts and the specific corrections above. The expanded arc, transport-ambiguity, limiter and transaction-retry audit tests were added after those mechanisms existed. Their passing results establish current behavior, **not test-first chronology for every individual behavior**. Historical M1 chronology is also unavailable. Under the current strict verification Skill, the universal TDD process gate is **FAIL / unverified** for those portions; no retrospective Red is invented. Runtime verification and this process limitation must be reported separately. No test is deleted, skipped or weakened to conceal the limitation.

6. Final independent source review identified missing platform admission-column checks. On2026-10-18, two new real-PostgreSQL tests temporarily renamed `max_open_tasks` / `max_storage_bytes` and asserted readiness503: **2 failed,4 deselected** (actual200), before application correction. Adding `PlatformControl` to the existing zero-row required-model checks produced **6 passed** for the entire regression file. The full independent rerun follows this correction.
