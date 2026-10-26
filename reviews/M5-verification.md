# M5 verification

Scope: M5 only, `feature/m5`, based on validated M4 commit
`9b184e111081ace4f8f8a1fd565f458bd8eb3210`. PR #4 was checked again on
2026-10-24 and remains OPEN/unmerged at that exact head. Earlier PRs are dependency
work, not implementation newly introduced by M5. The pre-existing AGENTS.md edit
is preserved outside all milestone commits (SHA256
`2d329753a5551d5498b88e0aa3fe309f9aab8243a87e5d2dadb747c84127cfcb`).
No merge, auto-merge or paid OpenAI request is performed. M6 remains outside this
report; the later user authorization to begin it is conditional on completing M5.

## Environment and commands

Python 3.14.8, uv 0.13.1, Ruff 0.17.0, mypy 2.4.0, pytest 9.1.1,
PostgreSQL 17.11 and Redis 8.2.2. Dependencies and image digests are pinned.
The test service uses a disposable real PostgreSQL/Redis stack; the report service
has its own PostgreSQL database and independent effect journal. The Responses
simulator is a separate process with its own physical request journal, outside
application transactions. Only synthetic provider credentials/data are used.

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
  test pytest -p no:cacheprovider -q --tb=short
make test-accounting test-provider-contracts
make test-unit
.venv/bin/python scripts/smoke_compose.py
make smoke-openai
```

Locally `UV=/private/tmp/atp-m5-tools/bin/uv` selects the restored pinned tool.
Local unit checks use `TIKTOKEN_CACHE_DIR=/private/tmp/atp-m5-tokenizer`, copied
from the image's prewarmed public tokenizer asset. CI uses `make prepare-tokenizer`.
Neither operation generates paid responses. Final integration runs use the
installed rebuilt image without source mounts. Development RED/GREEN commands
and coverage mapping are in [M5-test-matrix.md](M5-test-matrix.md).

## Final results

Installed-image regression, independently reviewed: **PASS — 589 passed, 0 failed,
0 errors, 0 skipped in 189.15s** (134 unit and 455 integration/API/database/failure cases).
The final image is `963e09240dffa975386dd315795ff3bbbf16f07a5bb5d0fa6c3a07131e408382`;
reviewed implementation HEAD is `94355047ebd696cf69bd22f5e2f35ccd8c4a430f`.
Focused gates and publication checks are recorded separately below.

| Check | PASS | FAIL | ERRORS | SKIPPED | Evidence |
|---|---:|---:|---:|---:|---|
| Full unfiltered M1–M5 suite | 589 | 0 | 0 | 0 | `evidence/m5-full-final.txt` |
| Local unit gate | 134 | 0 | 0 | 0 | `evidence/m5-unit.txt`, 2.17s |
| Accounting gate | 54 | 0 | 0 | 0 | Author run, independently reviewed; `evidence/m5-gates.txt`, 9.65s |
| Provider contracts gate | 59 | 0 | 0 | 0 | Author run, independently reviewed; `evidence/m5-gates.txt`, 40.20s |
| Smoke cap correction | 3 | 0 | 0 | 0 | `evidence/m5-smoke-cap-green.txt`, 1.77s |
| Ruff / format / mypy | PASS | 0 | 0 | 0 | `evidence/m5-static.txt`: 122 formatted files, 98 mypy source files |
| Actionlint | PASS | 0 | 0 | 0 | `evidence/m5-actionlint.txt` |
| Compose smoke | PASS | 0 | 0 | 0 | `evidence/m5-compose-smoke.txt`, exit 0; restart, Redis outage, replay/control and API secret isolation |
| Real paid OpenAI smoke | 0 | 0 | 0 | 1 | `evidence/m5-live-smoke.txt`: separate paid-call authorization absent |
| Independent review | APPROVE | 0 | 0 | 0 | [M5-review.md](M5-review.md), no unresolved confirmed findings |
| GitHub CI | See PR checks | — | — | — | [PR #5 checks](https://github.com/WYLLLY/ai-task-execution-platform/pull/5/checks), observed before completion handoff |

Focused runs overlap and are not added to the unique regression total. The real
smoke skip is an explicit user-approved boundary, not evidence of provider
compatibility. No fixture result is labelled real OpenAI validation. On 2026-10-25, automatic
approval review rejected a proposed rerun of `make smoke-openai` because it could
use credentials and incur charges without separate spending authorization. The
command was not executed and was not bypassed; its earlier SKIPPED evidence is
retained, and no live validation is claimed.

## Corrections and intermediate results

The retained pre-final full run passed 579 tests in 206.77s. It predates the final
remaining-cap regression and correction and is not the final acceptance run.
Its original output is `evidence/m5-full-before-cap-fix.txt`.

The first installed post-correction run produced **579 passed, 1 failed, 1 error,
0 skipped in 207.97s**. `command_owner` fixture teardown timed out (`55P03`) on
TRUNCATE after the collision test, leaving a task that made the following cancel
test observe two rows. The unchanged complete rerun passed **580 tests in 207.13s**.
Both outputs are retained (`m5-full-fixture-lock.txt`, `m5-full-unchanged-rerun.txt`).
No product code, assertion or timeout was changed to obtain that rerun. This is a
known isolated fixture-cleanup timing limitation, not a confirmed production
accounting defect. The independent final run uses the subsequently strengthened
protocol fixtures, which now supply valid item IDs/status so invalid tool/content
checks cannot pass merely by failing the identity validator first.

The original independent reviewer found six concrete issues: actual service tier pricing,
invalid/incomplete protocol items, lost partial usage observations, missing
readiness grant checks, a smoke that could pass without a full tool round-trip,
and insufficient per-send remaining-budget admission. All were reproduced with
behavioral RED tests before correction. An additional author regression found a
connection leak after administrative commit acknowledgement loss; explicit outer
session closure fixes it. See the separate review and TDD matrix for dispositions.

The later R7 correction fixes the authenticated workflow catalogue: supported,
available analysis versions 1 and 2 are advertised as implemented; disabling a
version hides it. Two behavioral RED cases preceded the shared supported-handler
predicate and catalogue correction, followed by seven targeted GREEN tests.
The previous 580-test and 53/57 gate results are retained as
`m5-full-before-catalogue.txt` and `m5-gates-before-catalogue.txt`.
A subsequent full run was interrupted by the stopped local VM before producing
counts (`m5-full-interrupted.txt`); it is not acceptance evidence. The first run
after restart produced 581 passed, 1 failed, 0 errors, 0 skipped in 205.56s
(`m5-full-after-interruption.txt`). Its first test's unscoped task-state query
returned `queued` instead of `suspended`, consistent with the interrupted fixture's
remaining task; fixture cleanup runs at teardown, not setup. All remaining tests
passed and teardown completed. A full unchanged rerun follows this cleanup; no
code, assertion or timeout was modified in response.

Historical CI at `0d72817`: the push run
[40095133197](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40095133197)
passed. PR run
[40095136809](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40095136809)
failed twice at the existing five-second worker CLI subprocess bound: attempt 1
at the denied simulation process and attempt 2 at the authorized process.
Each integration run had 449 passed, 1 failed, 0 errors, 0 skipped; unit tests
had 130 passed. Attempt 2 took 288.08s. This records observed startup timing
failures, not proof of an accounting defect or a successful PR check. No existing
assertion or timeout was relaxed. Final-head CI must pass before M5 completion.

At R7 (`8e5ff80`), the clean local suite passed 582 tests in 202.53s
(`m5-full-before-readiness.txt`). Both CI runs nevertheless failed:
[push 40211726372](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40211726372)
had 451 passed, 1 failed, 1 teardown error in 229.71s; fixture TRUNCATE timed out
and the next test saw a leftover call.
[PR 40211730161](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40211730161)
had 451 passed, 1 failed, 1 teardown error in 284.01s; a fixture lock timeout and
the worker CLI bound failed. No blocker backend identity was captured, so the
readiness transaction is a candidate contributor, not a proven CI root cause.

A new deterministic real-PostgreSQL regression injects 80ms per SQL round trip
while retaining actual grants, schema, and the existing 4s health deadline.
It failed behaviorally (readiness false) before the correction. Exact INSERT and
column UPDATE checks are now aggregated into two SQL calls; no privilege or
existing timeout is removed. This reduces unnecessary transaction/lock duration.
The schema/privilege file then passed 11 tests in 2.36s, followed by the complete
583-test historical suite retained below. No existing assertion or timeout was changed.

Earlier development runs include unavailable environment/asset setup (including
a sandbox-denied uv cache write, rerun successfully with tool access), an OOM-killed
512 MiB test container and a deadline fixture that attempted to expire before its
creation timestamp. These are preserved as diagnostics and never counted as
behavioral RED or acceptance PASS. The test service now has a bounded 1 GiB limit;
all original assertions remain enabled. Final complete verification must follow
all corrections.

At `1019d30`, local full regression passed 583 tests (retained in
`m5-full-before-ci-fixes.txt`), but CI still failed. Push run
[40243464918](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40243464918)
had 453 passed, 0 failed, 1 teardown error in 225.21s. PR run
[40243467628](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40243467628)
had 452 passed, 1 failed, 1 teardown error in 291.81s: fixture TRUNCATE lock timeout
and the unchanged five-second worker subprocess bound.

Two further corrections have separate behavioral RED/GREEN evidence:

- Test-only cleanup retries its complete transaction at most three times, only
  after confirmed SQLSTATE 55P03 rollback. Permanent contention still raises;
  application transaction policies, one-second lock timeout and all existing
  product assertions are unchanged. Two independent connections and an observer
  demonstrate actual blocking. Initial one-slot observer-pool setup failure is
  retained as `m5-cleanup-setup-error.txt` and is not counted as RED.
- The CLI supervisor parent no longer initializes execution runtimes which are
  initialized again by the spawned child. Import journaling tests first completed
  successfully but failed the no-parent-runtime assertion in four modes; they now
  pass. The real five-second subprocess test and Responses process E2E also pass
  unchanged (2 tests, 31.03s). This removes observed duplicate initialization; it
  is not a universal startup-latency guarantee.

At `5392a5d`, local tests again passed 589 cases (retained in
`m5-full-before-startup-observer-fixes.txt`). Push CI
[40327622929](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40327622929)
failed with 453 passed, 2 failed, 0 errors, 0 skipped in 292.64s: the worker
subprocess bound and stale cleanup-observer statistics. The PR companion run
[40327628648](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40327628648)
also failed; its final log remains accessible at that link.

Final corrections remove the remaining database/control initialization from the
supervisor parent, refresh each statistics observation transaction and explicitly
synchronize the restart E2E with persisted provider/step eligibility. Behavioral
RED and the combined four-test GREEN are recorded in the matrix. All original
product assertions, physical request/effect counts, worker deadlines and cleanup
retry bounds remain enabled. No performance guarantee is inferred beyond these
measured environments.

## Implemented boundaries and remaining limits

- Direct HTTPX Responses adapter; fixed endpoint, zero hidden retries, disabled
  redirects/proxy inference, bounded payload/response size and absolute deadline.
- Explicit snapshot, tariff, tokenizer and supplier project quota configuration;
  integer micro-USD, conservative complete-context reservations and full actual
  overrun accounting. The configured model's official contract and pricing links
  are in the matrix and [operations guide](../docs/openai-operations.md).
- Versioned M4 workflow integration with private protocol items/call IDs and
  `store=false`, no dependency on provider-side stored conversations. Server-side
  tool/schema/cardinality checks remain authoritative; the model cannot invoke
  report writes, arbitrary URLs, shell, SQL or credentials.
- Migration `0005_provider`: private protocol checkpoints and immutable bounded
  observations; immutable administrative decisions; role-specific grants and
  readiness. Administrative evidence is fingerprinted/revisioned and replayable
  after lost acknowledgement. Acknowledgement never removes the historical
  overrun and applies only to the reviewed accounting revision.
- Shared durable M3 accounting, fencing, cancellation, deadline, unknown-result
  suspension and explicit owner regeneration remain in use. Provision survives
  ambiguity and UTC rollover; final usage is attributed to B's day.
- No live quota or invoice authenticity is inferred. Operators must supply actual
  project limits and review evidence. A local estimator cannot guarantee provider
  invoices, a local fence cannot cancel remote work, and structural citation
  validation does not establish report truth or prompt-injection immunity.
- No public SSE, retention/restore implementation, production deployment or paid
  smoke is included. M6 is not started.

## GitHub delivery

[PR #5](https://github.com/WYLLLY/ai-task-execution-platform/pull/5) targets `main`.
PRs #1–#4 remain unmerged, so the PR includes their cumulative history; the M5
delta starts at M4 commit `9b184e111081ace4f8f8a1fd565f458bd8eb3210`.
The branch was pushed with focused Conventional Commits for the provider
contract, administrative reconciliation, workflow/CLI integration and confirmed
corrections, followed by the English verification artifacts. The implementation
push run [40375341184](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40375341184)
passed at `9435504`. The final documentation publication triggers another CI run;
its final conclusions and exact run links are recorded on the PR after they are
observed. No merge or auto-merge is enabled.
