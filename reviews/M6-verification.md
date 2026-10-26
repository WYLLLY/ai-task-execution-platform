# M6 verification and release record

Scope: approved M6, F17–F19, on `feature/m6`, based on validated M5
`43f63971a83832150a0e2123b6df4706550ac443`. M1–M5 were explicitly authorized
and merged already; main's M5 merge is `590a72d56a67b5a5007332480dd37b494f4146c7`.
There is no remaining dependency on an unmerged earlier milestone PR.
The pre-existing AGENTS.md change is excluded from all M6 commits; its SHA256
remains `2d329753a5551d5498b88e0aa3fe309f9aab8243a87e5d2dadb747c84127cfcb`.

Local code/test verdict: **PASS**. Compose smoke: **PASS**. Final GitHub CI remains the required PR gate. This record is updated
only from observed command results. Earlier focused PASS does not establish
full milestone completion.

## Environment and commands

Python3.14; uv0.13.1; PostgreSQL17.11 and Redis8.2.2 in disposable Compose
project `atp-m1-test`, Docker context `colima-ai-task-platform` (4CPU/6GiB VM).
Pinned runtime/tool versions are recorded in `pyproject.toml` / `uv.lock`.
M6 adds OpenTelemetry API/SDK1.45.1 and prometheus-client0.26.0. Nginx is a
digest-pinned private test fixture, not a production deployment.

Final regression runs the installed image with its prewarmed tokenizer asset,
no source mounts, no paid credentials and no live OpenAI egress:

```sh
COMPOSE=docker-compose DOCKER_CONTEXT=colima-ai-task-platform \
  .venv/bin/python scripts/run_verification.py -q --tb=short --junitxml=/tmp/m6-full.xml
```

The coordinator launches pytest in the installed test image and supervises the
real Redis stop/restart from outside the test container. The temporary JUnit
file is not retained after the container exits; durable pytest totals and fault
profile are in `reviews/evidence/m6-regression-release.txt`. No result depends
on the continued existence of that temporary XML file.

Focused entry points:

```sh
make test-accounting test-provider-contracts
make test-sse test-degraded test-observability
make smoke-compose
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
actionlint
UV_CACHE_DIR=/private/tmp/atp-m6-uv-cache uv lock --check --offline
```

On this host, Make uses `UV=/private/tmp/atp-m6-tools/bin/uv`,
`COMPOSE=docker-compose`, and `DOCKER_CONTEXT=colima-ai-task-platform`.
Ordinary GitHub CI uses Linux `docker compose` and pinned uv. CI covers all
integration tests, all unit tests, M5 and M6 named gates and Compose smoke.

## TDD and independent review

`M6-test-matrix.md` maps the layers and F17–F19 to maintained tests and the
chronological RED/GREEN logs. Import/setup/infrastructure failures are retained
but never classified as RED. The existing suite was not deleted, skipped,
disabled or weakened. Existing exact-schema expectations were updated to include
the new M6 schema, retaining exactness.

Independent reports: `M6-initial-review.md`, `M6-stream-review.md`,
`M6-core-review.md` and `M6-final-review.md`. The separate reviewer used
`adversarial-review`, `execution-reliability-review` and
`agent-security-review`. Confirmed defects were reproduced before their fixes:
stale cache publication/read-ahead looping, buffered middleware acknowledgement,
backlog gaps, absolute lifetime, SQL UNKNOWN trace validation, premature nested
commit notifications, missing real alert inputs, and runtime-alert log metadata.
The correction mapping and final reviewer verdict remain separate from author
verification.

## Results

Named gates (all0FAIL/ERRORS/SKIPPED), retained in `m6-gates-release.txt`:

| Command | PASS | Duration |
|---|---:|---:|
| make test-accounting | 54 | 6.36s |
| make test-provider-contracts | 59 | 23.98s |
| make test-sse | 76 | 17.38s |
| make test-degraded | 1 | 13.18s |
| make test-observability | 19 | 3.41s |


Complete installed-image regression: **688 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED
in163.78s**. This includes all M1–M6 unit/integration tests, real proxy scenarios,
real Redis outage with100streams and120reads, populated/clean migration paths
and privilege checks. The embedded fault profile recorded no stream errors and
one physical request/effect with committed B/C during the outage. Images were
built after the final application correction; selected source hashes match the
independent review's anchors.

Intermediate results below are evidence for their exact revision only:

- Review fixes:21PASS,0FAIL/ERRORS/SKIPPED,4.71s.
- Populated migration and concurrent snapshot:18PASS,0FAIL/ERRORS/SKIPPED,8.79s.
- Public event boundaries and multi-batch replay:6PASS,0FAIL/ERRORS/SKIPPED,0.45s.
- Real proxy/network and initial observability:8PASS,0FAIL/ERRORS/SKIPPED,16.54s.
- Real Redis outage with100streams/120reads/B-HTTP-C:1PASS,0FAIL/ERRORS/SKIPPED,
  13.23s (`m6-degraded-execution.txt`); requests=effects=1, no stream errors.
- Final log-alert correction:14PASS,0FAIL/ERRORS/SKIPPED,0.15s.
- Ruff/format:PASS,150files; mypy:PASS,121source files; actionlint:PASS.
- Lock consistency:PASS,48packages resolved offline; initial host cache write
  sandbox error was environmental and resolved with a writable isolated cache.

`make smoke-compose`: **PASS**, exercising identity, lost HTTP response,
PostgreSQL/API restart, Redis outage/recovery, retained command replay/control,
revocation, redaction, container isolation/limits and worker polling/heartbeat.
Retained output: `m6-smoke-release.txt`.

## Limits and unexecuted checks

**SKIPPED: paid live OpenAI smoke**, because no distinct spending authorization
with cap was granted. Synthetic provider contract fixtures are not real provider
validation. No API key is added to API credentials/mounts.

No production load/capacity, multi-process API, TLS deployment, retention job,
backup success or disaster restore is certified by M6. The private proxy and
short F19 profile prove their stated controlled conditions only. Missing external
disk/backup observations are explicit actionable unknowns, never PASS. Watcher
hold timers, runtime counters and renewal streaks are process local and reset
on restart. Pool measurement excludes post-checkout connection validation.
Task fences and cancellation cannot guarantee remote termination/exactly-once
physical effects. See `docs/operations.md` for operational actions and limits.

The user's later authorization permits M7 only after M6 passes its completion
gate and is merged. No M7 implementation is part of this M6 release.


## Delivery

Application commit `88989ce` and fault/CI fixture commit `47bc8c1` contain the
reviewed code. SHA256 anchors from the independent final review were rechecked
unchanged before committing. Review and verification artifacts follow in a
separate documentation commit. The final PR's check status is the authoritative
CI record; no local or reviewer result is presented as GitHub CI success.
