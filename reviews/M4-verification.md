# M4 verification

Scope: M4 only, branch `feature/m4`, baseline M3 commit `046ebc4`. The pre-existing
AGENTS.md edit is preserved outside M4 commits. M1–M3 PRs remain unmerged dependencies;
no merge or M5 implementation is authorized.

## Environment and reproducible commands

Python 3.14.8, uv 0.13.1, PostgreSQL 17.11, Redis 8.2.2; pinned lockfile and image digests.
Docker context `colima-ai-task-platform`, disposable Compose project `atp-m1-test`.
Application PostgreSQL and the report service PostgreSQL use independent clusters/volumes.
No live provider requests or credentials were used.

```sh
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/mypy
actionlint .github/workflows/ci.yml
git diff --check

export DOCKER_CONTEXT=colima-ai-task-platform
export COMPOSE=docker-compose
docker-compose -p atp-m1-test -f compose.yaml -f compose.demo.yaml build api test
docker-compose -p atp-m1-test -f compose.yaml -f compose.demo.yaml up --wait api demo
docker-compose -p atp-m1-test -f compose.yaml -f compose.demo.yaml run --rm --no-deps \
  test pytest -p no:cacheprovider tests -q --tb=short
make test-workflows test-tools test-e2e
.venv/bin/python scripts/smoke_compose.py
```

During development, source/test mounts were used as documented in the test matrix. Final
verification uses the installed image; the complete run also includes unit tests, API tests,
role/constraint tests, deterministic concurrency, failures and clean migration/upgrade paths.
No SQLite or mocked database establishes PostgreSQL guarantees.

## Results and corrections

Final unfiltered installed-image regression: **PASS — 504 passed, 0 failed, 0 errors,
0 skipped in 157.89 seconds** (87 unit tests and 417 integration/API/database/failure tests).
All M1–M4 tests were enabled. The run includes clean migration round-trips and upgrade paths.
The corrected physical crash/lost-ack subset passed **5 tests**, with 6 unrelated tests
deselected, in 9.61 seconds. Independent review: **APPROVE**, no unresolved confirmed
findings; see [M4-review.md](M4-review.md).

| Final gate | Result |
|---|---|
| Unfiltered M1–M4 pytest suite | PASS: 504 passed, 0 failed/errors/skipped, 157.89s |
| `make test-workflows` | PASS: 34 passed, 0 failed/errors/skipped, 15.67s |
| `make test-tools` | PASS: 24 passed, 0 failed/errors/skipped, 2.23s |
| `make test-e2e` | PASS: 3 passed, 0 failed/errors/skipped, 33.99s |
| Compose smoke: restart, Redis outage, replay/control, roles and isolation | PASS |
| Ruff / format / mypy / actionlint / diff checks | PASS |
| Independent adversarial, reliability and security review | APPROVE |

Overlapping focused totals are not added to the 504 unique tests.

The first unfiltered M1–M4 run produced **499 passed, 1 failed, 0 errors, 0 skipped in
157.44 seconds**. The M3 `test_b_db_measurement_deducts_elapsed_transaction_time` injects a
replacement for the original admission function. Passing the new `kind` keyword to the
unchanged simulated-model path broke that injection. The original six-argument call is now
preserved for that path; the test and its elapsed-time assertion were not changed. The final complete rerun above supersedes this intermediate failure.

Independent-review regressions first reproduced seven failures across local-tool deadline,
unpinned document materialization, missing M4 preflight grants, report storage copies and
probe deadline after delayed commit. The combined focused suite then passed **18 tests**.
Details and original RED counts are retained in the [test matrix](M4-test-matrix.md) and
[redacted evidence](evidence/). The M1/M2 historical TDD exception remains baseline-only.

The next complete run produced **503 passed, 1 failed, 0 errors, 0 skipped in
157.94 seconds** and exposed an instrumentation regression in the existing `c_ack` crash
fixture: SQLAlchemy emits `after_commit` for SAVEPOINT release as well as the outer COMMIT.
The injector now ignores nested transactions and still raises after the durable outer commit.
The independent reviewer verified the callback flags; all crash/effect/state/accounting
assertions remain unchanged. The final full rerun includes this correction.

Static checks passed: Ruff lint, formatting (99 Python files), strict mypy (79 source files),
actionlint and whitespace. These passed against the final implementation tree before publication.

## Implementation decisions and bounds

- `0004_workflow` adds immutable corpus versions, ordinary-tool count, frozen report operation
  bytes/key/first authorization and bounded probe count. Worker grants are column-specific;
  the corpus is writable only by admin, with immutable content guarded in PostgreSQL.
- Model and local-tool results create the next step atomically with accepted progression;
  no connection spans HTTP. Source rights are rechecked at reads/B/resolve. SQL search is
  restricted to pinned versions before ranking and LIMIT 5, with at most 2,000 characters
  per excerpt. Context reconstruction retains protocol identity and opaque items internally.
- Local-tool progression rechecks ownership/deadline after the read. Model C uses a local
  savepoint and fresh time after validation: expired progression rolls back while accepted
  usage/evidence remains settled. The legacy M3 handler rejects corpus/advanced M4 steps.
  Report storage is conservatively reserved for transmission, frozen step bytes and terminal copy before B.
- The demo service authenticates a separate worker credential, fixes its purpose/destination,
  transactionally deduplicates exact payloads and retains a separate invocation/effect journal.
  Model outputs cannot choose a destination, header, secret, shell, SQL or writing tool.
- Proof-based report recovery verifies owner/operation/payload hash and selected continuation.
  Missing evidence is not no-effect evidence. At most three probes are scheduled five seconds
  apart; resends keep exact bytes/key, use a new transmission, and obey deadline/24-hour horizon.
- Direct successful operation proof may complete a previously authorized write after source
  revocation; this is evidence of the already-authorized effect, not permission for a new send.
  Terminal tasks and deselected calls cannot acquire a new continuation from late evidence.
- M4 retains the conservative simulated byte estimator/fictitious tariff. Live Responses
  protocol, model tokenizer and real billing verification are M5.

## Semantic results and deferred checks

The deterministic fixture campaign contains 20 normal dossiers and 20 adversarial variants.
Each fixture has source text, owner, pinned reference/version/hash, expected facts and exact
citation spans. Adversarial sources include explicit instructions to falsify conclusions.
Foreign-owner and forged citations are rejected independently of semantic scoring.
All 40 satisfy the report schema; the normal 20 match expected facts and the adversarial 20
are intentionally contradictory. Those 20 semantic failures are expected evaluator outcomes,
not ignored pytest failures. This is not a real-model quality benchmark or injection-immunity
claim. Permission denial is measured separately through the tool and authorization tests.

SKIPPED / not applicable: paid OpenAI smoke (not authorized, M5), SSE/load-degradation campaign
(M6), full retention/disaster-restore/load delivery (M7). Demo deduplication currently retains
records indefinitely, satisfying the minimum window without claiming M7 purge is implemented.
The default API dispatch flag remains closed; test execution requires explicit profile/opt-in.
