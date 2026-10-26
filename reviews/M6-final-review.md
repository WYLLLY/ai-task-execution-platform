# Independent M6 final code review

## Scope and verdict

**Code-review verdict: APPROVE for the inspected M6 working tree.** No confirmed
finding remains open after the corrections below. This is not a claim that M6's
full release gate, final installed-image regression, GitHub CI or merge has
completed. Those checks were still being performed by the primary author.

Review baseline: M5 `43f63971a83832150a0e2123b6df4706550ac443`; committed initial M6
slice at HEAD `e411d230e62ec3d5ad18ffa43d6ee81dab12949d`, together with the supplied
tracked working-tree diff and untracked M6 application, migration, infrastructure,
test and operations files. This is a working-tree review, not an assertion that
an immutable final commit already exists. The pre-existing AGENTS.md edit was
inspected as context and left untouched. The reviewer changed only review reports.

The reviewer reread `adversarial-review`, `execution-reliability-review` and
`agent-security-review`; applied AGENTS.md, approved plan §3.6, M6/F17–F19 and
operations/resource contracts; inspected Git status, unstaged/staged summaries,
the relevant tracked diff and new files. M7 implementation, deployment, retention
jobs and disaster recovery are outside this review. No merge, push, application,
test or infrastructure mutation was performed by this reviewer.

## Findings and closure

| Finding | Final inspected status | Enforcement and regression evidence |
|---|---|---|
| H1: stale cache publication / busy wait | Closed | Cache publication rejects metadata behind the poller and the negative deletion sentinel; waiting distinguishes newer progress from lagging metadata. Deterministic behind/ahead/deletion barrier regressions pass. |
| H2: middleware bypasses transport deadline | Closed | Transparent ASGI middleware directly awaits the physical sender; the full application blocked-send regression retains position zero. The reviewer independently reproduced old failure and corrected completion. |
| M1: backlog retention fails without reset | Closed | One post-header batch helper covers both immediate backlog and waiting reads, emits a reset without ID and closes. Unexpected gaps also invoke a fixed invariant alert. |
| C1: NULL-permissive trace CHECK | Closed | Non-null trace JSON now requires a TRUE bounded-object validation, required nonzero trace/span IDs, fixed fields and SDK-compatible flags `00`–`03`. SQL NULL remains valid for legacy attempts; context updates are immutable. Negative inserts and all four pinned-SDK flags are maintained tests. |
| C2: savepoint commit publishes too early | Closed | Pending task IDs are tracked by SQLAlchemy transaction frame; savepoint success merges to its parent, rollback drops only that frame, and only successful outer commit queues hints. Tests cover nested commit plus outer rollback and preserving earlier outer work across nested rollback. |
| C3: rules lack actual runtime inputs | Closed within the documented one-process deployment | The dedicated CLI collects bounded allowlisted API process metrics; collection failure has an explicit unknown-status alert. Worker heartbeat failures emit the fixed actionable alert at source while abandoning ownership immediately. Hold timers, process-local streak/reset limitations and collection boundaries are documented. |
| Follow-up: missing runtime alert logger mapping | Closed | Review reproduced that `runtime.check_missing` lost its ID/runbook in stderr. A new regression checks every configured rule against the formatter; the fixed mapping passes independently. |

Original failure scenarios and diagnostics remain in `M6-stream-review.md` and
`M6-core-review.md`; their historical CHANGES REQUIRED verdicts are not erased.

## Reliability and security assessment

- **F17 / coherent reads and replay.** State, result, costs, version and watermark
  are projected in one task SELECT with a correlated cost aggregate. The added
  deterministic MVCC test pauses the actual SELECT after execution while another
  connection settles usage, then requires an entirely old projection followed by
  an entirely new projection. Event metadata and ordered rows share one SQL
  statement; replay validates expected sequence, retention, task-specific cursor,
  100-event/raw-byte limits and bounded public wire events. Multi-batch replay
  checks every sequence across 151 retained events. Owner filtering precedes
  cursor errors and tombstones retain owner-only 410 behavior.
- **F18 / streaming bounds and authorization.** Per-owner/global admission is
  capped at 5/100. Streams retain no SQL connection across waits or sends; shared
  polling/cache serves a followed task. Authentication validity is counted from
  check start, rechecked before writes and cannot renew after failure; a separate
  outer session lifetime bounds waits and completion. Cursor advancement follows
  a successful actual send. Tests inspect revocation and blocked authorization DB
  checks, real proxy flushing/reconnect, post-header reset, transport stall,
  lifetime, disconnect and release behavior. Bytes handed to transport cannot be
  recalled; no business delivery acknowledgement or exactly-once claim is made.
- **F19 / Redis degradation.** Notification queues/hints are bounded and lossy,
  only UUIDs are published, and reference PostgreSQL polling survives missing or
  duplicate hints. The host-side controller stops/restarts only test Redis and
  gives the test container marker files rather than a Docker socket. The enriched
  harness opens 100 real TCP streams across 20 tasks/owners, schedules 120 reads
  at 10/s, verifies creation 503, retained replay 202, cancellation 200, renewal at
  the actual 10-second interval, durable progress and recovery. B/HTTP simulator/C
  also runs while Redis is down, with an independent physical request/effect
  journal asserting one request and one effect. This is a short controlled
  acceptance profile, not production performance evidence.
- **Accounting, migration and permissions.** `estimated` is a safe public
  provenance flag; administrative source/proof/reason data stays private. Cost
  replacement and same-amount provenance change preserve money totals while
  updating revision; unknown exposure and late terminal-task usage remain
  represented. The populated M5 upgrade test retains estimated charges and
  permits legacy attempts without fabricated trace ancestry. API writes to costs
  and tombstones remain forbidden. Durable execution lock/order, A/B/C ownership
  and existing accounting constraints were not replaced with new orchestration.
- **Observability and isolation.** Short OpenTelemetry spans retain immutable
  per-attempt context and previous-attempt linkage; committed B correlates the
  task/step/attempt/call without provider/source bodies. Root sampling and local
  allowlisted export are bounded; no implicit OTLP exporter is configured. Logs
  drop arbitrary messages, URLs, headers and exceptions. Metric names are fixed
  and have no user/task labels. The private proxy blocks `/metrics`; the API
  retains no OpenAI secret. New service mounts/capabilities/network exposure are
  bounded and the proxy image is pinned. Supervisor-parent imports remain small.
- **Verification entry points.** `make test-integration` uses the real proxy and
  external fault coordinator; M6 gates exist and ordinary CI invokes them, with
  cleanup including the proxy. CI remains synthetic, includes feature/m6 and
  does not invoke the paid OpenAI smoke. Existing assertions were not weakened;
  changed existing expectations reflect the new schema revision/table rather
  than reduced coverage.

## Independently executed checks

The first local complete-unit attempt used a vanished tokenizer cache path:

```text
TIKTOKEN_CACHE_DIR=/private/tmp/atp-m5-tokenizer .venv/bin/pytest tests/unit -q --tb=short
186 passed, 7 failed, 0 errors, 0 skipped in 1.49s
```

Six failures were `tokenizer_asset_unavailable` caused by the missing prewarmed
local asset, not product regressions and not TDD RED. The seventh was the author's
new formatter regression during its meaningful RED phase. The reviewer neither
downloaded an asset nor changed/disabled a test to make this run pass.

After the logger correction, the reviewer selected all asset-independent units:

```text
.venv/bin/pytest tests/unit -q --tb=short -k \
 'not smoke_requires_bounded_stateless_tool_roundtrip and \
  not first_turn_overrun_cannot_spend_the_second_turn_reservation and \
  not request_uses_complete_context_strict_tools_and_stateless_replay and \
  not complete_context_bound_includes_history and \
  not missing_tokenizer_asset_fails_closed_without_runtime_download'
187 passed, 0 failed, 0 errors, 0 skipped, 6 deselected in 1.43s
```

The six deselections are this reviewer's explicit environment limit; they are
not a complete unit-suite PASS or a proposed reduction in maintained CI. The
primary author runs the complete suite in the final prewarmed image.

Independently executed repository static checks:

- `.venv/bin/ruff check .`: **PASS**.
- `.venv/bin/ruff format --check .`: **PASS**, 150 files already formatted.
- `.venv/bin/mypy`: **PASS**, 121 checked source files.
- `git diff --check`: **PASS** for the tracked working-tree diff.

No PostgreSQL, Redis, migration, network, Compose, live-provider or CI checks were
executed by this reviewer in the final review phase, at the author's request to
avoid shared-environment interference. No result from those checks is labeled an
independently executed PASS here.

## Inspected primary-author evidence

The reviewer inspected the maintained test code and retained logs, rather than
substituting fixture results for real provider evidence:

| Retained evidence | Recorded result inspected |
|---|---|
| `m6-core-review-red.txt` | 6 meaningful behavioral failures for SQL validation / premature savepoint hints |
| `m6-alert-inputs-red.txt` | 2 behavioral failures for absent real pool/renewal inputs |
| `m6-review-fixes-green.txt` | 21 passed in 4.71s |
| `m6-migration-mvcc.txt` | 18 passed in 8.79s |
| `m6-public-values-red.txt` | 2 meaningful failures for unvalidated public metadata |
| `m6-public-values-green.txt` | 6 passed in 0.45s |
| `m6-degraded-execution.txt` | 1 passed in 13.23s; 100 streams, 120 scheduled reads, 11.986s profile, p95 reads 0.0161s, one request/effect, committed execution |

Historical setup/tokenizer/SDK-flag failures are retained; they must remain
separate from meaningful behavioral RED and final installed-image results.

## Release conditions and documented limits

Final full M1–M6 installed-image regression, migrations/roles, gates, smoke,
immutable commit/PR and CI results remain the primary author's completion gate.
No final totals are invented here, and this review does not approve a failed or
unexecuted required gate. Recheck the final commit against this reviewed code
before merge. M7 starts only under its separate authorization and prerequisites.

The runbook states the operative limits: one API process; lossy notifications;
process-local renewal streaks and hold timers reset on process restart; fixed
private API collector; queue/acquisition observations exclude post-checkout
pre-ping; no provider invoice/token guarantee; synthetic controlled load rather
than a production benchmark. Disk/backup observations must be supplied by a
trusted operator; absent observations alert as unknown, and M7 backup/restore
jobs are not presented as implemented. No paid OpenAI smoke was authorized or
executed by this reviewer.

Selected final inspected SHA-256 anchors (working-tree bytes):

```text
event_reads.py       271e04444255d47644b6191ef251b409245a54311842297bb18af55413857f98
event_hub.py         24feac23d6935ff9304cedbb2247fa89062f5e5162af06e97a0f0945b7511ab9
sse.py               1ab3bd71e4300d9e1c94e4f54ccd9280000ab622f3da96824a8c0945eceeb6ee
notifications.py     b3ac07e0aa741f0b6a02105d47746e112dbe0fb7d8e75e241424d5a5e8a74ae7
operations.py        efb27910f117f4b0d455f4c8e60e1b7cf10d8c32c573e46830599ae64066a846
observability.py     6d979e4e019e8b7ba81f977b3be241dcd8bc80333891993ed08b7bd2bbedf09d
0006_operations.py   9bb0134a0f826f1664041ce98762c80caed61daa640717b03c297f8383ca4ffb
```
