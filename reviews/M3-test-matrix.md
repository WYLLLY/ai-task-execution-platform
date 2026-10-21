# M3 test matrix and TDD record

Scope: durable executor and simulated external contracts only. Baseline: `9cc6627` (M1/M2,
including its explicitly accepted historical TDD exception). Strict test-first development
applies to M3. The pre-existing AGENTS.md modification is user-owned and excluded.

## Coverage

| Increment | Behaviors and risks | Evidence files |
|---|---|---|
| Schema | Immutable call identities/provisions/evidence, legal transitions, ownership FKs, grants, clean migration and upgrades | `test_execution_schema.py`, `test_z_migrations.py` |
| A / heartbeat | FIFO, competing claims, holder/generation/epoch fencing, fresh DB time, expired renewal, independent control pool | `test_execution.py` |
| B / accounting | Current rights/epoch, exact payload, atomic reservations, global/owner/task money and tokens, calls, rolling windows, storage and local capacity | `test_execution_calls.py` |
| C / maintenance | Completion, unknown suspension, bounded retry, stale result, cancellation/deadline, concurrent settlements and late accounting | `test_execution_calls.py` |
| Resolution | Current owner/version/risk, explicit regeneration, retained exposure, no old continuation, quarantine denial, idempotency races | `test_execution_resolution.py` |
| Transport | Fixed destination, proxy exclusion, no retries/redirects, raw byte cap, strict outcomes, slow drip and absolute deadlines | `test_execution_transport.py` |
| Worker | API creation → A/B → physical HTTP → C; heartbeat loss and cancellation; DB loss; context overflow; CLI opt-in and metrics | `test_execution_worker.py` |
| Faults | Real process death A/B/C, commit acknowledgement loss, delayed first send after revocation/cancellation/expiry, independently supervised stall/cleanup | `test_execution_failures.py` |
| Redis outage | Actual service stop/start; reads/replay/cancellation and PostgreSQL claim/heartbeat continue | `scripts/smoke_compose.py`, `tests/support/redis_worker_check.py` |

Unit regressions run separately. M3 boundary validators and arithmetic are exercised through
transport/service integration, rather than duplicating their implementation in superficial unit
assertions. API, integration, E2E, concurrency, security, resilience and failure layers all have
applicable tests. PostgreSQL locks use independent sessions and `pg_blocking_pids` to observe
contention. The standalone HTTP simulator fsyncs its invocation/effect journal outside application
transactions; process-crash tests check both the journal and durable database state.

| Plan gates | Mapping / boundary |
|---|---|
| F01–F03 | Complete M1/M2 regression, command/idempotency/admission tests preserved |
| F04–F05 | Actual process termination after A and B, lost B acknowledgement, zero physical sends, retained unknown provision |
| F06 | Revocation-before-B denies; previously committed B can send late; stale C cannot progress |
| F07 | Observed three-way heartbeat/C/reaper contention, double reaper, expired vs live lease |
| F08 | Process dies after committed C / lost C acknowledgement: one result, settlement and continuation |
| F09 | Slow drip, absolute timeout, delayed B clock anchor, execution-pool saturation, control DB loss, event-loop stall and uncooperative cleanup |
| F10 | Versioned owner resolution, explicit overlap risk, old continuation deselected and exposure retained |
| F11–F13 | M4 tool/report/protocol semantics outside M3; no tool dispatcher or report service added |
| F14–F16 | Simulated accounting/caps/rollover/overrun exercised in M3; real provider evidence and tokenizer remain M5 |
| F17–F22 | Later SSE/retention/restore/delivery scenarios outside M3; M2 isolation and Redis regressions remain enabled |

## Reproduction command

Focused development tests used real PostgreSQL 17.11 and Redis 8.2.2 in the isolated
`atp-m1-test` Compose project. Prefix each test path/selection below with:

```sh
DOCKER_CONTEXT=colima-ai-task-platform docker-compose -p atp-m1-test run --rm --no-deps \
  -e PYTHONPATH=/app/src -v "$PWD/src:/app/src:ro" -v "$PWD/tests:/app/tests:ro" \
  test pytest -p no:cacheprovider
```

The schema RED preceded the migration and needed only the tests mount. Typed interfaces were
introduced where needed to permit collection; their missing behavior, not an import failure,
caused the recorded RED assertions. Initial logs lived in `/private/tmp/m3-*.log`; those
transient logs were lost across desktop resumptions. The following counts were recorded in
this task's tool outputs before implementation, not inferred from final green tests.

## Red → Green → Refactor

| Increment / command suffix | Meaningful RED observed before implementation | GREEN observed |
|---|---|---|
| `tests/integration/test_execution_schema.py -q` | 13 failed: absent call/provision/evidence tables, checkpoint columns/triggers and worker insert boundary | 13 passed |
| `tests/integration/test_execution.py -q` | 6 failed, 1 passed: no claim/heartbeat from typed interfaces | 7 passed |
| `test_execution.py test_execution_calls.py -q` (under `tests/integration/`) | 22 failed, 7 passed: missing B/C/recovery/settlement | 29 passed |
| `tests/integration/test_execution_resolution.py -q` | 5 failed: missing resolve route (404) | 5 passed |
| `tests/integration/test_execution_transport.py -q` | 10 failed, 1 passed: transport returned no outcome/request | 11 passed |
| Review regression selection in `test_execution_calls.py` | 4 failed, 22 deselected: prior settlement prevented matching C and B omitted payload storage charge | 26 passed |
| Initial `tests/integration/test_execution_worker.py -q` | 3 failed: run-once false and readiness 503 | 4 passed, including metric coverage |
| Metrics selection in worker tests | 1 failed, 3 deselected: missing durable metric keys | Included in green worker run |
| CLI selection in worker tests | 1 failed, 4 deselected: preflight claimed execution unimplemented | 5 passed in worker suite |
| C deadline after cost-row lock selection | 1 failed, 26 deselected: expired C still accepted | Included in 39-pass boundary regression |
| Context-overflow and expired-resolution selections | 2 failed, 10 deselected: no explicit context failure / expired suspension not closed | 39 passed across calls/worker/resolution |
| Supervisor and delayed-B deadline selection | 3 failed, 5 deselected: unbounded process and stale monotonic anchor | Final results in verification report |

Harness/setup corrections were explicitly excluded from RED evidence: psycopg cannot decode
PostgreSQL `-infinity` as a datetime (provider default changed to a finite past UTC instant),
and a lock observer originally borrowed a second connection from a one-connection migrator
pool (moved to an independent admin observer). The six other claim assertions remained intact.
No assertion was removed or relaxed to obtain a pass.

Refactoring extracted accounting, resolution, transport, worker supervision and immutable ORM
records from the transaction executor. Targeted checks stayed enabled, and complete regression
is required again after review corrections. Additional adversarial cases test existing behavior
without claiming a new RED for already implemented behavior. Final counts, remaining limitations,
independent review and command outcomes are in [M3 verification](M3-verification.md).

## Final review regressions (2026-10-20)

- `test_execution_worker.py -k requires_execution_write_grants`: **2 failed** before the
  readiness correction. PostgreSQL was reachable and compatible incorrectly remained true
  after revoking call INSERT or provision-column UPDATE. Required worker grants now participate
  in preflight, preserving the existing API privilege checks.
- `test_execution_worker.py -k http_retry_after`: **5 failed, 9 deselected** before the
  transport/executor correction. Durable provider eligibility remained in the past after
  numeric/date Retry-After, a 429, malformed header or oversized delay. Retry-After now sets
  shared durable eligibility; explicit no-effect retry respects it; HTTP 429 alone remains
  unknown with retained exposure. Untrusted delay is bounded to 24 hours; malformed/missing
  429 delay uses a one-second backoff. Task deadlines still prevent authorization.
- The hypothesized downgrade privilege leak was **not reproduced**: the new real-PostgreSQL
  downgrade test passed before any migration change, verifying both denied worker columns and
  preserved API permissions. No production correction was made for that hypothesis.
- The supervision test originally timed eight seconds from process creation. Independent
  instrumentation measured 3.4 seconds of imports/startup before the injected fault, then
  actual exit 70 after 4.95 seconds (blocked loop) / 5.35 seconds (uncooperative cleanup).
  The test now waits at most ten seconds for an explicit `fault-ready` marker, then requires
  termination within six seconds (five-second grace plus one-second scheduling margin).
  This tests the specified fault-to-stop bound, independently of bounded startup. Physical
  request/effect assertions and the production five-second watchdog were preserved.

The final independent review also found a cooldown idempotence regression introduced by the
Retry-After correction. `test_execution_calls.py -k replayed_c_does_not` first produced
**1 failed, 32 deselected**, then **2 failed, 32 deselected** after adding the recovered-call
variant. Both failures showed `next_allowed_at` increasing on a repeated C. A guarded durable
`response_observed_at` now records the first response under the call lock, including a first
late response after reaping. Replays retain the original observation. After the migration was
round-tripped on the disposable database, the same command produced **2 passed, 32 deselected**.

The combined worker/fault/migration regression before this last correction produced
**28 passed, 0 failed, 0 errors, 0 skipped in 54.00 seconds**, including both supervision cases,
all five Retry-After HTTP cases and both revoked-write readiness cases. Complete regression
and independent acceptance are rerun after the final correction.
