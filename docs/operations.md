# M6 operations

M6 supports one API process and bounded worker slots. PostgreSQL owns execution,
permissions and accounting. Redis hints are lossy acceleration only. This runbook
is not a claim of high availability, provider exactly-once execution, production
TLS deployment, backup implementation or disaster-recovery readiness.

Use the dedicated administrator credentials:

```sh
atp admin operations --format json
atp admin operations --format prometheus --watch
atp admin operations --format json --watch --probe-file /run/operations/probe.json
```

The watcher samples every five seconds. Keep the same process running for the
120-second queue and 60-second pool hold timers. Restarting the watcher resets
pending alert timers. Immediate alerts do not require a hold. Output contains
fixed aggregate metric names and alert IDs with runbook links, never source
bodies or provider protocol items. `/metrics` on the private API exposes only
process aggregates; the private test reverse proxy blocks that route. The CLI
collects the API metrics using a fixed destination, one physical request per
sample, no proxy environment, redirects or transport retries, a one-second
absolute deadline and 16 KiB response limit. Collection failure emits
`runtime.check_missing`; it must not be interpreted as healthy pool state.
There is no public financial metrics endpoint. Never publish the internal API
port or administrator output to an untrusted network.

The API has eight read connections and two reserved control connections. Worker
pools have four execution and two control connections. Pool acquisition timing
includes connection creation in `_do_get`; connection validation/pre-ping after
checkout is not included. Slow observations are retained for 60 seconds in a
bounded 256-entry deque. Metrics contain no task, owner, API-key or document
labels. A real PostgreSQL contention test verifies that a saturated read pool
leaves a separate control pool available.

## Queue

`queue.delayed`: eligible ready work older than 30 seconds for 120 seconds.
Scheduled capacity waits, expired tasks and old execution epochs are excluded.
Check worker health, compatible handlers, provider capacity and DB waits before
opening admission or adding capacity. Do not reset durable attempt counters.

## Leases

`lease.expired`: an expired running lease older than 15 seconds.
`lease.renewal_failed`: three consecutive failed renewal observations in a worker
process. Workers emit this fixed alert at the source; they still abandon local
execution immediately on each failed heartbeat. Successful renewal resets the
process streak. The counter is process local, not a deployment-wide durable sum;
collect worker JSON logs separately from the API pool scrape. Worker restarts
reset the streak. Never revive an expired lease. Inspect the DB/control pool,
event loop and task deadline, and stop a defective worker. Local interruption
does not prove remote cancellation.

## Pools

`pool.saturated`: maximum API pool utilization above 80% for 60 seconds.
`pool.acquire_slow`: an observed acquisition above one second during the last
60 seconds. Reduce read traffic, inspect long transactions and preserve control
capacity. `runtime.check_missing` means the private API measurement could not be
validated. The one-process metrics collector does not aggregate other hosts.

## Accounting

`accounting.unknown`: retained unknown exposure older than 15 minutes.
`accounting.overrun`: an actual overrun lacks acknowledgement of its current
revision. Keep the full recorded amount and provisions. Reconcile using the M5
administrator evidence commands; never release by age or regenerate an ambiguous
call automatically. Acknowledge the exact current overrun revision only after
reviewing provider evidence and tariff/estimator configuration.

Public snapshots and `/v1/usage` separate confirmed charges, estimates, uncertain
provisions, tokens and overrun counts. Estimate-to-confirmation changes replace
provenance without charging the same amount twice. SQL aggregates and the task
watermark share one MVCC statement. Terminal task state/results stay immutable
when late evidence updates usage and emits another event.

## Handlers

`handler.unavailable`: suspended tasks require unavailable pinned handlers.
Restore the compatible version, run preflight and use authorized resolution.
Never silently select a newer workflow or context builder.

## Events

`events.invariant`: an unexpected gap or invalid public event metadata.
Inspect continuity and retained watermark metadata. An active stream sends
`stream.reset` without an ID and closes; clients fetch a fresh snapshot. Before
headers, an owned expired/inconsistent history returns 410 and the snapshot URL.
Foreign resources remain 404, including retained tombstones.

Clients fetch `/v1/tasks/{id}`, then subscribe after its `event_sequence` with
`after=task_uuid:sequence` or `Last-Event-ID`. If both are sent they must match
exactly. Heartbeats carry no IDs. Reconnect after the last physically received
ID, using bounded client backoff. Do not reconnect indefinitely on 401/403/404.
A terminal task does not close its stream immediately: late accounting can still
arrive before the five-minute session limit. Each new connection reauthenticates.

Limits: five streams per owner, 100 globally, batches of at most 100 events and
256 KiB, individual wire events at most 16 KiB, write deadline five seconds,
connection lifetime 300 seconds, idle heartbeat ten seconds. Authorization is
rechecked every five seconds, valid at most ten seconds from DB check start;
failed checks never renew it. Already written bytes cannot be recalled. The
shared poller queries each followed task every second, can accelerate on a
bounded UUID-only Redis hint, and releases SQL sessions before waits/writes.
Cache holds at most two batches per task and cannot republish stale metadata.

## Storage

`storage.disk_full`: verified data/WAL volume utilization exceeds 80%.
`storage.check_missing`: no verified observation supplied. The external probe
must describe the actual PostgreSQL data/WAL filesystem, not the API container's
ephemeral disk. Stop new admission if space is unsafe and investigate retained
exposure before deleting anything. Logical storage counters are not physical
PostgreSQL/WAL/index usage. M6 does not implement M7 retention jobs.

## Backups

`backup.failed`: the supplied last backup observation failed.
`backup.stale`: verified last success is older than 24 hours.
`backup.check_missing`: no verified success/observation exists.

The optional probe file is strict JSON, supplied by a trusted operator:

```json
{"disk_used_percent": 42, "backup_last_success": null, "backup_failed": false}
```

Use an actual timezone-aware timestamp after a verified success. Missing or
future timestamps are not success. The backup execution/verification and restore
procedures belong to M7; M6 only consumes observations and reports missing
checks explicitly. Never describe the absence of a backup as PASS.

## Verification and Redis degradation

```sh
make test-integration
make test-sse test-degraded test-observability
```

The disposable test stack includes an immutable private Nginx fixture for real
SSE flushing. A host-side controller stops/restarts only the test Redis service;
the test container has no Docker socket. The F19 profile opens 100 TCP streams
for 20 owners, schedules 120 reads at ten per second, verifies creation 503,
retained replay 202, cancellation 200, renewal at the real ten-second heartbeat
interval, PostgreSQL event progress, and one simulator HTTP request/effect with
B/C committed while Redis is stopped. It then verifies recovery. This short
controlled profile is acceptance evidence, not a production capacity benchmark.
The full suite must use `scripts/run_verification.py` so the fault is executed,
not skipped. Ordinary CI never calls the paid OpenAI API.

Trace context is retained per attempt, immutable and linked across replacement
attempts. Spans cover short transactions/call progress, not an entire task. The
pinned OpenTelemetry SDK 1.45.1 emits sampled and random-ID flags (`00`–`03`),
which the SQL boundary validates along with nonzero IDs. See the
[OpenTelemetry SpanContext contract](https://opentelemetry.io/docs/specs/otel/trace/api/#spancontext)
and [W3C trace flags](https://www.w3.org/TR/trace-context-2/#trace-flags).
Opaque provider items and source content are never trace attributes. Sampling
uses a parent-based 10% root ratio and a local safe JSON exporter, without
implicit OTLP network export.
