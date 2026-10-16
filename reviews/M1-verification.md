# M1 foundation verification

Initial verification: 2026-10-15. Final local and GitHub verification: 2026-10-16. Scope: M1 only on `feature/m1-foundation`. Architecture, prior review and AGENTS.md preserved. Repository: private `WYLLLY/ai-task-execution-platform`. No merge or M2 work is authorized or performed.

**Local verification: PASS. Remote CI: PASS on application/test commit `e3f6bfd41b44c4e2054a94fc949c2f1c637b3726`.**

## Delivered behavior

- Typed FastAPI identity API; current PostgreSQL authentication, revocable API-key digests, owner-scoped metadata and generic errors.
- Administrative principal/key provisioning, revocation and disable commands; atomic audit and permission-version changes; consistent principal-before-key locking.
- Dedicated SQL migration role, restricted API/worker/admin roles; four M1 tables plus Alembic revision, explicit constraints/grants, no task/workflow/provider tables.
- Operational epoch and closed submission/dispatch flags; DB/schema/role/capacity/epoch readiness, live health independent of dependencies, visible Redis degradation without removing identity reads.
- Bounded pools/timeouts, allowlisted JSON logs, role-specific file secrets, non-root/resource-limited containers and private backend networking. Shared image exposes API/admin and worker compatibility preflight only.
- Locked Python dependencies, immutable base images and CI action commits, compatibility/pool manifest, developer instructions and real integration/smoke automation.

## Environment

Local host: macOS arm64; isolated Colima profile/context `ai-task-platform` / `colima-ai-task-platform`, 2 vCPU, 4 GiB RAM, 20 GiB disk, Docker 29.5.2, Compose 5.6.0 (standalone binary; classic builder because buildx is absent). Application tests run on Linux arm64 containers. No other project's database is used.

Python 3.14.8; uv 0.13.1; FastAPI 0.143.1; Pydantic 2.14.1; SQLAlchemy 2.0.54; psycopg 3.3.6; Alembic 1.20.0; HTTPX 0.28.1; Redis client 8.1.0; PostgreSQL 17.11; Redis server 8.2.2; pytest 9.1.1; Ruff 0.17.0; mypy 2.4.0. Full resolved dependencies/hashes are in `uv.lock`, image digests in Dockerfile/Compose.

Configured DB capacity: 50 connections. Budget: API 8+2, two future workers 2×(4+2), admin 2, migration 1, probes 3 and operator/PostgreSQL reserve 10 = 38. Workers do not execute tasks in M1. Containers: API/admin/migration/worker 0.5 CPU/256 MiB, PostgreSQL 1 CPU/512 MiB, Redis 0.25 CPU/128 MiB; disposable test runner 1 CPU/512 MiB.

## Commands and results

The local uv executable is `/private/tmp/ai-task-uv/bin/uv`. `UV_CACHE_DIR=/private/tmp/ai-task-uv-cache` avoids a sandbox-restricted cache. Docker commands use the explicitly selected isolated context and standalone Compose binary.

| Exact command | Status | Evidence |
|---|---|---|
| `UV_CACHE_DIR=/private/tmp/ai-task-uv-cache make UV=/private/tmp/ai-task-uv/bin/uv lint typecheck test-unit` | PASS | Ruff lint/format; strict mypy (22 sources); 10 passed, 0 failed/errors/skipped |
| `DOCKER_CONTEXT=colima-ai-task-platform COMPOSE=docker-compose make UV=/private/tmp/ai-task-uv/bin/uv test-integration` | PASS | Builds/starts real services; 48 passed, 0 failed/errors/skipped after correction |
| `DOCKER_CONTEXT=colima-ai-task-platform COMPOSE=docker-compose make UV=/private/tmp/ai-task-uv/bin/uv smoke-compose` | PASS | Real HTTP, provisioning/revocation, actual Redis stop/start, current identity reads, redaction, private ports, non-root/capabilities/resources, mounted secrets and worker preflight |
| `actionlint .github/workflows/ci.yml` | PASS | CI syntax and expressions validated |
| `git diff --check` | PASS for M1 changes | Initial source review's Markdown hard-break spaces were retained unchanged in the baseline commit |

Total pytest coverage: **58 passed, 0 failed, 0 errors, 0 skipped** across the unit/integration suites. Smoke is a separate command, not included in the pytest count.

## Gate coverage

- Empty database: fresh Compose volume initialized PostgreSQL roles; Alembic ran under `atp_migrator`; administrative epoch initialization preceded API readiness.
- Migrations: explicit downgrade to base, upgrade to head and repeated upgrade on the disposable database; missing schema/epoch fail readiness; initialization restores readiness. Migration under the API role is rejected.
- SQL restrictions: 24 negative role-operation cases plus API/worker mutation denials, allowed reads and exact table/role inspection. DDL, temporary/schema creation, SET ROLE escalation, credential catalog reads, audit modification and schema revision modification denied to runtime roles. Structural constraints and transactional rollback tested negatively.
- Identity: owner A cannot read/revoke owner B's key; absent and foreign metadata return identical 404. Revoked/expired/disabled keys fail authentication; expiry uses DB time. CLI provisioning output is intentionally one-time, distinct from logs.
- Contention: a separate PostgreSQL connection observes `pg_blocking_pids` before releasing the principal lock; repeated revoke produces one audit record and one version increment. This is not a claim about the future executor.
- Secrets/errors: auth/path canaries are not echoed; JSON formatter drops messages, exception strings and arbitrary extras; smoke scans real API logs for request canaries, the issued API key and generated role passwords. Reviewer independently tested four CLI configuration/DB error scenarios.
- Redis: real Redis PING and actual stop/start in Compose; readiness stays 200 with Redis false and identity reads remain available. No M2 task admission endpoint exists, so submission/dispatch are always closed and explicitly documented.

## Failures corrected and limits

Initial attempts exposed environment/setup issues: no Git repository, uv absent, stopped Docker, sandbox-restricted network/cache and a standalone Compose binary. A new private repository and isolated Docker profile were authorized/prepared. No credentials were copied from another project. GitHub authentication was verified outside the network sandbox.

Implementation/test corrections: type/format errors; asynchronous fixture context and migration subprocess handling; quoted tmpfs option in YAML; separate API ingress network because an internal-only network did not publish the host port; preservation of existing `.env` identified by independent review. Required checks were rerun; none was weakened to hide a failure. Narrow static-lint annotations identify fixed container binding, trusted test/CLI commands, static SQL identifiers and external PostgreSQL wait observation; strict mypy remains enabled.

Initial independent review: [APPROVE, one Low finding fixed, none unresolved](M1-independent-review.md). The subsequent [independent PR review](M1-review.md) concludes **APPROVE**, with no confirmed findings. That reviewer independently reran all 58 tests, static checks, empty-database startup, migrations, Compose smoke and GitHub CI verification, then executed six additional adversarial scenarios. Those probes cover issuance/disable contention, concurrent revocation, pool isolation, expanded SQL restrictions and secret-safe errors. Their reproduction instructions and limitations are in the review report; they are not committed regression tests.

**SKIPPED / deferred:** F01–F22 execution/admission/provider/SSE/recovery/load scenarios belong to M2–M7. Production TLS, backup/restore and release/scan campaigns are later delivery work. No paid OpenAI smoke test or real model capability is implemented or claimed. No application security scan or benchmark beyond the specified M1 checks is claimed. CI has no live provider credentials; development secrets are ignored and excluded from the image context.

## GitHub evidence and final recheck

The implementation was committed as `1bc4f48` (foundation/identity) and `e3f6bfd` (tests/CI/documentation), pushed to `feature/m1-foundation`, and opened as [PR #1](https://github.com/WYLLLY/ai-task-execution-platform/pull/1) targeting `main`. Repository visibility was verified as private.

Both the [pull-request run](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/38858012159) and [push run](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/38858003514) completed successfully on the exact code/test SHA above. `gh pr checks 1 --repo WYLLLY/ai-task-execution-platform` reports two passing `verify` checks. CI logs independently show Ruff PASS, strict mypy PASS (22 sources), **10 unit tests passed**, **48 integration tests passed**, and **Compose smoke PASS**. No test failures, errors or skips; no live credentials or paid calls.

On 2026-10-16 the complete local static/unit/integration/smoke commands above were rerun successfully after restarting the isolated Colima profile and recreating the temporary pinned uv installation. A tracked-file comparison against the generated local role passwords/database URLs found no leaked values. The full branch diff passes `git diff main...HEAD --check`. No application code changed during this finalization or the subsequent PR audit. Later documentation-only commits are checked by the same CI workflow; consult the current PR checks for their exact head.
