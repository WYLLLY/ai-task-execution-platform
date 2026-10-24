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
make smoke-compose
make smoke-openai
```

Locally `UV=/private/tmp/atp-m5-tools/bin/uv` selects the restored pinned tool.
Local unit checks use `TIKTOKEN_CACHE_DIR=/private/tmp/atp-m5-tokenizer`, copied
from the image's prewarmed public tokenizer asset. CI uses `make prepare-tokenizer`.
Neither operation generates paid responses. Final integration runs use the
installed rebuilt image without source mounts. Development RED/GREEN commands
and coverage mapping are in [M5-test-matrix.md](M5-test-matrix.md).

## Final results

Independent installed-image regression: **PASS — 580 passed, 0 failed, 0 errors,
0 skipped in 206.75s** (130 unit and 450 integration/API/database/failure cases).
The final image is `29da60cb4e70829b8d2824bb2893bee3ad1367b791430e32118e5e33f170e28a`;
reviewed implementation HEAD is `0b679bf1dd964be83e2debe7735a881a3c402106`.
Focused gates and publication checks are recorded separately below.

| Check | PASS | FAIL | ERRORS | SKIPPED | Evidence |
|---|---:|---:|---:|---:|---|
| Full unfiltered M1–M5 suite | 580 | 0 | 0 | 0 | `evidence/m5-full-final.txt` |
| Local unit gate | 130 | 0 | 0 | 0 | `evidence/m5-unit.txt`, 2.89s |
| Accounting gate | 53 | 0 | 0 | 0 | Independent reviewer; `evidence/m5-gates.txt`, 8.14s |
| Provider contracts gate | 57 | 0 | 0 | 0 | Independent reviewer; `evidence/m5-gates.txt`, 47.14s |
| Smoke cap correction | 3 | 0 | 0 | 0 | `evidence/m5-smoke-cap-green.txt`, 1.77s |
| Ruff / format / mypy | PASS | 0 | 0 | 0 | `evidence/m5-static.txt`: 120 formatted files, 96 mypy source files |
| Actionlint | PASS | 0 | 0 | 0 | `evidence/m5-actionlint.txt` |
| Compose smoke | PASS | 0 | 0 | 0 | `evidence/m5-compose-smoke.txt`, exit 0; restart, Redis outage, replay/control and API secret isolation |
| Real paid OpenAI smoke | 0 | 0 | 0 | 1 | `evidence/m5-live-smoke.txt`: separate paid-call authorization absent |
| Independent review | APPROVE | 0 | 0 | 0 | [M5-review.md](M5-review.md), no unresolved confirmed findings |
| GitHub CI | See PR checks | — | — | — | [PR #5 checks](https://github.com/WYLLLY/ai-task-execution-platform/pull/5/checks), observed before completion handoff |

Focused runs overlap and are not added to the unique regression total. The real
smoke skip is an explicit user-approved boundary, not evidence of provider
compatibility. No fixture result is labelled real OpenAI validation.

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

The independent reviewer found six concrete issues: actual service tier pricing,
invalid/incomplete protocol items, lost partial usage observations, missing
readiness grant checks, a smoke that could pass without a full tool round-trip,
and insufficient per-send remaining-budget admission. All were reproduced with
behavioral RED tests before correction. An additional author regression found a
connection leak after administrative commit acknowledgement loss; explicit outer
session closure fixes it. See the separate review and TDD matrix for dispositions.

Earlier development runs include unavailable environment/asset setup (including
a sandbox-denied uv cache write, rerun successfully with tool access), an OOM-killed
512 MiB test container and a deadline fixture that attempted to expire before its
creation timestamp. These are preserved as diagnostics and never counted as
behavioral RED or acceptance PASS. The test service now has a bounded 1 GiB limit;
all original assertions remain enabled. Final complete verification must follow
all corrections.

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
The branch was pushed with three focused Conventional Commits for the provider
contract, administrative reconciliation and workflow/CLI integration, followed
by the English verification artifacts. Final CI conclusions and run links are
recorded on the PR after publication. No merge or auto-merge is enabled.
