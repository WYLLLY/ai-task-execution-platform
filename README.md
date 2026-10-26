# AI Task Execution Platform

A Python/FastAPI backend with PostgreSQL as its durable source of truth. **M1 foundation/identity, M2 commands/states and M3 simulated durable execution** are implemented. The [approved plan](docs/architecture-plan.md) governs later development; its historical validation header is preserved.

M1 provides API-key authentication, owner-scoped key metadata, administrative provisioning/revocation, operational epoch, bounded database pools, JSON logs, Alembic migrations and isolated Docker Compose services. M2 adds a PostgreSQL task queue, versioned catalogue, serialized admission, retained command responses and atomic cancellation. M3 adds lease/fencing, atomic A/B/C execution, simulated money/token reservations, recovery, owner resolution and a supervised worker. Live provider calls, the analysis workflow, SSE and the report service remain later milestones. All verification uses synthetic identities and local services, with no paid requests.

## Run locally

Install Docker Compose and uv **0.13.1**. The Python runtime is pinned in `.python-version`; dependencies and transitives are locked in `uv.lock`. Images and CI actions are pinned to immutable digests/SHAs.

```sh
uv sync --frozen
make dev-secrets
docker compose up --build --wait api
curl http://127.0.0.1:8000/health/ready
```

`make dev-secrets` creates ignored `.secrets/` and `.env`, refuses to overwrite existing configuration and never prints credentials. These files are local/CI fixtures; production secrets must be supplied by the operator. Keep the host secret directory private. Compose mounts only each role's credential files. PostgreSQL and Redis expose no host ports; API binds to host loopback. Production TLS/reverse proxy and deployment hardening are M7 delivery work; this local stack is not a production deployment.

The one-shot migration uses `atp_migrator`; initialization uses `atp_admin`. API/worker roles cannot create schema objects, provision identities, alter the epoch, modify audit records or inherit administrative roles. The default worker command performs compatibility preflight and exits. M3 execution requires an explicit isolated simulation profile; it never presents synthetic completion as an analysis report.

```sh
docker compose run --rm admin admin principal-create --name 'Demo owner'
docker compose run --rm admin admin key-create --principal OWNER_UUID
# Securely capture the one-time JSON api_key output; it is never persisted in plaintext.
docker compose run --rm admin admin key-revoke --principal OWNER_UUID --key KEY_UUID
docker compose run --rm admin admin principal-disable --principal OWNER_UUID
docker compose run --rm worker
```

The CLI is the trusted administrative boundary, using a separate DB credential. A key contains 256 random bits; only its SHA-256 digest is stored. Authentication reads current key/principal state and expiry from PostgreSQL on every request. A concurrent request whose authentication completed before revocation can finish; later authentication fails. Permission changes increment versions and append audit entries in the same transaction. Mutations lock principal before key. Command transactions retry only confirmed PostgreSQL serialization/deadlock failures, at most three attempts. Unknown commit acknowledgements trigger a stable command lookup, never a blind mutation retry.

## HTTP contract

- `GET /health/live`: process liveness, independent of dependencies.
- `GET /health/ready` and `GET /v1/system`: DB/schema/role/capacity/epoch readiness, Redis availability and operation flags. A Redis outage keeps HTTP 200 readiness and reports `redis=false`; PostgreSQL, revision or epoch failure yields 503. Submission is closed by default. It becomes available after explicit administrative admission opening when required read/write privileges, schema, epoch and Redis checks pass. `dispatch_available=false` for the API deployment: simulation execution is an explicitly enabled test worker capability.
- `GET /v1/me`: authenticated owner/key IDs.
- `GET /v1/api-keys/{key_id}`: metadata for the authenticated owner's key; another owner's identifier and a missing key both return 404. No secret/hash is returned.

Bearer credentials belong in headers, never URLs. Responses disable caching. Validation and DB errors are generic; logs use an allowlist of structured events, numeric status and timestamp. Exception text, arbitrary extras, URL/query/header/body and SQL parameters are omitted. This sacrifices detailed diagnostics deliberately at M1; richer bounded error classification can be added with its own disclosure tests.

## Verification

```sh
make lint
make typecheck
make test-unit
make dev-secrets                 # once per checkout
make test-integration
make test-commands
make test-concurrency
make test-execution
make test-failures
make smoke-compose
docker compose -p atp-m1-test down --volumes --remove-orphans
```

Integration tests use a **disposable** `atp-m1-test` Compose project and real PostgreSQL/Redis. They include destructive migration round-trips; never point them at a populated development or production database. No silent skips are used for missing infrastructure. Local demo and test projects share the default API host port: stop one before starting the other, or set `API_PORT`.

On hosts with the standalone Compose binary, set `COMPOSE=docker-compose`; set `DOCKER_CONTEXT` explicitly when using Colima. `UV=/path/to/uv` is supported. `make smoke-compose` expects the test stack from `make test-integration` and verifies a physically sent HTTP request whose response is discarded, exact replay across PostgreSQL/API restart, real Redis stop/start, continued reads/replays/cancellation plus PostgreSQL claim/heartbeat during outage, admission recovery, revocation, container limits, private networking, secret mounts and worker preflight.

The [compatibility manifest](docs/compatibility.json) records schema/runtime support and the 38-connection budget within PostgreSQL's configured 50. Production application pools have no overflow; pool acquisition, statements and lock waits are bounded. API control uses a separate pool. M2 covers admission/state gates F01–F03. M3 covers F04–F10 with simulated external calls, real PostgreSQL and an independent physical HTTP/effect journal; later provider, tools, SSE and recovery gates retain their milestone boundaries.

See [M3 test matrix and TDD record](reviews/M3-test-matrix.md), [M3 verification](reviews/M3-verification.md), [M3 independent review](reviews/M3-review.md), [M2 test matrix](reviews/M2-test-matrix.md), [M2 verification](reviews/M2-verification.md), [M2 independent review](reviews/M2-review.md), [M1 verification](reviews/M1-verification.md), the [independent PR review](reviews/M1-review.md) and the [initial implementation review](reviews/M1-independent-review.md) for observed results, commands and limits.

## M2 task commands

Open admission explicitly with `docker compose run --rm admin admin admission --enabled true`.
This accepts durable queued work; execution requires an explicitly enabled worker. Close with `--enabled false`.
`admin workflow-availability --name analysis --version 1 --enabled false` withdraws new admission
without invalidating retained command responses. Both administrative actions are audited.

- `GET /v1/workflows`: available versioned catalogue and accepted input schema.
- `POST /v1/tasks`: Bearer authentication, ASCII `Idempotency-Key` (1–128 characters),
  `X-Execution-Epoch` from `/v1/system`, JSON body below; returns retained `202` after commit.
- `GET /v1/tasks` and `GET /v1/tasks/{id}/steps`: owner-scoped cursor pagination, limit 1–100.
- `GET /v1/tasks/{id}`: consistent snapshot with state, version and event watermark.
- `POST /v1/tasks/{id}/cancel`: idempotency key and `{"expected_task_version":1}`;
  atomically closes executable children/active attempts and decrements admission counters.
  A deadline crossed before final lock acquisition produces `timed_out`. A fresh command on
  a terminal task conflicts (`409`); the original successful command still replays exactly.

```json
{"workflow":{"name":"analysis","version":"1"},"input":{"texts":["Synthetic dossier"]}}
```

Optional `limits` only lower server ceilings: deadline 900 seconds, 8 model calls, 8 tool calls,
40,000 tokens, 1,000,000 micro-USD. The validated values and all definition versions are pinned.
M3 enforces external-call budgets using an explicitly fictitious byte tokenizer and integer micro-USD tariff. No document corpus is accepted
before M4. Request bodies are bounded to 1 MiB, 20 texts, depth 32 and a five-second read deadline.
Duplicate JSON keys, nonfinite/oversized numbers, NUL and malformed Unicode are rejected.
Canonicalization v1 preserves omitted versus explicit null and normalizes object order/numbers.

New tasks consume one of five default owner slots / 100 global slots and a conservative retained
storage charge (canonical effective request plus 8 KiB control reserve). Cancellation releases the
slot but retains storage for responses/events. Retention and quota administration are future scope;
operators can close admission now. PostgreSQL serializes competing admissions across owners.
Redis provides only the 60/minute/key creation limiter. Its loss rejects fresh creations with `503`
while authenticated replay, reads and cancellation continue. Bounded per-process read/control
limits (120/minute/key and 30/minute/owner) assume the single API process in the manifest.


## M3 simulated execution

`atp worker --check` verifies compatibility and prints aggregate queue, lease and exposure
metrics. `atp worker --simulate [--once]` requires `ATP_TEST_PROFILE=1`,
`DISPATCH_ENABLED=true`, the worker DB credential and a fixed `SIMULATOR_URL` whose host is
`simulator` or `127.0.0.1`. PostgreSQL dispatch admission must also be open. This mode is
for disposable synthetic tests only. The test image includes the independent HTTP simulator;
the application image contains no fault server. `make test-execution` and `make test-failures`
exercise this mode without provisioning a live provider or enabling public dispatch.

The executor commits A (claim/lease), B (exact payload, authorization, provision and capacity)
and C (accepted result, settlement and terminal state) separately, releasing connections before
HTTP. Each worker has four execution slots and a separate control pool. PostgreSQL polling
continues without Redis; heartbeat runs every ten seconds against a sixty-second lease.
An independent process watchdog stops a blocked or uncooperative worker. Local cancellation
and process termination do not prove that a remote operation stopped.

An ambiguous call retains its money/token exposure and suspends the task. No automatic
regeneration occurs. The owner can submit `POST /v1/tasks/{id}/resolve` with an idempotency key:

```json
{"expected_task_version":4,"action":"regenerate","accept_overlap_cost_risk":true}
```

Use `resume` for a suspension before B after execution conditions become valid again.
Resolution rechecks rights, epoch, deadline and capacity; it cannot resolve restore quarantine
or erase unknown exposure. Regeneration deselects the old continuation immediately, and a
new B reserves separately. Matching commands replay; changed bodies or stale versions conflict.
Late usage evidence can update accounting after cancellation, but never restart a terminal task.

`0003_execution` adds calls, cost provisions/evidence and shared provider admission, plus
step eligibility/checkpoints and attempt ownership metadata. Money uses integer micro-USD;
confirmed daily usage is attributed to B's UTC day, while all unresolved exposure remains in
admission across rollover. Overrun blocks further authorization pending future administrative
reconciliation. Production provider tariffs, Responses protocol, corpus tools and report semantics
are not inferred from this simulation.
