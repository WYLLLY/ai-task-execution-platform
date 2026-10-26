# Independent review of the initial M6 stream slice

Review target: commit `e411d23`, based on M5 commit `43f6397`.
This report covers the committed cursor/read/hub/stream/API increment, not a
completed M6 milestone. The implementation author continued work in the shared
checkout during review; subsequent changes require their own verification.

Skills applied: `adversarial-review`, `execution-reliability-review`,
`agent-security-review`, and `verification` reporting guidance. Sources read:
AGENTS.md, approved plan §§3.6, 4.4–4.5, M6, F17–F19 and §7.3, the accepted
architecture-review response, the initial M6 matrix, affected implementation and
associated unit/integration tests. No application or test files were modified;
no Git operations, database tests, external requests or paid provider calls were
performed by this reviewer.

**Scoped verdict: CHANGES REQUIRED.** Three implemented-scope defects were
confirmed. Missing full-milestone features listed below are separate pending
work, not additional findings against this deliberately partial increment.

## H1 — A read can republish a stale batch after poll invalidation

- Severity: High.
- Location at the reviewed revision: `event_hub.py:90–94,103–128`.
- Contract: F17/F19 require eventual contiguous replay through shared PostgreSQL
  polling, with bounded resource use.
- Scenario: a `batch()` cache miss starts a database read using snapshot W=0;
  an event commits; the poller observes W=1 and clears the cache; the older read
  finishes and inserts W=0 after that invalidation. Every later request at the
  same position reuses W=0. `wait()` immediately returns because the poller's
  metadata is different, so the stream repeatedly reads the same cached batch.
  An analogous spin occurs when a fresh batch is ahead of lagging poll metadata:
  inequality alone does not establish that the cached batch needs refreshing.
- Impact: clients miss committed events and the event loop can enter an
  immediate-return loop until another operation yields, affecting unrelated
  streams/control requests. Repeated unchanged polls do not clear the resurrected
  cache entry.
- Cause: cache publication is serialized by `state.lock`, while poll invalidation
  uses a different condition lock. No generation check protects a read spanning
  invalidation; the wait predicate treats older and newer metadata alike.
- Existing coverage: the shared-cache integration test uses unchanged metadata
  and concurrent subscribers; it does not synchronize publication across a poll.
- Minimal recommendation: coordinate invalidation/publication, or reject a read
  whose cache generation changed. Define waiting so a lagging poll observation
  cannot cause a hot loop against a newer cached snapshot.
- Required tests: deterministic barriers around a cache-miss read and poll
  invalidation; test both read-behind-poll and read-ahead-of-poll orderings. Assert
  convergence to the new event and a genuine wait/yield rather than repeated
  immediate stale responses. Add a real PostgreSQL interleaving for the MVCC
  contract during milestone verification.

Independent pure concurrency probe used an import-level replacement of
`read_events` and explicit `asyncio.Event` barriers, without changing repository
files or accessing a database. Its behavioral oracle failed:

```text
poll_watermark: 1
cached_watermarks after five wait/batch cycles: [0, 0, 0, 0, 0]
underlying reads: 1
AssertionError: cache resurrected pre-invalidation MVCC snapshot and cannot converge
```

This proves the in-memory race; it is not presented as a PostgreSQL test.

## H2 — HTTP middleware bypasses the real transport write bound

- Severity: High.
- Location: `api.py` HTTP request middleware and `sse.py:109–125,166–191`.
- Contract: §3.6/F18 require a five-second client write bound, bounded connection
  lifetime and resource release for slow clients.
- Scenario: FastAPI's decorator installs Starlette `BaseHTTPMiddleware`.
  `Stream` sends into that middleware's memory channel; the middleware later
  calls the actual outer ASGI `send` outside `Stream.write()`'s timeout. Block
  that outer send on the first nonempty body. The stream can advance its cursor
  and release its connection ticket while the HTTP request remains blocked.
- Impact: real slow-client requests can outlive the write/lifetime limits and
  remain live after application connection counters have been released, allowing
  the counters to understate outstanding resources.
- Cause: the direct-stream test supplies the real blocking sender directly and
  bypasses the actual API middleware stack.
- Existing coverage: the isolated `Stream` transport timeout test passes; no
  complete API ASGI send-backpressure regression existed in the reviewed slice.
- Minimal recommendation: use an ASGI boundary that preserves the actual
  transport sender inside the stream deadline, and retain the intended sanitized
  errors/logging behavior. Verify through the full middleware stack.
- Required test: block outer ASGI response-body send through the configured
  middleware; assert bounded request completion/cancellation, accurate connection
  accounting, and no cursor advancement on the blocked physical send. Real proxy
  flushing/backpressure remains a separate required gate.

Independent pure ASGI reproduction used a minimal FastAPI app with the same
HTTP middleware form and the committed `Stream`, with a 20 ms write limit and
50 ms lifetime. After 150 ms:

```text
app_done_after_150ms: False
released: [True]
position: 1
AssertionError: real ASGI send outlives Stream timeout and lifetime through HTTP middleware
```

No socket/proxy guarantee is inferred from this synthetic sender probe.

## M1 — Retention during backlog draining omits the required reset

- Severity: Medium.
- Location: `sse.py:135–137` versus `sse.py:155–164`.
- Contract: §3.6/F17 require `stream.reset` without a new ID and closure when
  retained history disappears after headers.
- Scenario: an initial batch does not reach its watermark because of the
  100-event/byte limit. After emitting it, retention advances beyond the next
  position. The immediate backlog branch calls `hub.batch()` outside the reset
  exception handler. `HistoryExpired` escapes instead of emitting the reset.
- Impact: the client sees an unexplained broken stream rather than the explicit
  instruction to reload its snapshot. Unexpected event gaps in this same branch
  likewise bypass reset handling.
- Cause: only the read following `hub.wait()` has the reset handler.
- Existing coverage: pre-header retention 410 was tested; post-header retention
  while draining a multi-batch backlog was absent from the committed tests.
- Minimal recommendation: apply identical post-header retention/gap handling to
  every batch read, including the immediate backlog path; preserve invariant
  alerting for unexplained gaps.
- Required test: initial partial batch followed by `HistoryExpired`, then verify
  exactly one no-ID reset and closure. Repeat with `EventInvariantError` and
  verify the invariant alert when observability is implemented.

While the author added that behavioral regression during review, the reviewer
observed its initial meaningful failure at the unhandled backlog call. This was
not counted as an independent pre-existing test failure or as completed fix
verification.

## Executed checks and evidence limits

The exact original cursor and five stream unit cases were selected explicitly:

```text
.venv/bin/pytest tests/unit/test_event_cursor.py \
  tests/unit/test_sse_stream.py::test_connections_bound_owner_and_global_and_release_idempotently \
  tests/unit/test_sse_stream.py::test_auth_validity_counts_from_check_start_and_failure_never_renews \
  tests/unit/test_sse_stream.py::test_expired_during_auth_check_cannot_write \
  tests/unit/test_sse_stream.py::test_transport_timeout_releases_ticket_without_advancing_cursor \
  tests/unit/test_sse_stream.py::test_disconnect_cancels_wait_and_releases_connection \
  -q --tb=short
39 passed, 0 failed, 0 errors, 0 skipped in 0.29s
```

An earlier broad two-file run overlapped the author's newly written backlog
regression: **39 passed, 1 failed, 0 errors, 0 skipped in 0.34s**. The failure was
`HistoryExpired` from the immediate backlog branch described above. This review
does not claim that the committed baseline itself already contained that test.

The cache-race and middleware probes each terminated with the intended failed
behavioral assertion, not an import, connection or infrastructure failure.
They were ad hoc diagnostics, not maintained test counts or TDD proof for the
author's future fixes.

Protected mechanisms inspected: bounded task-specific cursor parsing; owner SQL
filtering before cursor errors; single-statement snapshot and event projections;
state/version-only public progress payloads; independent per-stream authentication
leases counted from check start; idempotent connection/subscription release; no
database connection retained across stream sends/waits; cache capacity bounded
to two batches per followed task. Passing unit checks cover only their selected
properties and do not override the findings.

**Not executed by this reviewer:** PostgreSQL/Redis tests, migrations/privileges,
full regression, static checks, real proxy/TCP tests and F19 load. The primary
author owns the shared disposable infrastructure and milestone verification.
No such check is reported PASS here. Redis wakeups, post-header retention and
revocation integration, owner-visible tombstones, `/usage` classification,
100-stream degradation, observability/alerts/tracing/operator commands, final
gates and CI were explicitly pending in the initial matrix. This scoped review
does not certify M6 completion or authorize merging it.

## Independent correction check — working tree after e411d23

This follow-up reviews the uncommitted correction delta supplied by the author;
the original findings and revision above remain historical evidence. New
projection/tombstone/usage work is outside this follow-up's scope.

- **H2: corrected in the inspected delta.** `RequestBoundary` is transparent
  ASGI middleware: its wrapper directly awaits the outer sender, without a body
  iterator, intermediate channel or detached task. It preserves the established
  `task_platform.api` logger, no-store headers and sanitized pre-header 500s.
  Repeating the independent blocked-outer-send probe with this middleware, a
  20 ms write bound and 50 ms lifetime completed within 150 ms, released its
  ticket exactly once and kept `position == 0`. The real API regression and its
  recorded behavioral RED/GREEN were inspected, not rerun against the shared DB.
  Real TCP/proxy verification is still a milestone obligation.
- **M1: corrected in the inspected delta.** A single `next_batch()` helper now
  handles retention/not-found/invariant failures from both backlog and waiting
  branches, emits a no-ID reset, then returns a closing signal. The maintained
  backlog regression passes independently. Invariant alerting remains pending
  observability work rather than part of this partial correction claim.
- **H1: partly corrected, still OPEN.** Normal increasing-watermark invalidation
  is protected by rejecting older batch metadata at cache publication, and
  waiting no longer treats lagging poll metadata as a new event. Both new unit
  regressions pass. However, the poller's task-deletion sentinel is `(-1, -1)`:
  an in-flight pre-deletion batch satisfies the new numeric `>=` checks, can be
  republished after deletion invalidation, and is then returned forever. `wait()`
  immediately wakes on the negative sentinel, reproducing the hot loop and
  preventing the required post-header reset. A deletion-specific publication
  guard or invalidation generation is still required before closing H1.

The additional independent deletion-barrier probe produced:

```text
deleted_task_detected: False
underlying reads: 1
cached_positions: [0]
AssertionError: deletion sentinel permits stale snapshot cache resurrection and no reset
```

Schedule: pause the first batch read at W=0; apply the poller's actual missing-row
metadata `(-1,-1)` and clear its cache; finish the old read; repeat five
wait/batch cycles. A second read would raise `NotFoundError`, but no second read
occurs. This is an in-memory diagnostic, not a claim to have executed a purge or
PostgreSQL integration test. Add this precise interleaving to the regression
tests and verify reset plus bounded yielding after the correction.

Independently executed focused command:

```text
.venv/bin/pytest tests/unit/test_event_cursor.py tests/unit/test_sse_stream.py \
  tests/unit/test_event_hub.py -q --tb=short
42 passed, 0 failed, 0 errors, 0 skipped in 0.47s
```

Scoped Ruff and format checks on `event_hub.py`, `sse.py`, `http_boundary.py`,
`api.py` and the two affected unit files: **PASS**, six files already formatted.
No database tests, full regression or Git operations were run during this
follow-up. The author's reported full-suite run is not relabeled as independent
reviewer evidence. **Follow-up verdict: CHANGES REQUIRED while H1 remains open.**

### Deletion follow-up closure

The later working-tree correction additionally requires `state.watermark >= 0`
before caching a completed read. The deletion-barrier regression now performs a
second read and receives `NotFoundError`; the prior read cannot repopulate the
deleted task's cache. The recorded meaningful RED/GREEN in
`m6-hub-deletion-red.txt` / `m6-hub-deletion-green.txt` was inspected, and the
maintained regression was rerun independently as part of the 57-test selection
reported in `M6-core-review.md`. **H1, H2 and M1 are now closed for the inspected
stream correction scope.** This does not supersede pending real-network/load
milestone gates or the separate findings in the core review.
