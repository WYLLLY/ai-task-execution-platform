# M5 independent adversarial, reliability and security review

Review started: 2026-10-23. Updated: 2026-10-24. Reviewer: a separate review subagent, independent of the
implementation author. Skills: `adversarial-review`,
`execution-reliability-review`, and `agent-security-review`.

## Scope and current verdict

Base: M4 `9b184e111081ace4f8f8a1fd565f458bd8eb3210`. Final code revision:
`0b679bf1dd964be83e2debe7735a881a3c402106` on `feature/m5`. The reviewer
followed the working changes through their final commits and verified the
installed final image
`sha256:29da60cb4e70829b8d2824bb2893bee3ad1367b791430e32118e5e33f170e28a`. This is
not a review of a later milestone or authorization to merge. The existing local
AGENTS.md change was neither edited nor staged by this reviewer.

**APPROVE — reviewed M5 implementation and local verification.** All six
reviewer findings are corrected. The independent reviewer executed the complete
post-correction suite and both M5 gates successfully on the installed final
image, with real PostgreSQL and Redis. No confirmed finding remains open.
GitHub CI remains a separate delivery gate; this verdict neither authorizes
a merge nor claims live OpenAI validation.

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
| Final installed-image complete M1–M5 suite | 580 | 0 | 0 | 0 | 206.75 seconds; no filtering |
| `make test-accounting` | 53 | 0 | 0 | 0 | 8.14 seconds; real PostgreSQL |
| `make test-provider-contracts` | 57 | 0 | 0 | 0 | 47.14 seconds; synthetic HTTP boundary and real persistence |
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

Complete outputs: `evidence/m5-full-final.txt` and `evidence/m5-gates.txt`.
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
