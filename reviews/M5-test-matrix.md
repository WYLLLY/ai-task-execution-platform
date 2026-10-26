# M5 test matrix and execution record

Scope: Responses integration and evidence-based accounting (F14–F16). Base:
M4 `9b184e111081ace4f8f8a1fd565f458bd8eb3210`; PR #4 remains open.
The pre-existing AGENTS.md modification is excluded from this milestone.

| Layer / gate | Executable coverage | State |
|---|---|---|
| Unit / provider contracts | `test_responses.py`: explicit profile, integer/cached pricing, complete wire context, strict tool names/arguments/cardinality, refusal/incomplete/malformed output, opaque replay, partial usage, actual tier, tokenizer asset | PASS — see M5-verification.md |
| HTTP boundary / security | `test_provider_contracts.py`: separate process and independent physical request journal, redirects, 429, truncation, slow drip/absolute deadline, expired permit; no implicit retries/proxy | PASS — see M5-verification.md |
| Workflow / API / E2E | `test_responses_workflow.py`, `test_provider_e2e.py`: registered/pinned version 2, fresh worker processes between turns, opaque checkpoint/call-ID replay, local tool, remote report reconciliation, explicit owner regeneration; authenticated implemented/available workflow catalogue; inherited M4 permission/security suites | PASS — see M5-verification.md |
| F14 / concurrency | `test_accounting_admin.py`, `test_provider_capacity.py`, `test_execution_calls.py`: concurrent exact evidence, fingerprint/revision conflict, commit acknowledgement loss and pool cleanup, shared task/owner/global accounting and capacity | PASS — see M5-verification.md |
| F15 / usage and rollover | Existing `test_execution_calls.py` UTC rollover, old exposure, late/partial evidence, immutable calls/evidence; new real-price reservations and unbounded actual overrun, revision-specific administrative acknowledgement | PASS — see M5-verification.md |
| F16 / restart and quotas | Independent concurrent owners wait behind an observed PostgreSQL lock; tiny shared RPM/active/money/token quotas; persisted 429 cooldown across new executors/configuration replay; deadline terminalization retains exposure | PASS — see M5-verification.md |
| Crash / cancellation / stale workers | M3 `test_execution_calls.py`, `test_execution_failures.py`, `test_execution_resolution.py` remain unfiltered; M5 process-per-turn E2E uses the same A/B/C lifecycle and accounting | PASS — see M5-verification.md |
| Migration / privileges | `test_accounting_schema.py`, `test_database.py`, `test_z_migrations.py`: role grants/denials, readiness under revoked grants, empty database and existing-version upgrades, downgrade/re-upgrade | PASS — see M5-verification.md |
| Smoke controller | `test_openai_smoke.py`, `test_provider_cli.py`: explicit authorization/cap/key gates, forced read-tool then report, exact protocol replay, bounded requests and remaining-cap admission | PASS — see M5-verification.md |
| Full M1–M5 regression | All tests, Ruff, formatting, mypy, installed Compose smoke and GitHub CI | PASS — see M5-verification.md |
| Independent review | Separate reviewer using adversarial, execution reliability and agent security skills; confirmed findings and CI corrections reviewed | See M5-review.md |
| Paid provider smoke | Separate authorization, synthetic data and explicit cap required | SKIPPED: no paid-call authorization |

All requested test categories apply: unit, integration, API, E2E, concurrency,
security, resilience and failure behavior. M3/M4 execution tests are deliberately
retained rather than duplicated for every adapter: M5 reuses their transaction,
permission, accounting and durable workflow implementations. Protocol fixtures
and the loopback HTTP journal do not establish live provider behavior. M6 SSE and
M7 restore/retention features remain outside this implementation.

## TDD record

New modules initially used importable interface stubs. Only failures at the
invoked behavior count as RED; collection, imports and unavailable infrastructure
do not. Every log below was retained before its corresponding implementation or
correction. No retrospective RED is claimed. All listed tests run again in the
final unfiltered suite; targeted GREEN logs are supplementary checkpoints.

Commands use `pytest -p no:cacheprovider <path> -q --tb=short` in the disposable
Compose test service for integration tests, with `PYTHONPATH=/app/src` and read-only
source/test/migration mounts during development. The final verification uses the
installed rebuilt image without these source mounts. Unit-only RED runs use the
same pytest test path locally or in that test service.

| Behavior / pytest selection | Meaningful RED evidence (FAIL / PASS) | GREEN evidence |
|---|---|---|
| `tests/unit/test_responses.py` initial protocol/pricing/configuration | `m5-contract-red.txt`: 29 / 0, invoked missing contract behaviors | `m5-contract-green.txt`: 29 passed; expanded final suite |
| `tests/integration/test_provider_contracts.py` | `m5-http-red.txt`: 7 / 1, missing physical request/response behavior | `m5-http-green.txt`: 8 passed |
| `tests/integration/test_accounting_schema.py` initial schema/grants | `m5-schema-red.txt`: 6 / 0, missing relations/columns/privileges | `m5-schema-green.txt`: 6 passed; expanded final suite |
| Initial `test_responses_workflow.py` and `test_accounting_admin.py` | `m5-workflow-accounting-red.txt`: 7 / 0, missing registration/settlement behaviors | Final unfiltered suite |
| `tests/unit/test_provider_cli.py` | `m5-cli-red.txt`: 5 / 0, missing CLI commands/authorization gate | `m5-cli-green.txt`: 5 passed |
| Protocol item status/ID and retained partial observation | `m5-review-protocol-red.txt`: 4 / 0 | `m5-review-protocol-green.txt`: 5 passed including actual tier |
| Actual processing tier | `m5-review-tier-red.txt`: 1 / 0 | Same protocol GREEN and final suite |
| Durable provider observation schema | `m5-review-observation-red.txt`: 1 / 0 | Final unfiltered suite |
| `test_owner_explicit_regeneration_preserves_old_provider_exposure` | `m5-resolution-red.txt`: 1 / 0, OpenAI regeneration incorrectly rejected | Final unfiltered suite |
| `test_missing_tokenizer_asset_fails_closed_without_runtime_download` | `m5-tokenizer-red.txt`: 1 / 0, missing asset did not fail closed | Final unfiltered suite |
| `test_worker_readiness_checks_m5_required_privileges` | `m5-readiness-red.txt`: 3 / 0, readiness remained healthy after real REVOKE | Final unfiltered suite |
| Initial `tests/unit/test_openai_smoke.py` | `m5-smoke-controller-red.txt`: 2 / 0, premature PASS and missing second turn | `m5-smoke-cap-green.txt`: 3 passed after both smoke corrections |
| `test_lost_commit_ack_replay_is_stable` pool assertion | `m5-ack-pool-red.txt`: 1 / 0, connection remained checked out after committed transaction exception | Final unfiltered suite |
| `test_first_turn_overrun_cannot_spend_the_second_turn_reservation` | `m5-smoke-cap-red.txt`: 1 / 0, two sends despite exhausted remaining reservation | `m5-smoke-cap-green.txt`: 3 passed |
| `test_catalogue_advertises_implemented_available_workflows[1/2]` | `m5-catalogue-red.txt`: 2 / 0, implemented versions missing or advertised as unimplemented | `m5-catalogue-green.txt`: 7 passed across workflow integration file |
| `test_worker_readiness_tolerates_bounded_database_round_trip_latency` | `m5-readiness-latency-red.txt`: 1 failed, 10 deselected; healthy PostgreSQL with 80ms/query exceeds the unchanged 4s readiness budget | `m5-readiness-latency-green.txt` and full final suite |
| `test_fixture_cleanup_retries_confirmed_lock_timeout[True/False]` | `m5-cleanup-red.txt`: 2 failed; transient cleanup aborted and persistent failure was not retried within the defined bound | `m5-cleanup-green.txt`: 2 passed in 4.74s; real blocking observed with `pg_blocking_pids` |
| `test_cli_parent_does_not_initialize_execution_runtime` | `m5-cli-startup-red.txt`: 4 failed after successful subprocess exits; import journal shows duplicate parent runtime initialization, no import/setup failure | `m5-cli-startup-green.txt`: 4 passed; unchanged five-second worker and Responses E2E: 2 passed in 31.03s |

Refactoring retained the shared M3 reservation/settlement and M4 workflow logic
through narrow transport/context/reservation hooks, and introduced explicit outer
session lifetimes for administrative transactions. Existing assertions were not
deleted or weakened. The foundation table list and migration head expectation
were extended for the new schema; fixture cleanup includes the new dependent table.

Intermediate failures remain visible. `m5-workflow-accounting-green.txt` is an
historical intermediate log despite its filename (6 passed, 1 failed), not final
GREEN. `m5-workflow-focus.txt` records the call-immutability guard correction.
`m5-targeted-final.txt` records 74 passed and one invalid deadline fixture setup;
that fixture now expires after creation and waits explicitly. Earlier interrupted
runs have no complete test counts. `m5-oom.json` records the test container's
confirmed OOM; test memory was raised from 512 MiB to 1 GiB. The environment-only
`m5-workflow-red.txt` is not behavioral RED. These are not hidden or treated as
successful acceptance evidence. See the final report for final counts.

## Final CI synchronization corrections

The first startup optimization still initialized database/control modules in the
supervisor parent. Extending the successful-exit import journal produced **4
behavioral failures in 1.65s** before moving these imports to runtime functions
(`m5-cli-control-imports-red.txt`). The four cases then passed in 0.32s.

The cleanup observer's first transaction could cache idle activity for later
polls. Priming `pg_stat_activity` before the cleanup starts reproduced **2 failed
contention-observation tests in 6.62s** (`m5-cleanup-observer-red.txt`). Each sample
now ends its observation transaction, retaining the three-second observation
bound and actual `pg_blocking_pids` assertion. See PostgreSQL's
[statistics snapshot contract](https://www.postgresql.org/docs/17/monitoring-stats.html).

Faster startup also exposed implicit timing in the existing five-process E2E.
The synthetic server sends Retry-After:3; after the second model call, the fourth
process encountered the persisted provider gate and save-report became
`retry_wait`. The fifth process started before its eligibility. The diagnostic
retains per-process durable step states (`m5-e2e-schedule-diagnostic.txt`); the
intermediate **3 passed, 1 failed** runs are retained separately. The test now
waits on both task-specific eligible steps and the shared provider gate, outside
a DB connection, within a ten-second total waiting budget. Five processes,
twenty-second subprocess limits, two physical Responses requests, three durable
calls, four steps, cost276 and terminal success assertions remain unchanged.

The combined cleanup, unchanged five-second worker subprocess, and Responses
E2E selection passes **4 tests in 31.64s** (`m5-observer-cli-green.txt`). Final
unfiltered verification follows these corrections; previous suite results are
historical, not substituted for this final run.

## Contract sources (checked 2026-10-23)

- [GPT-5.4 mini](https://developers.openai.com/api/docs/models/gpt-5.4-mini):
  snapshot `gpt-5.4-mini-2026-03-17`, 400,000 context, 128,000 output ceiling;
  standard text rates per million tokens: USD 0.75 input, 0.075 cached input,
  4.50 output. Application limits remain substantially lower.
- [Responses create](https://developers.openai.com/api/reference/resources/responses/methods/create)
  and [migration](https://developers.openai.com/api/docs/guides/migrate-to-responses):
  stateless `store=false`, encrypted reasoning replay and structured text format.
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
  and [structured outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
- [Official tokenizer releases](https://github.com/openai/tiktoken/releases):
  tiktoken 0.14.0; `o200k_base` for the GPT-5 family.

Fixtures are synthetic protocol examples, not recorded live provider validation.
Project quotas must be supplied explicitly; published account tiers are not evidence
of the deployment's actual limits. No credentials are embedded in configuration.

Evidence logs retain diagnostic text and counts; terminal color codes and trailing
terminal whitespace are normalized for readable Git diffs.
