# AI Task Execution Platform

A Python/FastAPI backend with PostgreSQL as its durable source of truth. **M1: foundation and identity** is the only implemented milestone. The [approved plan](docs/architecture-plan.md) governs later development; its historical validation header is preserved.

M1 provides API-key authentication, owner-scoped key metadata, administrative provisioning/revocation, operational epoch, bounded database pools, JSON logs, Alembic migrations and isolated Docker Compose services. No task queue, workflow, provider call, SSE or report service is implemented. All verification uses synthetic identities and local services, with no paid requests.

## Run locally

Install Docker Compose and uv **0.13.1**. The Python runtime is pinned in `.python-version`; dependencies and transitives are locked in `uv.lock`. Images and CI actions are pinned to immutable digests/SHAs.

```sh
uv sync --frozen
make dev-secrets
docker compose up --build --wait api
curl http://127.0.0.1:8000/health/ready
```

`make dev-secrets` creates ignored `.secrets/` and `.env`, refuses to overwrite existing configuration and never prints credentials. These files are local/CI fixtures; production secrets must be supplied by the operator. Keep the host secret directory private. Compose mounts only each role's credential files. PostgreSQL and Redis expose no host ports; API binds to host loopback. Production TLS/reverse proxy and deployment hardening are M7 delivery work; this local stack is not a production deployment.

The one-shot migration uses `atp_migrator`; initialization uses `atp_admin`. API/worker roles cannot create schema objects, provision identities, alter the epoch, modify audit records or inherit administrative roles. The worker command is a compatibility preflight and exits; the durable executor belongs to M3.

```sh
docker compose run --rm admin admin principal-create --name 'Demo owner'
docker compose run --rm admin admin key-create --principal OWNER_UUID
# Securely capture the one-time JSON api_key output; it is never persisted in plaintext.
docker compose run --rm admin admin key-revoke --principal OWNER_UUID --key KEY_UUID
docker compose run --rm admin admin principal-disable --principal OWNER_UUID
docker compose run --rm worker
```

The CLI is the trusted administrative boundary, using a separate DB credential. A key contains 256 random bits; only its SHA-256 digest is stored. Authentication reads current key/principal state and expiry from PostgreSQL on every request. A concurrent request whose authentication completed before revocation can finish; later authentication fails. Permission changes increment versions and append audit entries in the same transaction. Mutations lock principal before key. No blind DB retry is implemented.

## HTTP contract

- `GET /health/live`: process liveness, independent of dependencies.
- `GET /health/ready` and `GET /v1/system`: DB/schema/role/capacity/epoch readiness, Redis availability and operation flags. A Redis outage keeps HTTP 200 readiness and reports `redis=false`; PostgreSQL, revision or epoch failure yields 503. M1 always reports `submission_available=false` and `dispatch_available=false` because execution is not implemented.
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
make smoke-compose
docker compose -p atp-m1-test down --volumes --remove-orphans
```

Integration tests use a **disposable** `atp-m1-test` Compose project and real PostgreSQL/Redis. They include destructive migration round-trips; never point them at a populated development or production database. No silent skips are used for missing infrastructure. Local demo and test projects share the default API host port: stop one before starting the other, or set `API_PORT`.

On hosts with the standalone Compose binary, set `COMPOSE=docker-compose`; set `DOCKER_CONTEXT` explicitly when using Colima. `UV=/path/to/uv` is supported. `make smoke-compose` expects the test stack from `make test-integration` and verifies an actual Redis stop/start, authenticated reads, revocation, container limits, private networking, secret mounts and worker preflight.

The [compatibility manifest](docs/compatibility.json) records schema/runtime support and the 38-connection budget within PostgreSQL's configured 50. Production application pools have no overflow; pool acquisition, statements and lock waits are bounded. API control uses a separate pool. Read/SSE load and durable worker failure matrices F01–F22 require later milestones and are not claimed by M1.

See [M1 verification](reviews/M1-verification.md) and [independent review](reviews/M1-independent-review.md) for observed results, commands and limits.
