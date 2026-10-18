# Independent M1 + M2 review

Date: 2026-10-18. Reviewer: separate review agent; implementation and corrections remained with the primary author. Scope: the approved M1 foundation and M2 commands/state milestone, including their combined regression suite. No application, migration, test, configuration or Git changes were made by this reviewer. This report is the reviewer's only repository edit.

Reviewed source: `51c7e3c4a31b6d7470941c6cef2dc9560b495e59`, following M2 commit `644437389b3ea28de4f24645ffd49d2f66a83076`; M2-only base `24669e1d9ab191e04e2a350a50cc3e41e4aa0715`. [Draft PR #2](https://github.com/WYLLLY/ai-task-execution-platform/pull/2) targets `main` and therefore also contains unmerged M1 work. The user's local `AGENTS.md` update was read and preserved outside these commits.

## Verdict

**Technical/runtime verdict: PASS for implemented M1 + M2 scope, with no remaining confirmed application defect. Overall milestone-completion verdict: REJECT under the strict process gate.** The universal per-behavior TDD process gate remains **FAIL / unverified**. The recorded initial Red runs and test-first corrections do not establish test-first chronology for every later audit-covered behavior or historical M1 behavior.

This process limitation is separate from application correctness. It must remain visible even when every current test passes. The PR remains a draft; neither this review nor successful CI authorizes a merge or M3.

Finding counts: **0 Critical, 0 High, 3 Medium and 2 Low application findings resolved**; **0 remaining confirmed application findings** after source review and the final complete integration run. One **Medium process finding** remains: universal TDD chronology is unverified. This is not evidence of an undiscovered runtime defect, but it prevents unconditional acceptance under the applicable completion rule.

## Review method and boundaries

Read the current repository instructions, approved architecture plan and review response, plus `adversarial-review`, `verification`, `execution-reliability-review` and `agent-security-review`. Compared the task/step/attempt state graph with plan §§2.2–2.4 and traced authentication, replay, admission, cancellation, storage counters, events and SQL privileges through the services and migration.

The adversary is an API caller controlling requests and identifiers. Application code, administrative SQL roles and the host remain trusted, as specified by the plan. No RLS, arbitrary-plugin sandbox, provider integration or later-milestone feature was introduced as an implicit review requirement.

Independent verification uses the repository's actual commands, a newly created disposable PostgreSQL volume, rebuilt installed application/test images, real Redis and real role-specific DB connections. The reviewer had exclusive ownership of `atp-m1-test` during stateful verification. The earlier independent clean-database run on `6444373` produced **278 passed in 50.52 seconds**, with no failures, errors or skips. The final run below includes the additional two readiness regressions.

## Findings and corrections

### R1 — Medium: stale time after waiting for child locks — resolved

**Location:** `src/task_platform/tasks.py`, `cancel_task`.

**Scenario and impact:** cancellation acquired the task lock, sampled database time, then waited for a step/attempt lock across the task deadline. It could commit `cancelled` using the earlier timestamp when the required outcome was `timed_out`. This violated the fresh-time/lock ordering contract in plan §§2.4 and 3.3.

**Correction:** sample `clock_timestamp()` only after all required task, step and attempt locks are held. The regression holds a real step lock, observes the blocked API connection, observes database time crossing the deadline, then releases the lock. `test_cancel_samples_time_after_waiting_for_children` requires `timed_out`. Both cancellation and deadline cleanup are also exercised over the six canonical nonterminal task/step/attempt combinations.

### R2 — Medium: readiness accepted unusable required schema/permissions — resolved

**Location:** `src/task_platform/health.py`, `check_database`.

**Scenario and impact:** revoking required identity SELECT or M2 INSERT/column UPDATE grants could leave readiness successful while authenticated routes or admissions failed. A later review found that renaming either `platform_control.max_open_tasks` or `max_storage_bytes` likewise left readiness successful because the zero-row schema check omitted `PlatformControl`. This could present submission availability despite an unusable command path.

**Correction:** query required model columns without fetching data, including `PlatformControl`, and inspect the exact API INSERT/UPDATE privileges used by command services. Permanent regressions cover both identity SELECT grants, task INSERT, principal counter UPDATE, and both platform admission columns. The final two tests rename and restore columns on the isolated database, preserving their data and constraints. The author recorded **2 meaningful failures** before the final one-line correction; the final full run independently verifies the corrected behavior.

### R3 — Low: successful fresh terminal command was not retained — resolved

**Location:** `src/task_platform/tasks.py`, `cancel_task`.

**Scenario and impact:** a new key cancelling an already terminal task with its current version returned an unretained `200`, unlike accepted command responses. Retaining arbitrarily many such commands instead would require additional bounded storage handling.

**Correction:** a new terminal cancellation returns `409`, consistent with the nonterminal precondition in plan §2.4. The original successful command still replays its exact retained response before mutable version/state checks. The regression checks the original replay and verifies no additional command, event or admission-counter mutation.

### R4 — Low: PostgreSQL-incompatible NUL reached storage — resolved

**Location:** `src/task_platform/command_schemas.py`, command parsing.

**Scenario and impact:** JSON NUL characters in keys or strings passed the parser even though the PostgreSQL storage boundary cannot represent them. A malformed client payload could therefore become a database/service error rather than a bounded validation error.

**Correction:** reject NUL during canonicalization with `422`; preserve strict duplicate-key, Unicode, depth, size and numeric checks. The author's recorded Red was **2 failed, 8 passed** before correction. The independent unit suite verifies rejection and exact Decimal canonicalization without binary floating-point aliasing.

### R5 — Medium: local secret setup could overwrite an intervening environment file — resolved

**Location:** `scripts/dev_secrets.py`.

**Scenario and impact:** another local setup could create `.env` after the existence check, or a dangling symlink could escape the existing-file check. The former write behavior could overwrite/follow that path, violating the explicit preservation guarantee.

**Correction:** refuse symlink destinations and create `.env` exclusively; report partial setup without overwriting the intervening file. The author recorded **2 meaningful failing regression tests** before changing the script. Both are independently rerun in the complete unit suite. This is a local setup correctness issue, not an exposed API attack path.

## Mechanisms inspected and coverage

| Contract | Protection and evidence |
|---|---|
| F01: durable acceptance and replay | Task, first step, event, counters and retained response commit together. Current credentials precede replay; replay precedes new admission checks. Tests cover full quota, unavailable workflow, closed admission, stale epoch and changed-body conflict. Physical HTTP response discard and restart replay are covered by Compose smoke. |
| F02: concurrent admission | Global then owner locking serializes distinct keys; database uniqueness protects owner/route/key identity. Tests observe actual PostgreSQL blocking for duplicate keys, differing bodies, last owner slot, last global slot and competing cancellation. |
| F03: coupled state and cancellation | All 104 distinct task/step/attempt state pairs are tested against the approved arc tables, plus terminal immutability, duplicate active attempts, foreign references and coupling constraints. Cancellation closes executable steps/active attempts, invalidates generations and releases slots once. Both deadline and cancellation variants run for the six canonical nonterminal combinations. |
| Authentication and owner isolation | Real current-credential authentication; nested task/step/cursor/cancel owner checks; composite owner/task foreign keys. Revocation committed while commands wait prevents mutation. M1 tests also cover disable/issuance ordering, expiry, concurrent revocation and secret-safe error responses. |
| Transaction failure and ambiguity | Bounded whole-transaction retries only for `40001`/`40P01`; injected SQL failures use a sequence that survives rollback to count attempts. The lost-ack test simulates the exception around a real committed command and real fresh lookup, asserting exactly one mutation invocation. It is not a physical PostgreSQL commit-packet-loss experiment. |
| Resource limits and Redis degradation | Owner/global slot and storage limits are transactional. Real Redis TTL/threshold tests prove creation limiting and replay bypass. Local limiter tests cover boundaries, window reset and bounded cardinality. Compose stops Redis and verifies new-create rejection with preserved reads, replay and cancellation. |
| M1 infrastructure and isolation | Real role-denial tests, audit rollback, committed/uncommitted process-death cases, independent control pool under read-pool exhaustion, migration-role checks and empty-DB/M1 upgrade paths. Compose inspects non-root users, dropped capabilities, resource limits, private DB/Redis, loopback API, secret mounts and log redaction. |

The lock order is consistent in the implemented paths. Administrative key changes take the principal lock, and workflow availability changes take the platform lock, so admission revalidation is protected against those concurrent service mutations. The Redis network operation occurs outside the database transaction. SQL role grants restrict command changes to the required columns; the API cannot administratively reopen admission or rewrite retained responses.

No existing assertion was weakened to obtain a pass. The M1 exact schema/revision assertions were extended for migration 0002, and the formerly absent task route now requires authentication. Expanded matrix harness failures were corrected through explicit SQL fixture casts and an unused fixture command key, not by loosening application constraints or assertions. Full details and historical counts are in [the test matrix](M2-test-matrix.md).

## Independently executed verification

Environment: Python **3.14.8**, uv **0.13.1**, PostgreSQL **17.11**, Redis **8.2.2**; locked dependency versions and pinned image/action revisions remain in the repository. PostgreSQL and Redis server versions were read from the running containers during the earlier independent run. One API process, the configured 38/50 connection budget, isolated `colima-ai-task-platform` Docker context and disposable `atp-m1-test` project were used. These runs are correctness tests, not benchmarks.

Commands (each `make` invocation below was executed with the shown environment):

```sh
DOCKER_CONTEXT=colima-ai-task-platform docker-compose -p atp-m1-test down --volumes --remove-orphans
DOCKER_CONTEXT=colima-ai-task-platform COMPOSE=docker-compose UV_CACHE_DIR=/private/tmp/ai-task-uv-cache make UV=/private/tmp/ai-task-uv/bin/uv test-integration
DOCKER_CONTEXT=colima-ai-task-platform COMPOSE=docker-compose UV_CACHE_DIR=/private/tmp/ai-task-uv-cache make UV=/private/tmp/ai-task-uv/bin/uv test-commands test-concurrency smoke-compose
UV_CACHE_DIR=/private/tmp/ai-task-uv-cache make UV=/private/tmp/ai-task-uv/bin/uv lint typecheck test-unit
actionlint
git diff --check
```

| Final check | Independent result |
|---|---|
| Ruff lint and formatting | PASS; 51 files already formatted |
| Strict mypy | PASS; 39 source files |
| Complete unit suite | PASS; 25 passed, 0 failed, 0 errors, 0 skipped; 0.26 seconds |
| Complete integration, clean database and rebuilt images | PASS; 280 passed, 0 failed, 0 errors, 0 skipped; 51.82 seconds |
| Focused command gate | PASS; 183 passed, 0 failed, 0 errors, 0 skipped; 27.26 seconds |
| Focused concurrency gate | PASS; 10 passed, 270 intentionally deselected, 0 failed, 0 errors, 0 skipped; 1.83 seconds |
| Real Compose smoke | PASS; physical HTTP response discarded, exact replay across PostgreSQL/API restart, real Redis outage/recovery, preserved reads/replay/cancellation, revocation, redaction, runtime isolation and worker preflight |
| Actionlint and whitespace checks | PASS |

GitHub status was independently read: both checks on `6444373` completed successfully ([PR run](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/39072558929), [push run](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/39072538531)). More importantly, **both checks on the corrected source revision `51c7e3c` also completed successfully**, confirmed directly from the PR: [PR run 39127995624](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/39127995624) completed at 12:39:28 UTC and [push run 39127991904](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/39127991904) at 12:39:46 UTC on 2026-10-18. PR #2 remained a draft. Documentation commits after this report require their own CI status; they are not silently covered by these revision-specific observations.

## TDD process gate and remaining limits

The [test matrix](M2-test-matrix.md) records initial M2 command/state Red (**38 failures**) and missing CLI behavior (**1 failure**), followed by specific test-first regressions. Setup/import failures, cached-observer failures and an intermediate stale-installed-image run are not valid Red evidence. These historical results are author-recorded evidence; this reviewer independently reran the final Green checks rather than inventing or reconstructing historical test order.

Expanded state-arc, transport, limiter and retry tests were added after their mechanisms existed, and historical M1 test-first chronology is unavailable. The strict verification Skill therefore leaves the universal TDD gate **FAIL / unverified**. Successful runtime verification cannot remove that limitation; unconditional M2 completion is not asserted.

Recommendation for this process finding: preserve the evidence limitation and draft status, and record demonstrable Red → Green → Refactor for subsequent behaviors and corrections. Do not fabricate retrospective Red runs, backdate test ordering, or treat publication, CI success or runtime acceptance as an automatic waiver.

M2 accepts and cancels queued tasks but does not execute them. Automatic deadline maintenance, dispatch A/B/C, lease recovery, external calls, unknown remote exposure and money/token settlement remain M3 work. Active-attempt generation equality and terminal-row metadata changes needed for later accounting/events must be handled by those later migrations/services; their future behavior is not certified here. Corpus/tool authority and semantic report correctness, OpenAI contracts, SSE, retention, restoration, load and production deployment gates remain unexecuted later scope. No paid provider request, live credential, external effect simulator or application deployment was used in this audit. Local cancellation is not evidence of remote cancellation.

No unconditional security, durability, performance or production-readiness guarantee follows from these checks. No merge, auto-merge or M3 work is authorized by this report.
