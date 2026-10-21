# M3 independent reliability and security review

Review dates: 2026-10-20–21. Scope: the M3 working-tree changes on `feature/m3`, based on
`9cc6627`; M1/M2 remain the baseline. The user-owned existing AGENTS.md
modification was excluded. Review performed by separate reviewer agents using
`adversarial-review`, `execution-reliability-review` and `agent-security-review`.
No production code was edited by the final reviewer.

## Scope and mechanisms inspected

- The approved M3 gate, architecture §§2–4, accepted review tradeoffs, new migration,
  roles, ORM records, transaction services, resolver, transport, worker and supervisor.
- A locks task/step and creates durable attempt ownership; heartbeat checks holder,
  generation, epoch, active state and fresh database time using a separate pool.
- B serializes platform/owner/provider admission before task state, pins exact payload
  bytes and permission versions, and reserves integer micro-USD/tokens atomically.
  Re-reading an existing authorization does not issue a fresh send permit.
- C checks selected ownership after its last blocking lock, settles evidence by identity
  and revision, and commits result/events together. Terminal results remain immutable;
  late evidence can settle a call without restarting its task.
- Recovery closes active attempts, distinguishes pre-B retry from post-B ambiguity,
  preserves exposure and requires explicit owner/version/risk resolution for regeneration.
- Fixed HTTP destination, disabled implicit proxy/redirect/retry behavior, bounded bytes,
  strict outcome schemas and absolute monotonic timeouts. No shell, model tools, corpus,
  provider credentials or report workflow were introduced by M3.
- Role-separated readiness/grants, owner-scoped resolution and replay, quarantined-task
  denial, immutable external identities/evidence, shared capacity and cross-day exposure.
- Fault tests use independent HTTP request/effect journals and real process death. Lock
  tests observe PostgreSQL contention, rather than treating a launch barrier as proof.

## Findings and disposition

Earlier independent reviews found matching prior settlement preventing C, missing payload
storage charging at B, stale deadline checking after cost-lock waits, delayed B clock anchoring,
and lack of independent termination for a blocked/uncooperative worker. The author added
behavioral regressions before correcting those paths; the TDD record identifies observed RED
counts and excludes setup failures. Final review inspects the corrected implementations.

The final review additionally investigated:

1. **Medium — missing durable Retry-After.** Explicit simulated no-effect rejection could
   schedule a retry before the server-supplied delay, and HTTP 429 did not update shared
   eligibility. The author reproduced five failing HTTP→database cases before implementing
   numeric/date parsing, bounded delay and durable shared eligibility. HTTP status alone
   continues to produce `unknown`, with retained exposure.
2. **Medium — worker preflight ignored required writes.** A readable schema could report
   compatibility after call INSERT/provision UPDATE was revoked. Two real-PostgreSQL RED
   cases preceded required worker table/column privilege checks.
3. **Medium — repeated C could prolong cooldown.** The first Retry-After correction computed
   `now + delay` again on duplicate completion. Two regression cases (ordinary and already
   recovered call) failed before correction. A durable, write-once `response_observed_at`
   now anchors the first observation under the call lock, so repeated C cannot extend it;
   first late responses after recovery remain supported. The worker-only column grant and
   SQL immutability guard were inspected independently.
4. **Investigated, cleared — downgrade privileges.** The proposed column-grant leakage was
   not reproduced: a real-PostgreSQL downgrade test passed before any production correction,
   checking forbidden worker mutations and preserved API permissions. It is not a finding.
5. **Test harness correction — supervision timing.** Previous independent instrumentation
   measured about 3.4 seconds of process/import startup before fault injection on the
   constrained container, followed by exit 70 after 4.95/5.35 seconds. The acceptance test
   now separately bounds startup and fault-to-exit. Its physical invocation/effect checks and
   five-second production watchdog remain intact.

## Independent execution

Final reviewer execution on 2026-10-21 used Python 3.14.8 in the isolated
`atp-m1-test` Compose test image, real PostgreSQL 17.11/Redis 8.2.2, and current source,
tests and migrations mounted read-only. No application database test ran concurrently
with the review. The supervisor timing subset ran before the image rebuild.

Command prefix:

```sh
DOCKER_CONTEXT=colima-ai-task-platform docker-compose -p atp-m1-test run --rm --no-deps \
  -e PYTHONPATH=/app/src -v "$PWD/src:/app/src:ro" -v "$PWD/tests:/app/tests:ro" \
  -v "$PWD/migrations:/app/migrations:ro" test pytest -p no:cacheprovider
```

| Independent command suffix / check | Result |
|---|---|
| `tests/integration/test_execution_failures.py -k supervisor -q` | **PASS: 2 passed, 9 deselected, 0 failed/errors/skipped; 17.56 s** |
| All seven `test_execution*.py` files plus `test_z_migrations.py` and `test_database.py`, `-k 'not supervisor' -q` | **PASS: 129 passed, 2 deselected, 0 failed/errors/skipped; 45.90 s** |
| `.venv/bin/ruff check .` | **PASS** |
| `.venv/bin/ruff format --check .` | **PASS: 77 files** |
| `.venv/bin/mypy` | **PASS: 61 source files** |
| `git diff --check` | **PASS** |

The two deselected supervisor tests in the larger run are the separately executed timing
subset above, not skipped acceptance tests. Together these runs independently verify **131
cases**: A/B/C ownership, budgets and evidence, reapers and contention, resolution, transport
faults/physical journals, supervisor, CLI/readiness, actual process crashes, migration round-trips
and database role boundaries. The existing exact-table test was updated only to include the
four planned M3 tables; strict equality and every role-escalation assertion remain intact.

**Verdict: PASS for the reviewed M3 implementation.** Confirmed findings are resolved with
behavioral regressions; no unresolved significant correctness/reliability/security finding was
identified in the final scope. Full M1–M3 regression, Compose smoke and final-SHA CI remain the
primary author's delivery checks and are recorded separately in `M3-verification.md`.
This independent review is not a GitHub approval or authorization to merge.

## Boundaries

This review covers the simulated M3 contract, not live provider billing, model behavior,
truthfulness, document tools, the demo report service, SSE, retention or restore workflows.
A previously committed B can still cause a first delayed physical send after local revocation
or cancellation; fencing rejects stale local progression but is not remote cancellation.
Simulation evidence is not production readiness or a promise of exactly-once remote execution.
