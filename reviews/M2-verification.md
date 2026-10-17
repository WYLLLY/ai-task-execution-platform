# M2 implementation and verification

Date: 2026-10-17. Scope: approved M2, plus requested M1 review follow-ups. PostgreSQL is the sole durable source of task state, command responses, admission/storage counters and events. No M3 executor or provider calls were introduced. The user-owned AGENTS.md change is preserved outside these implementation commits.

## Delivered behavior

- Immutable versioned catalogue and pinned validated task inputs/limits; authenticated owner-scoped task/step pagination and snapshots.
- Versioned bounded JSON canonicalization; exact retained202/200 responses scoped by owner/route/key; replay precedes mutable quota, epoch, workflow and Redis checks.
- Serialized owner/global slot and storage admission, with task/initial step/event/counters/response committed atomically.
- Expected-version cancellation, fresh database time after all locks, deadline precedence, child/attempt closure and exactly-once counter release. New commands cannot mutate terminal tasks.
- Migration0002 adds six tables, owner FKs, partial unique indexes, legal-arc/terminal guards, deferred coupling checks and minimal API grants. Administrative availability controls append audit records.
- Real Redis creation limiting, bounded local read/control fallback, privileged schema readiness; no provider credential in API.
- M1 follow-ups: exclusive secret environment creation, permanent adversarial tests, SHA-pinned Node24 CI actions.

## Observed runtime checks

Environment: Python3.14.8, uv0.13.1, PostgreSQL17.11, Redis8.2.2; dependency versions in uv.lock, image/action digests in Compose/CI. Local Docker uses the isolated `colima-ai-task-platform` context and disposable `atp-m1-test` project. API remains one process with the approved38/50 connection budget.

| Check | Result |
|---|---|
| `make lint` | PASS: Ruff lint and format,50 files |
| `make typecheck` | PASS: strict mypy,39 source files |
| `make test-unit` | PASS:25 passed,0 failed/errors/skipped |
| Complete real PostgreSQL integration after corrections | PASS:278 passed,0 failed/errors/skipped |
| Migrations | PASS: base→head, repeated upgrade, M1→M2 identity/audit/epoch preservation, wrong-role denial |
| SQL permissions/constraints and observed lock contention | PASS: included in complete integration |
| `make smoke-compose` | PASS: physical HTTP response loss, exact replay, PostgreSQL/API restart, real Redis outage/recovery, cancellation/reads/replay during outage, runtime isolation and secret/log checks |
| `actionlint` and `git diff --check` | PASS |
| Independent final run / GitHub CI | Recorded separately below after observation |
| Paid API calls / M3–M7 execution gates | SKIPPED: outside implemented/authorized scope; no provider credentials used |

Reproducible commands (standalone Compose on this host):

```sh
UV_CACHE_DIR=/private/tmp/ai-task-uv-cache make UV=/private/tmp/ai-task-uv/bin/uv lint typecheck test-unit
DOCKER_CONTEXT=colima-ai-task-platform COMPOSE=docker-compose make UV=/private/tmp/ai-task-uv/bin/uv test-integration
DOCKER_CONTEXT=colima-ai-task-platform COMPOSE=docker-compose make test-commands test-concurrency
DOCKER_CONTEXT=colima-ai-task-platform COMPOSE=docker-compose UV_CACHE_DIR=/private/tmp/ai-task-uv-cache make UV=/private/tmp/ai-task-uv/bin/uv smoke-compose
actionlint
git diff --check
```

The author's final278-test run used the rebuilt application image and a read-only test-directory mount after correcting matrix fixture SQL casts/key selection. The reviewer rebuilds the final test image and reruns the repository commands without that override. Setup failures and stale-image runs are recorded in the test matrix; none is presented as a product failure or valid TDD Red.

## Review corrections and process limitation

See [test matrix](M2-test-matrix.md) for recorded Red→Green evidence and all initial failures. Confirmed defects were tested before their corrections: secret-file race, false-positive identity/write readiness, NUL JSON acceptance, deadline sampling before child locks and unretained fresh-key terminal cancellation. Expanded audit tests add transaction/concurrency/transport/state/limiter evidence.

**Runtime verification PASS does not certify universal TDD chronology.** Initial M2 and regression Red evidence exists, but some per-behavior audit tests were added after implementation and historical M1 chronology is unavailable. Under the current verification Skill the strict process gate is **FAIL / unverified** for those portions. This cannot be repaired by inventing a retrospective Red run; M2 is not claimed unconditionally complete under that gate.

## Remaining limits

Tasks can be queued/cancelled but no worker executes them. Deadlines are evaluated by cancellation in M2; automatic maintenance, leases, external-call state/accounting and settlement belong to M3. Stored ceilings are not evidence of provider-budget enforcement. Corpus/document rights, tools and semantic report validation belong to M4; SSE, retention and restoration are later milestones. Redis loss does not demonstrate future worker polling because no executor exists yet. No throughput or production-readiness claim is made. M1 and M2 PRs must remain unmerged until explicitly authorized.
