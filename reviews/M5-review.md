# M5 independent adversarial, reliability and security review

Review started: 2026-10-23. Updated: 2026-10-26. Reviewer: a separate review subagent, independent of the
implementation author. Skills: `adversarial-review`,
`execution-reliability-review`, and `agent-security-review`.

## Scope and current verdict

Base: M4 `9b184e111081ace4f8f8a1fd565f458bd8eb3210`. Final reviewed code:
`94355047ebd696cf69bd22f5e2f35ccd8c4a430f` on `feature/m5`. Its installed image is
`sha256:963e09240dffa975386dd315795ff3bbbf16f07a5bb5d0fa6c3a07131e408382`.
The independent reviewers followed the transport, accounting, workflow, catalogue,
readiness, fixture-cleanup, CLI initialization and deterministic restart-schedule
corrections through their commits. The existing local AGENTS.md change was neither
edited nor staged by a reviewer. Historical revision-specific results are retained
below and are not presented as tests of this final head.

**APPROVE — independent code and test review.** No confirmed issue remains open
in the reviewed implementation. The full post-correction installed-image suite
passed **589 tests, 0 failures, 0 errors, 0 skips in 189.15 seconds**. The author
executed this suite exclusively; the independent reviewer inspected its completed
output, the final unit/static outputs and the correction diffs. These are distinct
from the historical reviewer-executed 580-test suite.

The final accounting and provider gates passed (54 and 59 tests respectively).
The final Compose smoke output also reports PASS. Final-head GitHub CI and
publication checks remain separate completion requirements until their successful
outcomes are observed. This is not GitHub PR approval, authorization to merge or
live OpenAI validation. M6 is outside this review.

Reviewed boundaries: direct HTTPX Responses transport; pinned model, tokenizer,
tariff and project configuration; complete context and protocol checkpoint
replay; M3 A/B/C and M4 tool/report integration; administrative evidence,
revision and overrun acknowledgement; database grants and readiness; owner
regeneration; worker CLI, optional egress/secret overlay and live-smoke controls.
The approved plan, AGENTS.md, architecture review response and M5/F14–F16 gates
were consulted.

## Findings and dispositions

### R1 — High — Processing tier could invalidate definitive pricing

Location: `responses.build_request` / `parse_output`.
The initial request omitted `service_tier`, and every valid usage response was
priced at the standard tariff. A project configured for another processing tier
could therefore receive definitive underpriced settlement and release its entire
provision. The official [Responses contract](https://developers.openai.com/api/reference/resources/responses/methods/create)
states that the omitted tier defaults to project settings.

Disposition: corrected by explicitly requesting `default` and only settling
usage under the supported actual response tier. Unsupported tier usage is
retained as an observation with exposure intact. Behavioral RED and subsequent
targeted GREEN are recorded in `evidence/m5-review-tier-red.txt` and
`evidence/m5-review-protocol-green.txt`. Confirmed in the final independent suite and provider-contract gate.

### R2 — Medium — Incomplete or invalid output items became tool actions

Location: `responses.parse_output`.
With an outer completed response, an inner function call with `status` set to
`in_progress`, or an invalid item ID, still produced a successful canonical
tool action. An independent local reproduction confirmed this behavior before
correction. Existing initial tests covered unknown tools/cardinality but not
these item identity/status defects.

Disposition: corrected with bounded item identity and completed-status checks;
invalid functional output retains billable usage and produces no action.
Behavioral evidence: `evidence/m5-review-protocol-red.txt` (4 failed) and
`evidence/m5-review-protocol-green.txt` (5 passed, including tier correction).

### R3 — Medium — Partial provider usage evidence was discarded

Location: Responses parsing and `Executor.complete`.
The initial parser converted partial usage to absent cost/tokens and no durable
observation. Full provision correctly remained, but the received evidence was
lost across restart, contrary to plan section 4.1.

Disposition: corrected with a bounded, immutable `provider_observation` on the
call, persisted independently of accepted business progression. It retains
allowlisted numeric usage and response metadata, not source text or opaque
reasoning. Partial evidence does not settle or release exposure. Behavioral RED:
`evidence/m5-review-observation-red.txt`; the final independent suite and
provider-contract gate passed.

### R4 — Medium — Readiness omitted newly required grants

Location: `health.check_database`.
Revoking worker writes to `steps.protocol_items` or
`external_calls.provider_observation` initially left readiness green while C
could no longer persist its result. Revoked acknowledgement-column reads also
went undetected before an admission/resolve operation failed.

Disposition: corrected with a zero-row query of acknowledgement fields and
both new worker UPDATE privilege checks. Negative grant regressions and RED
evidence are in `test_accounting_schema.py` and `evidence/m5-readiness-red.txt`.
The final independent suite and accounting gate passed against real PostgreSQL.

### R5 — Medium — Smoke could pass without a stateless round-trip

Location: `provider_commands.smoke_openai`.
The original one-request smoke accepted any successful canonical action,
including a tool call. It could claim PASS without replaying call IDs/protocol
items or obtaining the final report required by the contract being exercised.

Disposition: corrected with a forced synthetic search, deterministic local tool
output, exact protocol replay and a required final report within two requests
and a shared 60-second deadline. The controller stops on uncertain usage and
does not retry. Offline regressions observed two behavioral failures before the
change (`evidence/m5-smoke-controller-red.txt`). No real provider execution has
been authorized or performed.

### R6 — Medium — First-turn overrun could exhaust the second-turn smoke budget

Location: `provider_commands.smoke_openai` before each transport send.
The revised controller initially reserved two maximum planned turns but checked
only `confirmed_cost <= cap` after the first response. If the first billed cost
overran its reservation and equalled `cap - 1`, a second request was still sent
although its conservative reservation exceeded the remaining one micro-USD.

Required correction: before each send, require accumulated confirmed cost plus
that request's conservative reservation to fit the authorized cap. Required
regression: a valid first tool response with cost `cap - 1` must yield exactly
one attempted request and a failed smoke, with no second send. This does not
claim that an estimator can prevent an unknown provider overrun; it prevents a
new request when known usage has already consumed its available reservation.
Disposition: corrected. Before every send the controller checks accumulated
confirmed cost plus the current request reservation against the cap. The retained
RED shows two requests instead of one (`evidence/m5-smoke-cap-red.txt`); the
three offline smoke-controller regressions then passed
(`evidence/m5-smoke-cap-green.txt`). The independent reviewer inspected the
correction and regression; all three smoke-controller regressions passed again
in the final independent suite and provider-contract gate.

### R7 — Medium — Catalogue concealed registered Responses workflow

Location: `api.workflows` and `tasks.submit_task`.
The authenticated workflow catalogue still selected only version 1 and labelled
its execution unimplemented. After administrative registration of version 2,
clients could submit it by its known identifier but could not discover it through
`GET /v1/workflows`. This contradicted the catalogue contract and the implemented
M4/M5 handlers. The author identified this defect before starting M6; independent
review confirmed both stale conditions in the previous code.

Disposition: correction inspected. `supported_workflow` now provides the same
version/definition validation to admission and discovery. The catalogue still
requires authentication, selects only administratively available workflows,
filters unsupported definitions, and marks the supported handlers implemented.
The parametrized regression tests both versions and hides each after withdrawal.
Meaningful RED: two failures, version 1's false implementation flag and absent
version 2 (`evidence/m5-catalogue-red.txt`). Targeted author-run GREEN: seven
Responses workflow tests passed (`evidence/m5-catalogue-green.txt`). Independent
Ruff/format checks on all three changed files and repository mypy passed. No new
issue was found in the small shared-validator refactor. Complete post-correction
regression and M5 gates passed, as recorded in the final follow-up below; final-head
CI remains a separate delivery gate. No concurrent database test was run by the
reviewer during the author's verification.

### R8 — Medium — Per-column readiness round trips exhaust the health budget

Location: `health.check_database`.
The author demonstrated that an otherwise available real PostgreSQL database with
80 ms of added latency before each query makes worker readiness return
`(False, False, None)` after the existing four-second budget. The readiness path
performed one round trip per INSERT table and UPDATE column, in addition to its
schema/read checks. This created avoidable latency and kept its connection and
read transaction open longer. The retained RED is **1 failed, 10 deselected in
4.20 seconds** (`evidence/m5-readiness-latency-red.txt`); it is a behavioral failure,
not an unavailable database or import failure.

Disposition: the minimal correction was independently inspected on 2026-10-26.
The exact INSERT table list and UPDATE table/column pairs are unchanged. Two
parameterized array queries aggregate those same privilege checks with
`bool_and`; no identifier is interpolated into SQL. Existing schema, read access,
role, connection-capacity, epoch and timeout checks remain. The empty-result
conversion fails closed, and all configured privilege lists are nonempty. The
existing revoked-grant regressions remain intact. Reviewer-run Ruff and format
checks on both changed files and mypy on 96 source files passed. The author-run targeted GREEN passed all **11 accounting-schema tests in
2.36 seconds**, including the existing revoked-grant checks
(`evidence/m5-readiness-latency-green.txt`). The reviewer inspected that output
and the subsequent complete **583-pass** suite; exact attribution appears below. This correction is
supported by its latency reproduction; it is **not a proven explanation** for
any historical CI subprocess timeout or fixture lock failure.

## Mechanisms inspected and evidence limits

- F14: shared platform/owner/provider/task locking serializes reservations and
  settlements; retained evidence fingerprints reject conflicting replay;
  expected revisions protect adjustments. Administrative decisions and evidence
  are immutable, and late settlement does not select a task continuation.
- F15: accounting preserves full unknown/partial exposure, replaces it with
  definitive usage, charges the UTC day of B, and records full overruns. Admission
  checks acknowledgements against the current accounting revision without
  clearing the historical overrun flag.
- F16: provider windows are computed from persisted authorizations;
  registration preserves cooldown/history. Concurrent-owner tests explicitly
  observe PostgreSQL lock waiters. Capacity waits remain deadline-bounded.
- Security: fixed HTTPS provider destination, no environment proxy, redirects
  or transport retries; test override requires the isolated profile and
  synthetic credential. Only read tools are exposed; server-side schemas,
  names and cardinality remain enforced. Protocol data is excluded from public
  step projections. The optional Compose overlay gives the OpenAI credential
  only to the worker.
- The author also strengthened the lost-commit-acknowledgement test to check
  pool cleanup, observed a connection leak, and changed administrative session
  scope so close still executes when commit acknowledgement raises. The
  correction and regression were inspected and passed in the final independent
  suite and accounting gate.

Reviewer-executed checks (gate totals overlap the complete suite):

| Command / reproduction | PASS | FAIL | ERRORS | SKIPPED | Other |
|---|---:|---:|---:|---:|---|
| Initial local `pytest tests/unit/test_responses.py -q` | 27 | 2 | 0 | 0 | Both failures were unavailable tokenizer asset/DNS setup; no product RED claim |
| Later local `pytest tests/unit/test_responses.py tests/unit/test_provider_cli.py -q -k 'not request_uses and not complete_context' --tb=short` | 37 | 1 | 0 | 0 | 2 deselected; failure was the same missing local prewarmed asset prerequisite |
| Ruff lint / format / mypy over source, tests, migrations and scripts, 2026-10-24 | PASS | 0 | 0 | 0 | Ruff passed; 96 files formatted; mypy found no issues in 96 files |
| Pre-R7 installed-image complete M1–M5 suite | 580 | 0 | 0 | 0 | 206.75 seconds; no filtering |
| Pre-R7 `make test-accounting` | 53 | 0 | 0 | 0 | 8.14 seconds; real PostgreSQL |
| Pre-R7 `make test-provider-contracts` | 57 | 0 | 0 | 0 | 47.14 seconds; synthetic HTTP boundary and real persistence |
| Initial independent invalid-item probes | Reproduced | — | — | — | Invalid item status/ID returned succeeded before correction |

The final database run was coordinated exclusively with the author to avoid
concurrent mutation of the disposable fixtures. Exact reviewer commands:

```sh
DOCKER_CONTEXT=colima-ai-task-platform docker-compose -p atp-m1-test \
  -f compose.yaml -f compose.demo.yaml run --rm --no-deps \
  test pytest -p no:cacheprovider -q --tb=short
DOCKER_CONTEXT=colima-ai-task-platform COMPOSE=docker-compose \
  make test-accounting test-provider-contracts
.venv/bin/ruff check src tests migrations scripts
.venv/bin/ruff format --check src tests migrations scripts
.venv/bin/mypy
```

Historical reviewer outputs: `evidence/m5-full-before-catalogue.txt` and
`evidence/m5-gates-before-catalogue.txt`.
The unfiltered suite includes clean migration round-trip, M1 upgrade preservation,
M3 downgrade privilege checks, role denials, concurrency and earlier M1–M4 tests.
Independent HTTP fixture tests assert one physical request for each tested send
scenario and zero for an expired permit. The process-restart E2E asserts two
physical Responses requests and three durable calls including the report save.
The test runtime uses Python 3.14.8, real PostgreSQL/Redis in the dedicated
`colima-ai-task-platform` context, and a bounded 1 GiB test container.
No application or test file was changed by this reviewer.

Historical failures remain visible: the author's prior run had 579 passed,
1 failed and 1 teardown error after fixture TRUNCATE lock timeout SQLSTATE 55P03;
the following task-count assertion saw a fixture row left by the failed cleanup
(`evidence/m5-full-fixture-lock.txt`). The unchanged rerun passed 580 tests
(`evidence/m5-full-unchanged-rerun.txt`). The final independent run above uses the
final strengthened protocol fixtures and supersedes these intermediate outcomes.
The earlier 579-pass pre-R6 run is retained as
`evidence/m5-full-before-cap-fix.txt`, not presented as post-correction evidence.
The author also recorded and addressed test-container OOM and local tokenizer
setup issues; neither is classified as behavioral TDD RED.

Live OpenAI smoke: **SKIPPED — separate paid-call authorization was not given**.
The reviewer inspected the recorded SKIPPED command output in
`evidence/m5-live-smoke.txt`. Synthetic fixtures and physical loopback journals
establish local enforcement, not actual provider compatibility, billed invoice
accuracy, report truthfulness or universal prompt-injection resistance. M6 SSE
and M7 restoration/retention are outside this review scope. Push/PR/CI evidence
is maintained separately in the author's verification report.

## Historical CI timing failures inspected in final follow-up

The reviewer read both failed attempts of GitHub PR run
[40095136809](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40095136809)
at historical head `0d72817`. Each integration run produced **449 passed,
1 failed, 0 errors, 0 skipped** (294.40s and 288.08s). Both failures were
`test_worker_cli_preflight_and_explicit_simulation_profile`: attempt 1 timed out
waiting five seconds for the denied simulation subprocess; attempt 2 timed out
waiting five seconds for the allowed simulation subprocess. The traces do not
establish an unauthorized request or a production accounting failure. The M5 CLI
diff adds provider imports only within selected provider commands and does not
change this simulation branch. Runner scheduling/startup pressure is a plausible
explanation, not a proven diagnosis. No existing timeout or assertion was
weakened. These outcomes remain failed verification attempts and must not be
omitted merely because other runs pass. Final-head CI is a separate required
delivery gate.

The first local post-interruption attempt on 2026-10-25 produced **581 passed,
1 failed, 0 errors, 0 skipped in 205.56s**
(`evidence/m5-full-after-interruption.txt`). Its first accounting test passed
its evidence-count and settled-cost assertions but the unscoped
`SELECT state FROM tasks` returned `queued` instead of `suspended`. The fixture
only truncates task tables on teardown; the preceding interrupted suite had
not completed. Residual unrelated task rows are a concrete explanation for this
first-test-only failure, rather than evidence that reconciliation restarted its
selected task. The traceback does not identify the row itself. An unchanged
complete rerun after fixture cleanup is required; this attempt is retained as
failed verification, never relabelled PASS or behavioral TDD RED.

The unchanged complete post-R7 rerun passed **582 tests, 0 failures, 0 errors,
0 skips in 202.53s** (`evidence/m5-full-final.txt`). The author executed it
exclusively against the installed rebuilt image; the follow-up reviewer inspected
the completed output. This is distinct from the prior reviewer-executed 580-test
run and includes both R7 catalogue regressions. No application change or test
weakening occurred between the interrupted-environment failure and this rerun.

The post-R7 PR run
[40211730161](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40211730161)
was also inspected independently. Integration reported **451 passed, 1 failed,
1 error, 0 skipped in 284.01 seconds**. Its error was fixture TRUNCATE lock
timeout; its failure was the five-second wait for the authorized simulation
subprocess at `test_execution_worker.py:149`. These traces establish failed
verification, but do not identify the blocking session or prove a cause shared
with R8. No timeout, existing assertion or privilege check was weakened in R8.

## R8 final-code verification follow-up

Final reviewed code: `1019d3023e245adb8a66db0eac82521954399a1e`, with installed
image `sha256:af0a58265173d50a06e32823adef925b1e7a85bef130a6c282f05164bcbc40f4`.
The author ran the unfiltered suite exclusively against that rebuilt image:
**583 passed, 0 failed, 0 errors, 0 skipped in 205.17 seconds**
(`evidence/m5-full-final.txt`). This includes R8 and supersedes the historical
582-test result for final-code verification. The reviewer inspected the complete
summary and focused GREEN; these are author-executed results, distinct from the
historical reviewer-executed 580-test run. Final author-run Ruff passed, format
reported 120 files unchanged, and mypy reported no issues in 96 source files
(`evidence/m5-static.txt`). No regression assertion, timeout or privilege was
relaxed. The author-run final gates passed and their completed output was
independently inspected (`evidence/m5-gates.txt`):

| Final-code gate | PASS | FAIL | ERRORS | SKIPPED | Duration |
|---|---:|---:|---:|---:|---|
| `make test-accounting` | 54 | 0 | 0 | 0 | 9.66 seconds |
| `make test-provider-contracts` | 59 | 0 | 0 | 0 | 46.74 seconds |

Both gate totals overlap the complete 583-test suite. Independent review verdict:
**APPROVE for the reviewed implementation and test evidence**, with no confirmed
unresolved finding. This does not represent GitHub review approval, permission
to merge, paid provider validation, or a waiver of final-head CI and remaining
delivery checks. The author must record the final CI outcome before declaring M5
complete or beginning the conditionally authorized next milestone.


## Test-fixture cleanup follow-up (2026-10-26)

A separate independent reviewer read AGENTS.md, the approved M5/F14–F16 plan,
the accepted architectural tradeoffs, all three review skills and the preceding
review. Scope: the working-tree correction to
`tests/integration/conftest.py` and new
`tests/integration/test_fixture_cleanup.py` above application revision
`1019d3023e245adb8a66db0eac82521954399a1e`. No application, migration,
privilege, HTTP transport or accounting logic changes in this correction.

The disposable fixture's TRUNCATE can encounter an existing reader's lock and
fail with PostgreSQL SQLSTATE `55P03`. Earlier CI and local reports retained
these teardown errors; they were not relabelled successful tests. The new
fixture retries the **whole cleanup transaction**, at most three attempts, only
after `engine.begin()` has rolled back a confirmed `55P03` error. It retains the
existing one-second database lock timeout, raises other errors immediately and
raises persistent lock contention on the third attempt. This is test-fixture
DDL cleanup, not an extension of the application's transaction retry policy or
permission to retry an external effect. No existing behavioral assertion,
privilege check, test timeout or test selection was weakened.

The new real-PostgreSQL regression uses separate blocker and observer engines,
observes `pg_blocking_pids`, and deliberately holds an ACCESS SHARE table lock.
The transient case releases the blocker after the first observed lock timeout
and checks that cleanup removed the created task. The persistent case verifies
three lock-timeout failures, propagated `OperationalError`, and the retained
row. A final cleanup restores the disposable database even during the RED run.
The reviewer inspected both the implementation and tests and found no confirmed
new defect. No concurrent database test was started by this reviewer.

Author-executed evidence independently inspected:

| Check | PASS | FAIL | ERRORS | SKIPPED | Duration / interpretation |
|---|---:|---:|---:|---:|---|
| Behavioral RED, `m5-cleanup-red.txt` | 0 | 2 | 0 | 0 | 2.69 seconds; transient cleanup raises, persistent case sees one attempt instead of three |
| Targeted GREEN, `m5-cleanup-green.txt` | 2 | 0 | 0 | 0 | 4.74 seconds; real lock contention and rollback |

The separate setup failure in `m5-cleanup-setup-error.txt` is not TDD RED.
Reviewer-executed checks: Ruff passed on both changed test files, format found
both already formatted, and repository mypy passed on 97 source files.

The downloaded post-R8 PR CI log was also inspected: **452 passed, 1 failed,
1 error, 0 skipped in 291.81 seconds**. It shows a fixture TRUNCATE lock timeout
at `test_all_step_state_arcs[running-unknown]` and the existing worker simulation
subprocess timeout. This cleanup correction addresses the reproducible fixture
failure; it does not establish the cause or resolution of the subprocess timing
failure. Final full-suite, gate and final-head CI outcomes remain required.

**APPROVE for this test-fixture correction's code and targeted evidence.** No
confirmed finding remains open in the reviewed change. Milestone completion
remains pending the author's full post-correction verification and green final
CI; no merge, next-milestone work or paid provider request is authorized by this
review verdict.


## CLI initialization follow-up (2026-10-26)

The independent reviewer inspected the subsequent working-tree change to
`src/task_platform/cli.py` and `tests/unit/test_cli_startup.py`. The CLI previously
initialized uvicorn and execution/workflow modules in its parent process, then
initialized them again when its spawned worker imported the CLI. The OpenAI
selection additionally imported `provider_commands` before spawning. Moving
execution imports into the worker coroutine and using a module-level
`openai_run` coroutine avoids this duplicate initialization. API/demo commands
still import uvicorn immediately before running their server. The wrapper is
picklable under the existing spawn supervisor and awaits the same OpenAI worker
coroutine; the supervisor lifecycle, simulation opt-in checks, credential loading,
transport behavior and existing five-second integration limits are unchanged.

The author wrote a subprocess import-observation regression before the change.
Its finder records actual imports without rejecting or replacing them. A stub
supervisor prevents execution in this unit test; successful parsing must still
exit zero. Four behavioral assertion failures were retained in
`evidence/m5-cli-startup-red.txt` (**0 passed, 4 failed, 0 errors, 0 skipped in
2.01 seconds**). These are not failed imports or unavailable infrastructure.
The author-run GREEN retained in `evidence/m5-cli-startup-green.txt` and the
independent reviewer rerun both produced **4 passed, 0 failed, 0 errors,
0 skipped in 1.59 seconds**. Reviewer Ruff and format passed on both files;
repository mypy passed on 98 source files.

No confirmed defect was found in the correction. This evidence demonstrates
removal of duplicate initialization, not a guarantee of sub-five-second startup
under arbitrary scheduling or a conclusive diagnosis of every prior CI timeout.
The existing actual worker subprocess test and persisted Responses workflow E2E,
complete post-correction suite, gates and final-head CI remain necessary.
**APPROVE for the inspected code and targeted test evidence**, without waiving
those remaining verification requirements. No database tests were run concurrently
by this reviewer, and no paid provider request was made.


## Final reviewed-head verification (2026-10-26)

Reviewed code `5392a5d963dd156e6b3c40e5ee5503f0a6ffb986` includes the bounded
disposable fixture cleanup and lazy CLI initialization reviewed above. No further
application changes were made by a reviewer. Author-executed completed outputs
were independently inspected:

| Check | PASS | FAIL | ERRORS | SKIPPED | Duration / scope |
|---|---:|---:|---:|---:|---|
| Unfiltered installed-image M1–M5 suite | 589 | 0 | 0 | 0 | 212.22 seconds; `evidence/m5-full-final.txt` |
| Local unit suite | 134 | 0 | 0 | 0 | 4.13 seconds; `evidence/m5-unit.txt` |
| `make test-accounting` | 54 | 0 | 0 | 0 | 9.59 seconds; `evidence/m5-gates.txt` |
| `make test-provider-contracts` | 59 | 0 | 0 | 0 | 45.34 seconds; `evidence/m5-gates.txt` |
| Ruff / format / mypy | PASS | 0 | 0 | 0 | 122 files formatted, 98 source files type-checked; `evidence/m5-static.txt` |

The unit and gate tests overlap the full suite. The complete suite retains the
unchanged real worker five-second subprocess assertions and provider restart E2E.
The completed gate output was subsequently inspected independently. Final CI and
remaining delivery conclusions must be recorded when completed; this section does
not turn a running check into PASS.


## Final CI correction review: control imports and statistics observations (2026-10-26)

A further independent reviewer read AGENTS.md, the M5/F14–F16 acceptance gates,
the architecture response and all three review skills. Reviewed scope is the
commits `98aa7e3`, `f823190` and `94355047ebd696cf69bd22f5e2f35ccd8c4a430f`
above `5392a5d963dd156e6b3c40e5ee5503f0a6ffb986` in
`src/task_platform/cli.py`, `tests/unit/test_cli_startup.py`,
`tests/integration/test_fixture_cleanup.py` and
`tests/integration/test_provider_e2e.py`. The reviewer changed no application
or test file and ran no database test concurrently with the author's checks.

The previous CLI correction still imported SQLAlchemy, configuration, identity,
database, health, catalogue and metric modules in the supervisor parent. The
current change moves these imports to the command that needs them. Supervised
worker execution branches before `dispatch_command` and therefore does not
initialize the database runtime in its parent. The existing module-level worker
coroutines remain picklable; the child executes the same readiness, profile,
authorization, credential and workflow checks. Non-worker command errors still
pass through the same allowlisted error handler; supervised child exceptions
remain sanitized by the unchanged supervisor. No retry, privilege, HTTP or
accounting behavior is altered. The import journal records real successful
imports instead of injecting import failures. Its expanded regression retained
**0 passed, 4 failed, 0 errors, 0 skipped in 1.65 seconds** before the correction
(`evidence/m5-cli-control-imports-red.txt`). Author GREEN: **4 passed in 0.32
seconds**; independent rerun: **4 passed, 0 failed, 0 errors, 0 skipped in 0.34
seconds**. These observations do not promise startup timing under arbitrary
runner scheduling.

The cleanup observer previously polled `pg_stat_activity` within one implicit
transaction. PostgreSQL documents that current-session query information is
collected at the first request and retained until the transaction ends,
independently of ordinary READ COMMITTED row visibility
([PostgreSQL 17 statistics documentation](https://www.postgresql.org/docs/17/monitoring-stats.html)).
The strengthened regression explicitly primes that snapshot before starting
cleanup. Without ending observation transactions it retained **0 passed, 2
failed, 0 errors, 0 skipped in 6.62 seconds**, both actual three-second observation
timeouts (`evidence/m5-cleanup-observer-red.txt`). Committing each observation
transaction is the minimal correction and preserves the original three-second
bound, blocker engine, exact lock-error assertions and cleanup retry count.
It does not change application transaction semantics or weaken a product test.

Reviewer-executed Ruff and format checks passed for all three changed files.
Both `.venv/bin/mypy src tests` (88 source files) and the repository-configured
`.venv/bin/mypy` (98 source files) passed.
The author-run combined installed-image check in
`evidence/m5-observer-cli-intermediate.txt` reported **3 passed, 1 failed, 0 errors,
0 skipped in 26.73 seconds**: both real-PostgreSQL observer cases and the unchanged
five-second worker subprocess test passed, while the Responses restart E2E saw
a nonterminal task after its five fresh processes. That E2E failure is retained
as a failed check and requires a concrete diagnosis/correction before approval.
No final complete-suite or CI PASS is inferred from these targeted results.

The retained per-process history in `evidence/m5-e2e-schedule-diagnostic.txt`
resolves the missing transition: after the third process the report step is
ready; the fourth moves it to `retry_wait` with approximately five seconds of
remaining eligibility; the fifth starts before that eligibility and makes no
progress. This is capacity waiting, not failed remote report reconciliation.
The independent reviewer traced the synthetic server's unconditional
`Retry-After: 3` header, the transport's retained delay and the executor's durable
`provider.next_allowed_at`. Faster process startup exposes that shared cooldown
between the second model result and the report send. Waiting on ready-step time
alone does not account for the provider gate. A bounded readiness barrier should
respect both persisted step eligibility and provider cooldown before the next
fresh worker; all five processes and original outcome/count assertions must
remain. No application cooldown or test timeout should be loosened to hide it.

The final scheduling correction in `94355047ebd696cf69bd22f5e2f35ccd8c4a430f`
was independently inspected. It obtains the task identifier through the owner
API, reads only that task's ready/retry-wait/unknown step eligibility and the
shared provider cooldown, and waits for the later persisted timestamp using
fresh database time. It releases the database connection before sleeping. Each
wait must be at most five seconds and all added waits consume a ten-second total
budget. The five fresh worker processes retain their twenty-second limits; the
existing succeeded-state, two physical model requests, exact opaque replay,
276 micro-USD confirmed usage, three calls and four steps assertions remain.
No database state or clock is modified to force readiness. Bounded diagnostic
history adds only synthetic step metadata to an assertion failure.

The corrected combined installed-image run passed **4 tests, 0 failures, 0
errors, 0 skipped in 31.64 seconds** (`evidence/m5-observer-cli-green.txt`),
covering both real-PostgreSQL observer cases, the unchanged five-second worker
CLI subprocess checks and provider restart/report reconciliation. The reviewer
independently reran CLI import regressions (**4 passed in 0.34 seconds**),
Ruff/format on all four changed files and configured mypy (**98 source files**);
all passed. No database tests were launched by this reviewer.

**APPROVE for these three corrections and their targeted evidence.** No confirmed
issue remains in the inspected diff. Final M5 completion remains **PENDING**
until the full post-correction installed-image regression, gates, Compose
checks and final-head GitHub CI succeed. The preceding 589-test result belongs
to the earlier reviewed head and is not substituted for those checks.


## Final correction-head regression evidence (2026-10-26)

Reviewed head: `94355047ebd696cf69bd22f5e2f35ccd8c4a430f`; installed image:
`sha256:963e09240dffa975386dd315795ff3bbbf16f07a5bb5d0fa6c3a07131e408382`.
The reviewer inspected the completed author-run outputs against this rebuilt
image and current code:

| Check | PASS | FAIL | ERRORS | SKIPPED | Duration / evidence |
|---|---:|---:|---:|---:|---|
| Unfiltered installed-image M1–M5 suite | 589 | 0 | 0 | 0 | 189.15 seconds; `evidence/m5-full-final.txt` |
| Local unit suite | 134 | 0 | 0 | 0 | 2.17 seconds; `evidence/m5-unit.txt` |
| Ruff / format / mypy | PASS | 0 | 0 | 0 | 122 formatted files, 98 type-checked source files; `evidence/m5-static.txt` |

The unit suite overlaps the complete suite. No previous timing failure or
failed intermediate verification is relabelled PASS. The final complete suite
now includes the same five-second CLI integration bounds, real PostgreSQL
contention checks with refreshed observation transactions and the bounded
provider/step eligibility barrier between five independent worker processes.
**APPROVE for final code and completed local regression evidence.** Current gates,
Compose smoke, final-head CI and delivery remain pending until independently
inspected successful outputs are available.

The completed final gate output was then inspected independently
(`evidence/m5-gates.txt`): `make test-accounting` **54 passed, 0 failed, 0 errors,
0 skipped in 9.65 seconds**; `make test-provider-contracts` **59 passed, 0 failed,
0 errors, 0 skipped in 40.20 seconds**. Both selections overlap the complete
589-test suite. Compose smoke and final-head CI/publication remain pending.

The final Compose smoke output was independently inspected in
`evidence/m5-compose-smoke.txt`; it reports **PASS** for identity, lost HTTP
response, PostgreSQL/API restart, Redis outage/recovery, retained command replay,
cancellation, revocation, redaction, runtime isolation/limits and worker
PostgreSQL polling/heartbeat during Redis outage. The author reported successful
exit status 0 for the pipeline. This smoke is a scenario-based script, not an
additional pytest test count.

**Final independent verdict: APPROVE for code, tests, gates and local Compose
evidence at `94355047ebd696cf69bd22f5e2f35ccd8c4a430f`.** No confirmed finding
remains open. The implementation author is responsible for recording successful
final-publication-head CI and delivery. A documentation-only follow-up commit
does not imply an unreviewed application change; any new application correction
would require renewed verification. This review authorizes no merge, auto-merge,
paid provider request or next-milestone implementation.
