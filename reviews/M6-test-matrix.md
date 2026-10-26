# M6 implementation matrix — in progress

Base: verified M5 commit `43f63971a83832150a0e2123b6df4706550ac443`.
M5 PR #5 remains open; this branch does not assume it has merged.
The pre-existing AGENTS.md edit is preserved outside this work.

This initial slice defines the pure SSE cursor contract from plan §3.6.
It does not expose an events route or establish the M6 completion gates.

| Layer / gate | Required coverage | Status |
|---|---|---|
| Unit / F17 cursor | Bounded UUID/sequence IDs, default replay, matching header/query, malformed/foreign/future IDs, retention boundary and snapshot URL | PASS: 34 unit tests; HTTP integration pending |
| Integration / API / F17 | Atomic state/result/cost/watermark; coherent bounded batches; authorization before cursor errors; contiguous replay/reset; lost/duplicate notifications | Pending |
| Security / F18 | Owner scoping, revocation within 10s from check start, failed authorization DB checks cannot renew cached access; no secret/source/opaque data exposure | Pending |
| E2E / F18 | Real proxy flushing, slow clients, reconnect storms, 256KiB buffers, 5s writes and 5min connection lifetime | Pending |
| Concurrency / F17–F19 | Shared per-task polling, bounded connections and pools, retention during replay, concurrent accounting events | Pending |
| Resilience / F19 | Real Redis outage with 100 SSE clients and sustained reads; creation503, replay/control/heartbeat continue | Pending |
| Observability / operations | Bounded labels, durable trace correlation, actionable injected-failure alert and operator commands/runbooks | Pending |
| Regression / review | Complete M1–M6 suite, applicable migrations/privileges/static checks, independent review and CI | Pending |

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
