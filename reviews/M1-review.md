# M1 independent PR review

Date: 2026-10-16. Reviewer: independent Codex subagent `m1_final_review`, separate from the implementation author. Scope: M1 foundation and identity only, [PR #1](https://github.com/WYLLLY/ai-task-execution-platform/pull/1), branch `feature/m1-foundation`, application/test revision `e3f6bfd41b44c4e2054a94fc949c2f1c637b3726`.

**Verdict: APPROVE for M1. Verification: PASS. No confirmed Critical, High, Medium or Low findings remain in this review.**

This verdict is based on source inspection and independently executed checks, including real PostgreSQL/Redis, a fresh database volume, migrations, Docker runtime inspection, concurrency probes and GitHub CI. It does not approve a merge, M2 implementation or production deployment. No application, migration, test, configuration or architectural-plan file was modified. Only this report was written; the pre-existing modification to `reviews/M1-verification.md` belongs to the coordinating author.

## Scope and method

Read the complete approved architecture plan, repository `AGENTS.md`, architecture-review response and the `adversarial-review`, `verification`, `execution-reliability-review` and `agent-security-review` skills. The plan's historical validation header was preserved. The earlier independent M1 report was consulted after inspecting the current implementation; its previously corrected environment-overwrite issue is covered by the current unit tests and was not counted as a new finding.

Inspected all M1 application modules, SQL bootstrap/migration, SQLAlchemy models and pools, Dockerfile/Compose, secret-generation and smoke scripts, dependency/runtime pins, compatibility manifest, README, complete unit/integration suite and CI workflow. Compared the full branch scope against `main` and checked the actual HEAD/PR SHA. Source behavior was assessed against the M1 gate, without imposing later task/executor/tool requirements on the foundation.

The implemented trust boundaries are API caller to identity services, administrator CLI to privileged identity mutations, runtime roles to PostgreSQL, role-specific secret mounts, and process errors to public responses/logs. M1 has no model, corpus, agent dispatch, task state machine, remote report operation, SSE or cost-accounting execution.

## Findings and protective mechanisms

No concrete unresolved M1 defect was reproduced. The following mechanisms were inspected and exercised:

- API secrets contain 256 random bits; only a SHA-256 digest is stored. PostgreSQL authentication checks the current key revocation/expiry and principal status. Malformed credentials fail before a database lookup. Key metadata includes an owner predicate; foreign and absent key IDs return the same `404` response, without the digest or raw secret.
- Identity mutations use complete service-owned transactions. Principal locks precede key locks, permission versions and audit rows commit together, and repeated revocation produces a single version increment/audit event. Independent blocked-connection probes establish actual PostgreSQL lock contention, rather than relying on an asyncio scheduling barrier.
- PostgreSQL roles are distinct and have no superuser, role-creation, database-creation or administrative membership. API/worker credentials cannot provision owners, modify epoch, mutate audit/schema or escalate to migration/admin access. The administrator can provision identities but cannot rewrite/delete audit. Migration explicitly checks the dedicated role.
- SQL enforces the singleton control row, positive permission versions, nonempty bounded principal names, valid unique key digests, owner references and expiry ordering. A failed key insertion rolls back its audit row. Fresh bootstrap and destructive upgrade/downgrade/upgrade testing use only the disposable test database.
- PostgreSQL/schema revision/role/capacity/epoch determine readiness; Redis failure is exposed separately and preserves authenticated identity reads. M1 truthfully advertises submission and dispatch as unavailable. The worker command performs only a compatibility preflight with no handlers/execution loop.
- Independent bounded SQL pools have no overflow. Saturating all eight API read connections still permits a query through the separate control pool; the ninth read checkout times out at the configured two-second boundary.
- HTTP errors and validation failures are generic, responses disable caching, and JSON logging omits diagnostic text, URLs, headers, SQL parameters and tracebacks. An injected exception containing a synthetic canary and an invalid administrative UUID did not expose it. The deliberate loss of detailed diagnostics is documented, not claimed as full observability.
- Running API/PostgreSQL/Redis containers are non-root, drop capabilities, enable no-new-privileges and have CPU/memory bounds. Database/Redis ports are private. API root filesystem is read-only, ingress is host-loopback only, and mounts contain only API DB/Redis credentials. No OpenAI credential or provider integration is present. `.dockerignore` excludes local credentials and `.gitignore` excludes them from normal staging.

The README documents that a request authenticated before concurrent revocation may finish. This is consistent with the M1 read-only identity boundary and is not classified as a bypass. Future dispatch must implement the separate B authorization contract.

## Environment and exact checks

Host: macOS ARM64. Existing Colima profile `ai-task-platform`: Linux ARM64, 2 vCPU, 4 GiB RAM, 20 GiB disk. Docker client 29.8.2, server 29.5.2, standalone Docker Compose 5.6.0. Python 3.14.8 on host and application image; uv 0.13.1; pytest 9.1.1; pytest-asyncio 1.4.0; Ruff 0.17.0; mypy 2.4.0; actionlint 1.7.12. Running PostgreSQL 17.11 and Redis 8.2.2. Dependencies and container digest pins were read from `pyproject.toml`, `uv.lock`, `.python-version`, Dockerfile and Compose. No dependency changes were made.

Effective pool/DB settings: API reads 8 + control 2; planned two workers each 4 execution + 2 control; admin 2, migration 1, probes 3, reserve 10, total budget 38 within PostgreSQL `max_connections=50`. Pool checkout 2 seconds, lock timeout 1 second, statement timeout 3 seconds, idle-in-transaction timeout 5 seconds, `READ COMMITTED`, UTC. Worker execution pools are budgeted for M3, not running in M1.

Commands ran from the repository root. Existing ignored `.env` and `.secrets` were preserved. The missing temporary uv installation was recreated without changing dependencies:

```sh
python3 -m venv /private/tmp/ai-task-uv
/private/tmp/ai-task-uv/bin/pip install uv==0.13.1
UV_CACHE_DIR=/private/tmp/ai-task-uv-cache make UV=/private/tmp/ai-task-uv/bin/uv lint typecheck test-unit
actionlint
colima start --profile ai-task-platform
DOCKER_CONTEXT=colima-ai-task-platform docker-compose -p atp-m1-test down --volumes --remove-orphans
DOCKER_CONTEXT=colima-ai-task-platform COMPOSE=docker-compose UV_CACHE_DIR=/private/tmp/ai-task-uv-cache make UV=/private/tmp/ai-task-uv/bin/uv test-integration smoke-compose
```

| Check | Independently observed result |
|---|---|
| Ruff lint | PASS, no diagnostics |
| Ruff format check | PASS, 31 files already formatted |
| Strict mypy | PASS, 22 source files |
| Complete unit suite | **10 passed, 0 failed, 0 errors, 0 skipped**, 0.17 s |
| Complete integration suite | **48 passed, 0 failed, 0 errors, 0 skipped**, 5.74 s |
| Integration breakdown | Database/roles/constraints 31; health 4; identity/CLI/concurrency 12; migration round-trip 1 |
| Empty DB startup | PASS; a new `atp-m1-test_pgdata` volume was created, SQL bootstrap/migration/initialization completed, API became healthy |
| Migration round-trip | PASS; downgrade to base, upgrade head twice, readiness closed until initialization, API credential rejected as migrator |
| Compose smoke | PASS; real Redis stop/restart, authenticated reads, revocation, response/log redaction, runtime/container isolation, worker preflight |
| actionlint | PASS, exit 0 |
| `git diff main...HEAD --check` | PASS, exit 0 |
| Additional adversarial harness | **6 passed, 0 failed, 0 errors, 0 skipped** on the final complete run |
| Generated credential disclosure scan | PASS; 11 password/DB-URL/Redis-URL fixture values checked in tracked files and all disposable-stack logs; 0 disclosures, values never printed |

Thus all **58 existing tests** passed; the six additional scenarios are reported separately rather than counted as repository tests. Compose smoke is a separate end-to-end verification command, not a pytest test count.

Initial environment observation: Docker was unavailable because the isolated Colima profile was stopped. Starting that existing profile resolved the prerequisite. Compose warned that the buildx plugin was absent and successfully used its classic builder; no required build/test was skipped. The initial adversarial harness stopped after four successful scenarios because its `ALTER SYSTEM` privilege probe incorrectly ran inside a transaction (`25001`, before the permission check). Only the temporary harness was corrected to run that statement in autocommit mode. The complete six-scenario rerun passed, including `42501` permission denial for all 27 forbidden statements. That first run was a harness error, not an application failure or a passing privilege test.

## Additional adversarial scenarios

All data was synthetic. The harness ran in the test image on the private disposable stack, with separate SQLAlchemy engines/connections. There were no provider or report-service requests/effects, since those components do not exist in M1; no paid calls or live credentials were used.

| Scenario | Ordered actions and oracle | Result |
|---|---|---|
| Disable wins over waiting issuance | Lock principal, persist disabled state inside an uncommitted fixture transaction, start `issue_key` through another engine, observe `pg_blocking_pids`, commit disable; require `NotFoundError`, zero issued keys and zero key-created audit rows | PASS |
| Issue completes before disable | Create key and authenticate, call actual `disable_principal` service, authenticate the same key again | PASS; later authentication rejected |
| Two simultaneous revocations | Hold owner lock, start two revoke services through separate engines, observe two PostgreSQL waiters, release holder; read key version and audit count | PASS; version 2 and exactly one revoke audit event |
| Read pool exhausted | Hold eight connections; ninth checkout waits; concurrently query separate control pool | PASS; control query **0.0033 s**, ninth checkout timeout **2.0010 s**; no overflow |
| Expanded forbidden operations | For API, worker and admin roles attempt truncate audit, alter/drop schema objects, grant admin membership, create role, read server file, execute server program, alter system and assume postgres role | PASS; **27/27 denied with SQLSTATE 42501** |
| Synthetic exception/CLI disclosure | In-memory review-only endpoint raises canary error with canary URL/header; capture JSON logs/HTTP error; invoke CLI with invalid canary UUID | PASS; generic HTTP 500, CLI exit 1, zero canary disclosures |

The disable fixture manually holds/updates the principal to control the interleaving; it does not claim a second execution of the production disable service in that particular scenario. The adjacent ordering scenario uses the actual service. Pool measurements validate isolation of the current pool factory, not the future SSE/heartbeat workload or a throughput SLA.

## GitHub CI evidence

Read directly with:

```sh
gh pr view 1 --repo WYLLLY/ai-task-execution-platform --json headRefOid,headRefName,state,statusCheckRollup
gh run view 38858012159 --repo WYLLLY/ai-task-execution-platform --json headSha,conclusion,event,jobs
gh run view 38858003514 --repo WYLLLY/ai-task-execution-platform --json headSha,conclusion,event,jobs
gh run view 38858012159 --repo WYLLLY/ai-task-execution-platform --log
```

The PR remained OPEN on the audited SHA. Both the [pull-request workflow](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/38858012159) and [push workflow](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/38858003514) completed successfully on `e3f6bfd41b44c4e2054a94fc949c2f1c637b3726`. Job steps confirm frozen dependency installation, static/unit checks, secret generation, real integration, Compose smoke and cleanup. The PR-run logs independently show 10 unit tests passed, 48 integration tests passed, Ruff/format/mypy success and smoke PASS. Actions are SHA-pinned; workflow permissions are read-only and no provider secrets are configured. Later documentation-only commits must be associated with their own observed CI checks; this report does not predeclare their result.

## Coverage limits and deferred work

No required existing M1 check was left unexecuted. No remediation is requested because no unresolved defect was confirmed.

F01–F03 require M2 commands/admission/task states. F04–F10 require M3 execution/fencing/external-call recovery. F11–F13 require M4 workflow/tools. F14–F16 require M3/M5 accounting/provider behavior. F17–F19 require M6 SSE/degraded-load behavior. F20–F22 require M7 retention/restore/deployment recovery. These complete scenarios are **deferred, not passed**. M1 identity revocation, real Redis degradation and pool isolation are prerequisite evidence only; they do not certify those later gates. No nonexistent prospective `make test-*` command was treated as an executable check.

Production TLS/reverse proxy, live-provider smoke, prompt-injection/semantic campaigns, full load benchmarks, dependency/image security scans, backups/restore, destructive deployment recovery and provider effect journals were not run: they are outside this M1 request and implemented surface. There is no universal security, availability, performance or production-readiness claim.

Cleanup ran successfully:

```sh
DOCKER_CONTEXT=colima-ai-task-platform docker-compose -p atp-m1-test down --volumes --remove-orphans
DOCKER_CONTEXT=colima-ai-task-platform docker-compose -p atp-m1-test ps -a
```

The final project container list was empty; its disposable volume/networks were removed. The Colima profile, other projects, ignored secrets/environment and application sources were preserved. No commit, push, merge or deployment was performed by this reviewer.

## Reproducible additional harness

Save the following block as `/private/tmp/m1_adversarial_review.py` after recreating the disposable stack with `make test-integration`, then run:

```sh
DOCKER_CONTEXT=colima-ai-task-platform docker-compose -p atp-m1-test run -T --rm --no-deps test python - < /private/tmp/m1_adversarial_review.py
```

Run only against the disposable `atp-m1-test` project. The harness creates synthetic identities and permission-denial probes; clean its volume afterward with the command above. The review-only error route is attached solely to an in-memory app in this temporary process and does not modify repository code or the deployed API.

```python
import asyncio
import contextlib
import io
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select, text, func
from sqlalchemy.exc import DBAPIError, TimeoutError as PoolTimeout
from task_platform.api import create_app
from task_platform.db import create_engine, sessions
from task_platform.identity import provision_principal, issue_key, disable_principal, revoke_key, authenticate, AuthenticationError, NotFoundError
from task_platform.models import Principal, APIKey, AuditEvent
from task_platform.observability import configure_logging
from tests.integration.conftest import settings_for
from httpx import AsyncClient, ASGITransport

async def blocked(observer, count):
    async with asyncio.timeout(.8), observer.connect() as c:
        while (await c.scalar(text("SELECT count(*) FROM pg_stat_activity WHERE usename='atp_admin' AND cardinality(pg_blocking_pids(pid))>0"))) < count:
            await asyncio.sleep(.005)

async def disable_wins():
    engines = [create_engine(settings_for('admin')) for _ in range(3)]
    holder, contender, observer = engines
    try:
        p = await provision_principal(sessions(holder), 'review-disable-race')
        async with sessions(holder).begin() as s:
            owner = await s.scalar(select(Principal).where(Principal.id == p).with_for_update())
            owner.active = False
            owner.permission_version += 1
            await s.flush()
            task = asyncio.create_task(issue_key(sessions(contender), p))
            await blocked(observer, 1)
            assert not task.done()
        try:
            await task
        except NotFoundError:
            pass
        else:
            raise AssertionError('Disabled owner received key')
        async with sessions(holder)() as s:
            assert await s.scalar(select(func.count()).select_from(APIKey).where(APIKey.principal_id == p)) == 0
            assert await s.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.principal_id == p, AuditEvent.action == 'key.created')) == 0
    finally:
        for e in engines: await e.dispose()

async def issue_wins():
    e = create_engine(settings_for('admin'))
    api = create_engine(settings_for('api'))
    try:
        p = await provision_principal(sessions(e), 'review-issue-first')
        k, token = await issue_key(sessions(e), p)
        async with sessions(api)() as s: assert (await authenticate(s, token)).key_id == k
        await disable_principal(sessions(e), p)
        async with sessions(api)() as s:
            try: await authenticate(s, token)
            except AuthenticationError: pass
            else: raise AssertionError('Disabled owner authenticated')
    finally:
        await e.dispose(); await api.dispose()

async def double_revoke():
    es = [create_engine(settings_for('admin')) for _ in range(4)]
    try:
        p = await provision_principal(sessions(es[0]), 'review-double-revoke')
        k, token = await issue_key(sessions(es[0]), p)
        async with sessions(es[0]).begin() as s:
            await s.scalar(select(Principal).where(Principal.id == p).with_for_update())
            tasks = [asyncio.create_task(revoke_key(sessions(e), p, k)) for e in es[1:3]]
            await blocked(es[3], 2)
            assert all(not task.done() for task in tasks)
        await asyncio.gather(*tasks)
        async with sessions(es[0])() as s:
            assert await s.scalar(select(APIKey.permission_version).where(APIKey.id == k)) == 2
            assert await s.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.subject_id == k, AuditEvent.action == 'key.revoked')) == 1
    finally:
        for e in es: await e.dispose()

async def pool_isolation():
    config = settings_for('api')
    reads, control = create_engine(config), create_engine(config, control=True)
    try:
        async with contextlib.AsyncExitStack() as stack:
            for _ in range(8): await stack.enter_async_context(reads.connect())
            async def exhausted():
                start = time.monotonic()
                try:
                    async with reads.connect(): raise AssertionError('overflow connection')
                except PoolTimeout: pass
                return time.monotonic() - start
            t = asyncio.create_task(exhausted())
            start = time.monotonic()
            async with asyncio.timeout(1), control.connect() as c:
                assert await c.scalar(text('SELECT 1')) == 1
            control_time = time.monotonic() - start
            wait = await t
            assert 1.8 < wait < 3.0
            print(json.dumps({'control_seconds': round(control_time, 4), 'read_timeout_seconds': round(wait,4)}))
    finally:
        await reads.dispose(); await control.dispose()

async def extended_denials():
    attempts = 0
    statements = [
        'TRUNCATE audit_events',
        'ALTER TABLE principals ADD COLUMN rogue text',
        'DROP TABLE api_keys',
        'GRANT atp_admin TO atp_api',
        'CREATE ROLE rogue LOGIN',
        "SELECT pg_read_file('/etc/passwd')",
        "COPY (SELECT 1) TO PROGRAM 'true'",
        "ALTER SYSTEM SET log_statement='all'",
        "SET ROLE postgres",
    ]
    for role in ('api','worker','admin'):
        e = create_engine(settings_for(role))
        try:
            for sql in statements:
                async with e.connect() as c:
                    if sql.startswith('ALTER SYSTEM'):
                        c = await c.execution_options(isolation_level='AUTOCOMMIT')
                    try: await c.execute(text(sql))
                    except DBAPIError as exc:
                        assert getattr(exc.orig, 'sqlstate', None) == '42501', (role, sql, getattr(exc.orig,'sqlstate',None))
                    else: raise AssertionError((role,sql))
                    attempts += 1
        finally: await e.dispose()
    print(json.dumps({'forbidden_statements_denied': attempts}))

async def canary_errors():
    canary = 'REVIEW_CANARY_438ff0'
    config = settings_for('api')
    app = create_app(config)
    stream = io.StringIO()
    with contextlib.redirect_stderr(stream):
        configure_logging()
        async with app.router.lifespan_context(app), AsyncClient(transport=ASGITransport(app=app),base_url='http://test') as client:
            @app.get('/review-error')
            async def injected(): raise RuntimeError(canary)
            result = await client.get('/review-error?secret='+canary,headers={'Authorization':'Bearer '+canary})
            assert result.status_code == 500
            assert canary not in result.text
    assert canary not in stream.getvalue()
    assert 'request.failed' in stream.getvalue()
    env = os.environ.copy()
    env['DATABASE_URL_FILE'] = str(Path(env['TEST_SECRETS_DIR']) / 'admin_database_url')
    result = subprocess.run([sys.executable,'-c','from task_platform.cli import main; main()','admin','key-create','--principal',canary],env=env,capture_output=True,text=True)
    assert result.returncode == 1 and canary not in result.stdout + result.stderr
    print(json.dumps({'http_error_disclosures': 0, 'cli_error_disclosures': 0}))

async def main():
    for fn in (disable_wins, issue_wins, double_revoke, pool_isolation, extended_denials, canary_errors):
        await fn()
        print('PASS '+fn.__name__)
    print('6 adversarial scenarios passed; 0 failed; 0 errors; 0 skipped')
asyncio.run(main())
```
