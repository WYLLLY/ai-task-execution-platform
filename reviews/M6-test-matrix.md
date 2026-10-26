# M6 implementation matrix and TDD evidence

Base: verified M5 commit `43f63971a83832150a0e2123b6df4706550ac443`.
M1–M5 were merged into main with explicit owner authorization on 2026-10-26.
M5 merge commit: `590a72d56a67b5a5007332480dd37b494f4146c7`.
The pre-existing AGENTS.md edit is preserved outside this work.

The initial cursor slice and subsequent implementation records below are chronological.
Final release verification is recorded separately in `M6-verification.md`; historical
pending statements refer to those earlier slices, not the release verdict.

| Layer / gate | Maintained evidence |
|---|---|
| Unit / F17 | 34 cursor cases; auth validity/check-start timing, caps, transport cursor acknowledgement, absolute lifetime, backlog resets; cache publication races/deletion |
| Integration / API / F17 | Real PostgreSQL single-statement snapshot paused against concurrent settlement; >100-event contiguous bounded replay, byte caps, owner-before-cursor validation, provenance and tombstones |
| Security / F18 | Owned410/foreign404; real key revocation and authorization DB lock close within10s; source/protocol/private metadata excluded; API denied evidence/provenance/tombstone writes |
| E2E / F18 | Real private Nginx/TCP flushing, Last-Event-ID replay, retention during stream, auth failure; direct actual ASGI send backpressure and lifetime tests |
| Concurrency / F17–F19 | Single poller per followed task, cache query interleavings, real read-pool contention leaves control responsive, atomic connection caps100/5 |
| Resilience / F19 | Real Redis stop/restart with100SSE,120reads at10/s, creation503/replay202/control200,10s heartbeat, B/HTTP simulator/C plus independent request/effect journal |
| Observability / operations | Fixed labels/log fields and actionable alert IDs; real renewal failures; runtime API pool scrape; bounded collector; durable immutable trace context and replacement links |
| Migration / privileges | Clean base/head roundtrip, M1 upgrade, populatedM5 estimate backfill and nullable legacy traces, API forbidden writes, malformed/nonzero trace constraints |
| Regression / independent review | Complete M1–M6 suite and gates/static/CI counts in release report; original findings and corrections retained in independent reviews |

The cursor helper has no I/O. Its unit tests cannot prove PostgreSQL snapshot,
locking, retention, authorization, Redis delivery or streaming guarantees.
Those categories remain pending, not marked not applicable to the milestone.
The eventual HTTP service must authenticate and authorize the target task before
invoking cursor validation, as required by the approved plan.

The application's UUID/sequence identifier is a restriction of the general
[SSE Last-Event-ID contract](https://html.spec.whatwg.org/multipage/server-sent-events.html#the-last-event-id-header).
It is a resume position, not delivery acknowledgement or authorization.

## Initial TDD record

Tests precede behavior implementation. A minimal importable interface scaffold
allows meaningful behavior failures rather than missing-module/import failures.
RED/GREEN outputs are retained under `reviews/evidence/m6-cursor-*.txt`.
- RED: **32 failed, 2 passed, 0 errors, 0 skipped in 0.17s**. The importable
  scaffold returned an empty ID/default position; assertions and missing validation
  failed behaviorally. No import or infrastructure failure is counted as RED.
- GREEN/refactor: **34 passed, 0 failed/errors/skipped in 0.08s**.
- Complete unit regression: **168 passed, 0 failed/errors/skipped in 2.17s**.
- Ruff: PASS; format: 125 files unchanged; mypy: PASS, 100 checked files.

Commands: `.venv/bin/pytest tests/unit/test_event_cursor.py -q --tb=short`,
`TIKTOKEN_CACHE_DIR=/private/tmp/atp-m5-tokenizer .venv/bin/pytest tests/unit -q`,
`.venv/bin/ruff check .`, `.venv/bin/ruff format --check .`, `.venv/bin/mypy`.
Formatting refactoring preserved the green behavior tests. No existing test was
removed or weakened.

No M6 gate is claimed PASS yet. The complete PostgreSQL/Redis regression,
streaming/proxy/load scenarios, migrations/privileges where affected, required
Make targets and M6 CI remain pending milestone implementation. M5's published
591-test verification is pinned to its own final revision and is not presented
as full M6 validation. This initial work is local on `codex/m6`, outside PR #5.

## Read projection and transport increments

Branch: `feature/m6` as explicitly requested. No M6 completion is claimed.

- `m6-event-reads-red.txt`: 3 failed behavioral assertions (missing snapshot costs,
  missing event watermark and missing gap detection), 0 errors/skips.
- `m6-event-reads-green.txt`: 3 passed, real PostgreSQL, 0.60s. The read query
  projects metadata and retained events in one statement; snapshot costs share
  the task's single MVCC statement. No connection survives the read method.
- `m6-stream-red.txt`: 5 failed behavioral assertions for caps, authentication
  validity and transport lifecycle, 0 errors/skips. Importable interface scaffold.
- `m6-stream-green.txt`: 5 passed, 0.30s. ASGI send timeout is tested directly;
  proxy/HTTP middleware behavior remains a separate pending integration gate.
- `m6-sse-red.txt`: 3 failed, missing HTTP route and shared replay, 0 errors/skips.
- `m6-sse-green.txt`: 6 passed (including read projection regression), 2.02s,
  real PostgreSQL, owner checks before cursor errors, owned 410 and shared
  polling/cache with no checked-out connection while subscribers wait.

These increments do not establish the entire F17–F19 gates. Redis notifications,
load/outage, real proxy flushing, post-header reset, revocation integration,
usage estimate classification, tombstones, operations and tracing remain pending.

## Subsequent RED / GREEN records

All files below are retained in `reviews/evidence/`. Behavioral failures precede
the corresponding application changes. Infrastructure/import/setup errors are
listed separately and are never counted as RED. Existing tests remain enabled.

| Increment / confirmed correction | Meaningful RED | Focused GREEN / evidence |
|---|---|---|
| Backlog reset after retention | stream-backlog:1failed | 6passed |
| Actual middleware send backpressure | stream-transport:1failed | 19passed including identity regression |
| Shared cache stale/read-ahead race | hub-race:2failed | 8passed |
| Deleted-task stale publication | hub-deletion:1failed | 9passed |
| Absolute lifetime/final send accounting | lifetime:1failed | 11passed |
| Provenance, tombstone and durable trace | projections:4failed; behavior:2failed2passed after schema | 11passed |
| Safe correlated telemetry formatter | telemetry:1failed | 11focused foundation/unit checks; later call/network coverage |
| Post-commit Redis hints | notifications:2failed | 6passed |
| Actionable rules | operations:12failed | 12passed |
| Real operator metrics/CLI | observability:2failed | 47targeted passed |
| Pool acquisition observation | pool-observation:1failed | included47targeted |
| Authorized call correlation | call-trace:1failed | network+observability8passed |
| SQL trace CHECK and nested notification commits (C1/C2) | core-review:6failed | review-fixes:21passed |
| SDK sampled/random trace flags | sdk-flags:4failed | review-fixes:21passed including00,01,02,03 |
| Actual alert input wiring (C3) | alert-inputs:2failed | review-fixes:21passed |
| Public progress-value validation | public-values:2failed | 6passed including bounded multi-batch replay |

Additional coverage of existing guarantees needed no implementation change:
18passed for projection/MVCC and migration paths; real proxy/observability8passed;
F19 enriched profile1passed with100streams,120reads and one physical request/effect.
These are verification additions, not invented retrospective RED evidence.

Intermediate full regressions retained failures honestly:640-test stream run
initially had1failure (preserved M1 logger name contract), then640passed; core
650-test run had1failure (the exact table whitelist needed the new table added).
No existing assertion was loosened or removed. Final full suite supersedes these
intermediate revisions, without erasing their results.

Setup failures retained separately: proxy read-only temp paths, stale installed
test image, Colima-unshared temporary marker path, missing CLI module invocation,
and a Python3.14 syntax/import error. They do not establish behavior RED. The
first over-restrictive trace-flags fix rejected legitimate SDK02 values; retained
core-review-green/degraded-shared logs show this failure before the corrected
four-value contract was tested green.

Refactoring replaced buffered middleware with transparent ASGI forwarding,
factored shared batch/reset handling, and tracked nested transaction hint frames.
Money formulas, B/C boundaries, local fencing, unresolved provisions and M5
protocol contracts remain covered by the complete regression. M1/M2's previously
accepted historical TDD traceability exception remains limited to that old code.


Final installed-image regression after all confirmed fixes: **688 PASS,
0 FAIL, 0 ERRORS, 0 SKIPPED in163.78s** (`m6-regression-release.txt`).
The reviewer independently approved the inspected source; selected hashes were
rechecked unchanged. Named Make gates and GitHub CI are recorded in the release
report when observed. Final runtime-alert mapping correction:1failed1passed RED,
then14passed GREEN (`m6-alert-log-*.txt`).
