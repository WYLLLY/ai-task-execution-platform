# Independent M6 core review — working tree after e411d23

Scope: the M6 operations/projections/telemetry/notification/migration increment,
plus closure of the initial stream findings. The target is the supplied working
tree after committed initial slice `e411d23`; it is not a final immutable M6
release. Review performed while the primary author owns network verification and
the shared disposable PostgreSQL/Redis environment.

Applied review guidance: `adversarial-review`,
`execution-reliability-review`, `agent-security-review`, and `verification`.
The approved M6/F17–F19 contracts and AGENTS.md remain the reference. Inspected:
`0006_operations`, event projections/hub/stream/API, notification session hooks,
attempt/call telemetry, execution settlement/claim hooks, provider reconciliation,
observed pools, operations CLI/rules, logger redaction, and associated unit and
integration tests. No application/test/Git changes or database checks were made
by this reviewer. New isolated network harnesses, runbooks, Make/CI wiring and
full milestone gates were pending author work and are not certified here.

**Verdict: CHANGES REQUIRED for the core findings below.** The three initial
stream findings are closed within their reviewed correction scope; their original
evidence remains in `M6-stream-review.md`.

## C1 — Trace JSON constraint accepts structurally unusable contexts

- Severity: Medium.
- Location: `migrations/versions/0006_operations.py`, `attempts.trace_context`
  CHECK; consumers in `telemetry.attempt_context` / `call_progress`.
- Scenario: insert an attempt with non-null `{}`, with just one required ID, or
  with valid IDs and a malformed `flags` field. The required-ID regex expressions
  become SQL NULL for missing keys, making the full CHECK UNKNOWN; PostgreSQL
  CHECK accepts UNKNOWN. `flags` is not constrained at all. A subsequent claim
  using this retained previous attempt raises `KeyError` or `ValueError` rather
  than creating its next durable attempt.
- Impact: the advertised structural invariant is not enforced and an accepted
  unusable checkpoint can repeatedly prevent progression. This is a persistence
  integrity defect, not an API caller permission bypass or a claim to withstand
  compromise of a trusted SQL role.
- Cause: nullable JSON field extraction is combined with AND without requiring a
  final TRUE result. Regexes also admit all-zero IDs, which OpenTelemetry treats
  as invalid, and the consumer parses optional flags without boundary validation.
- Existing coverage: tests assert generated IDs and crash linkage, but do not
  attempt malformed contexts through the migration constraint.
- Recommendation: preserve SQL NULL for legacy attempts, but require non-null
  contexts to have required, nonzero IDs, bounded expected fields/types and valid
  flags if present. Ensure the validation expression cannot succeed as UNKNOWN.
- Required tests: real PostgreSQL insertion rejection for `{}`, missing/null IDs,
  invalid/all-zero IDs and malformed/null flags; valid generated context and
  legacy SQL NULL remain accepted. Verify immutable-context update denial and
  restart linkage. No database execution of this finding was performed by this
  reviewer; the defect follows the inspected CHECK's SQL NULL semantics.

## C2 — SAVEPOINT commit publishes a hint before the outer commit

- Severity: Low, because hints are deliberately lossy and PostgreSQL replay
  remains authoritative.
- Location: `notifications.committed` / `rolled_back` session hooks and the
  existing `Executor.complete()` nested progression transaction.
- Scenario: the outer transaction flushes `usage.updated`, accumulating a wake;
  it opens and commits its business-progression savepoint. SQLAlchemy emits
  `after_commit` for that nested commit too. The hook immediately queues the
  hint although the outer transaction is still open. A later outer rollback
  leaves the hint queued. A nested rollback also drops accumulated hints from
  before that savepoint, since rollback clears the entire pending set.
- Impact: the implementation's post-commit-only publication claim and its
  rollback test do not hold on a real existing application path. Consumers can
  be awakened before progress is visible. This does not create false durable
  events, authorize work or defeat the fallback SQL poller.
- Cause: hooks do not distinguish outer transactions from nested/savepoint
  events or track which pending events belong to which transaction boundary.
- Existing coverage: only ordinary outer commit/rollback is exercised by the
  notification integration test.
- Recommendation: publish only at the successful outer transaction boundary;
  either track nested pending state correctly or explicitly preserve the
  documented lossy behavior without early publication. Do not await Redis in a
  SQL transaction.
- Required tests: nested commit followed by outer rollback queues no hint;
  outer commit after nested success publishes only then; nested rollback cannot
  publish rolled-back progress. Test earlier outer progress across a nested
  rollback if the implementation promises to retain that hint.

Independent diagnostic used an **unbound** SQLAlchemy `Session` and the existing
hooks; it requires no database or modified repository files:

```text
Session(info={notifications: notifier}).begin()
session.info[event_wakes] = {task_uuid}
session.begin_nested().commit()
inside_outer_transaction: True
hints_before_outer_commit: 1
session.rollback()
hints_after_outer_rollback: 1
AssertionError: savepoint commit published a hint for a rolled-back outer transaction
```

This is a session-hook behavioral reproduction, not a PostgreSQL/Redis proof or
the implementation author's maintained TDD evidence.

## C3 — Pool and renewal alerts have rules but no runtime input path

- Severity: Medium if this increment is presented as completing those operation
  alerts; retain as explicitly pending work until wired.
- Location: `runtime_signals.py`, API `/metrics`, `operations.operational_snapshot`,
  `cli.run_admin` operations loop, and worker heartbeat.
- Scenario: three workers fail renewal, or the API read pool remains over 80%
  for a minute. The only `renewal_failures` definition stays zero: no code
  increments it. The operations watcher evaluates only durable SQL values plus
  disk/backup probe fields, so it never receives pool utilization/acquisition or
  worker renewal signals. Missing fields default to zero in `Alerts.evaluate`.
- Impact: those rule thresholds can pass unit tests yet never emit the required
  actionable production alert. `/metrics` exposes the API pool measurement, but
  no configured evaluator/collector path consumes it in this increment. Worker
  process data is not transported into the API's process-local variables.
- Existing coverage: synthetic rule input and a real read-pool delay validate
  threshold logic/measurement separately; they do not demonstrate the complete
  failure-to-alert path.
- Recommendation: connect actual API/worker observations to the intended alert
  evaluator or emit bounded actionable alerts at the source. State the deployment
  wiring explicitly; do not describe a permanently zero placeholder as a measured
  worker signal. Existing early worker termination on a failed heartbeat should
  not be relaxed to satisfy an alert counter.
- Required tests: inject actual renewal failures and sustained real pool
  pressure, then observe an actionable fixed-ID alert through the configured
  deployment path, without private content. This is part of pending M6 operations
  completion, not a request to add another milestone or a new broker.

## Positive mechanisms and remaining evidence limits

- Public usage and task snapshots aggregate explicit provenance/exposure fields
  with owner filters; the API is not granted administrative evidence details.
  New `estimated` writes are restricted to worker/admin. A same-amount provenance
  change increments revision without adding a second charge. The upgrade attempts
  to derive old estimate flags from final matching administrative evidence.
  Existing migration tests did not demonstrate a populated M5 estimate/provenance
  upgrade; add that case before claiming this upgrade preserves every old record.
- Tombstones retain owner identity and only owner-filtered 410 behavior; no purge
  implementation is introduced by their read projection. Public write access is
  denied, and hints contain only task UUIDs.
- Notification queues are bounded to 100 and Redis failures drop hints instead
  of holding SQL transactions. Polling remains the source of event progress.
- Telemetry uses fixed short span names, bounded sampling, a local safe exporter
  and allowlisted UUID/trace log fields; source bodies, provider opaque items and
  arbitrary exception strings are omitted. Call correlation follows the successful
  B transaction return. The supervisor parent's import boundary remains lightweight;
  OpenTelemetry is loaded at runtime execution rather than at parent CLI import.
- Metric names and labels are bounded; no task IDs appear in metric labels. Pool
  observations use a bounded deque and do not consume reserved control capacity.
  The present `_do_get` observation measures pool queue waiting, not the entire
  connect/pre-ping acquisition path; document that limit or extend measurement
  before claiming total acquisition latency.

Independently executed:

```text
.venv/bin/pytest tests/unit/test_event_cursor.py tests/unit/test_event_hub.py \
  tests/unit/test_sse_stream.py tests/unit/test_operations.py -q --tb=short
57 passed, 0 failed, 0 errors, 0 skipped in 0.56s
```

Ruff and format over telemetry, notifications, runtime signals, operations, event
reads, DB observations and migration `0006_operations`: **PASS**, seven files
already formatted. The notification savepoint diagnostic intentionally failed
its behavioral assertion as reported above. No mypy, full regression, database,
privilege, migration, network/load, real provider or CI result is claimed by
this reviewer. The primary author's ongoing verification is separate evidence.
