# Independent global review — M1–M7

Date: 2026-10-28 (review begun 2026-10-27). **Final independent code and local
evidence verdict: APPROVE, with the documented limits below.** G1 and R1–R11
are closed; no unresolved confirmed material code finding remains in the reviewed
scope. The latest unfiltered suite records **828 passed, 0 failed, 0 errors and
0 skipped in 313.43 seconds**. The corrected exact reference and separate
saturation campaigns both passed. Final GitHub CI on the documentation handoff
head remains a delivery gate to be observed by the primary author. This review
does not authorize merging, deployment or paid provider calls and is not a
production security or capacity certification. Earlier failures remain intact
in the review history below.

## Scope and method

The review applies `adversarial-review`, `execution-reliability-review` and
`agent-security-review`, after reading AGENTS.md, the approved architecture
plan and its review response. The inspected baseline is feature/m7 HEAD
`b1134bc45ddfac7ad7274fb3f6f3886349d17878`, including the earlier G1 correction
and the uncommitted M7 retention/recovery/delivery increment. The pre-existing
AGENTS.md change was preserved. No implementation, test, Git, network, database
or Compose mutation was performed by this reviewer. Only this report and the
reviewer's own evidence log are written.

Earlier independent reports retain the original G1 and R1–R4 findings and their
corrections. Those findings are not reopened by this report. Inspected areas:

- M1–M6 identity/command replay, owner-scoped read/control paths, B freshness,
  accounting admission and preserved uncertain exposure, event/SSE boundaries;
- migrations 0007–0011, migration-only redaction/association exceptions,
  SECURITY DEFINER search paths and grants, immutable private restore evidence;
- terminal/response/resolution clocks, bounded per-task purge, exact-payload
  unavailability, late accounting and explicit corpus deletion;
- all-task restore quarantine, revoked keys, new epochs, disaster reserve
  revisions, conservative unallocated owner exposure and guarded task release;
- manual close/drain/preflight/reopen, encrypted backup helpers and the evolving
  isolated restore driver/runbooks.

The core mechanisms reviewed preserve platform-first lock ordering, separate
transactions for each purge candidate, fresh database clocks after locks,
unchanged remote call identities and immutable financial evidence. Reserve
revisions serialize under the platform lock; missing history is represented as
audited disaster exposure rather than fabricated task/call authorizations.
Ordinary runtime guards do not query private restore decisions because the
migrator-only branch is a distinct PL/pgSQL statement. Releasing a quarantined
task requires current owner/key, rights, handler, deadline and retained payloads;
uncertain LLM continuation still requires explicit owner resolution.

Further baseline inspection checked current command replay before mutable new
admission, owner-filtered task/usage/event reads, regeneration epoch checks,
current rights on document reads, and the OpenAI/demo transports. The OpenAI
adapter keeps the fixed Responses endpoint, stateless requests and worker-only
key, transport/client environment isolation, zero transport retries, disabled
redirects, bounded raw response bytes and an absolute monotonic permit budget.
The report transport binds owner/operation/hash evidence to the exact saved
payload, constrains its trusted destination and does not follow redirects or
retry a failed request. Unknown responses preserve exposure through the existing
execution/accounting path; functional validity does not manufacture provider
evidence. No additional confirmed baseline defect was found in these inspected
paths. This is a targeted global review, not a proof about all interleavings.

## R5 — Medium — System status loses bounded degradation during DB failure

**Location:** `src/task_platform/api.py:389–396` and
`src/task_platform/recovery.py:340–348` in the inspected revision.

**Scenario:** PostgreSQL is unavailable, its schema is incompatible during a
failed migration, or the recovery metadata query stalls. `health()` deliberately
catches database/network/timeout failures and returns structured readiness
information. The new `/v1/system` handler then unconditionally opens another
session in `recovery_status()`, without an absolute timeout, even when the first
health result already says PostgreSQL/schema are unavailable.

**Impact:** the system endpoint can return 500 or stall instead of disclosing
bounded degraded status and its epoch/availability fields during the exact
incident where operators/clients need them. SQLAlchemy exceptions use the generic
503 handler and also discard the structured status; raw OSError/TimeoutError
become 500. This does not authorize dispatch or release any exposure.

**Independent reproduction:** the actual FastAPI app was exercised with HTTPX
ASGITransport, without lifespan/background workers or any network/database call.
The health function returned a legitimate unavailable Health value and the
recovery read raised `OSError("synthetic connection refusal")`. The actual routes
returned:

```text
GET /health/ready -> 503
{"ready":false,"postgres":false,"redis":true,"schema_compatible":false,
 "epoch_matches":false,"execution_epoch":null,"submission_available":false,
 "dispatch_available":false}
GET /v1/system -> 500 {"detail":"Internal error"}
```

**Cause:** adding metadata introduced an unguarded second dependency operation
after the existing bounded health boundary. The generic exception handler does
not make this metadata operation safe or preserve the system status contract.

**Existing coverage:** successful restoration metadata is tested, but database
failure/incompatible schema/stalled recovery status was not covered at finding
time. The primary author accepted R5 and will add maintained behavioral RED
before correction.

**Recommendation and required tests:** do not attempt recovery metadata through
an unavailable/incompatible database; bound the read and handle failures while
preserving structured 503 status. Cover known DB failure, incompatible schema,
failure after initially healthy status, bounded timeout and successful metadata.
Do not report unknown metadata as verified absence of a restoration incident.

## Independent executed checks

The unfiltered host unit suite was run twice; the retained second invocation was:

```text
TIKTOKEN_CACHE_DIR=/private/tmp/atp-m5-tokenizer .venv/bin/pytest tests/unit -q --tb=short
233 PASS, 6 FAIL, 0 ERRORS, 0 SKIPPED in 3.54s
```

Evidence: `reviews/evidence/m7-final-global-review-units.txt`. All six failures
are `tokenizer_asset_unavailable`: the historical host cache directory no longer
exists. These are environment/asset failures, not a product RED or permission to
weaken tests. No tokenizer network download was attempted. A rerun against the
already-baked image asset is pending, as is the primary author's final installed
image regression.

Offline follow-up, after the primary author copied the already-baked image's
tokenizer cache into the ignored local tools directory:

```text
TIKTOKEN_CACHE_DIR="$PWD/.secrets/m7-tools/tokenizer-cache" .venv/bin/pytest tests/unit -q --tb=short
239 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED in 3.45s
```

Evidence: `reviews/evidence/m7-final-global-review-units-offline.txt`. No download,
network/database request or test weakening was needed. The original six failures
remain preserved as a separate environmental run; the successful rerun does not
replace the required installed-image cumulative verification.

Focused static checks executed independently:

```text
Ruff check: PASS, 11 inspected files
Ruff format --check: PASS, 11 files already formatted
mypy: PASS, 6 source files
```

The 11 files are recovery/delivery/retention, backup and restore host helpers,
the restore fixture driver, and migrations 0007–0011. Mypy covers the six Python
application/helper files, excluding migrations. Static inspection and selected
units do not establish PostgreSQL locking, runtime privileges or remote effects.

## Pending final evidence and review limits

The following are completion evidence still pending from the primary author,
not invented passes or newly discovered bugs in unfinished work:

- corrected R5 with retained meaningful RED/GREEN and a full post-correction
  M1–M7 regression, migrations, exact privileges, Ruff/format/mypy and CI;
- completed actual encrypted backup/restore before B and before task creation,
  independent surviving request/effect journals, old credential rejection,
  external dispatch/egress fencing, measured loss window and safe-reopening RTO;
- required retention/restore/load gates, cumulative reference load without
  silently reducing the approved profile, overload/degraded results and
  separate deterministic semantic/adversarial evaluation;
- image/dependency scan results, immutable release evidence and failed migration/
  compatible rollback exercises, with actual limitations reported;
- public owner-usage treatment of disaster reserves, which was still a pending
  increment during this review;
- clarification whether delivery's operator procedure separately verifies actual
  API/worker schema/privileges before reopen. The current Python preflight checks
  the revision marker, epoch, handlers and active rows; it does not itself audit
  runtime grants. This remains a coverage/design question rather than a claimed
  reproduced defect in this interim report.

An encrypted local exercise does not establish daily off-host backup delivery.
No external destination/credentials or paid-provider authorization were provided
to this reviewer. Such unexecuted checks must remain explicitly SKIPPED with
reasons. M7 merge, deployment, zero RPO and universal injection immunity are not
approved or claimed.

## Follow-up — R5 closed; recovery and privilege evidence inspected

The new system handler skips the recovery query when PostgreSQL/schema are
unavailable, wraps the metadata read in a four-second absolute timeout, catches
SQLAlchemy/network/timeout failures and preserves structured 503 fields with
unknown recovery metadata. No private error text is returned. The original R5
above remains historical; **R5 is closed for the inspected correction**.

Retained author evidence inspected: `m7-system-status-red.txt` — **7 FAIL in
5.87s**; `m7-system-status-green.txt` — **7 PASS in 4.57s**. Independently rerun
the seven status tests together with the then-current six load-metric tests:
**13 PASS, 0 FAIL/ERRORS/SKIPPED in 4.59s**, retained in
`m7-final-global-review-system-load-units.txt`. No network/DB call was made.

The author addressed the delivery write-privilege question with a shared exact
INSERT/UPDATE privilege helper used by normal readiness and by administrative
preflight for both API and worker roles. Two real-PG regressions revoke
UPDATE(state) independently from each runtime role, retain the correct schema
revision, require preflight/reopen rejection and restore the exact grant. The
same increment adds one-statement owner-only disaster reserve usage projection:
allocated owner exposure is included once in uncertain totals and separately
identified; unallocated/global/foreign reserves are not disclosed as owner usage
or fabricated as call rows. Inspected author evidence:

- `m7-preflight-usage-red.txt`: **3 FAIL, 17 deselected in 0.60s**;
- `m7-preflight-usage-green.txt`: **42 PASS in 5.53s**.

These connected results were inspected rather than rerun by the reviewer. The
precise remaining SELECT-privilege edge is described below; the write-grant
correction itself preserves existing exact privilege checks.

Actual encrypted restore evidence inspected:
`m7-restore-drill-seventh-attempt.txt` reports PASS for both isolated snapshots,
one surviving remote effect, exactly two model requests and one demo write,
rejected old DB/API/demo credentials, blocked live egress and authenticated age
tamper rejection. Before-B: one quarantined task, measured potential-loss
interval **4.313s**, restore-to-safe-reopening **4.377s**. Before-creation:
accepted task absent as expected, potential-loss interval **12.154s**,
restore-to-safe-reopening **3.410s**. Those RTO values start at the helper's
restore operation and include verification/reopening; they exclude earlier
isolation work and must not be described as full incident-to-recovery times.
Off-host delivery remains explicitly SKIPPED for missing destination/credentials.

## R6 — Medium — Reference corpus guarantees second-turn context failure

**Location:** original `tests/support/load_profile.py` corpus fixture and
`scripts/load_metrics.py` simulated search request.

All twenty 4-KiB documents contained the search term Revenue. The unchanged
production local search returns five excerpts of up to 2,000 characters. With
the required 4-KiB input, independent execution of the actual context builder
measured the first context at **6,088 bytes** and persisted second-turn history
at **11,048 bytes**. The second actual `build_context` call raised
`ValueError("context_limit")`, before the second model call/demo write. Thus the
nominal fixture could never exercise its claimed fixed two-call workflow.

This is a benchmark-fixture defect, not evidence of product throughput failure
and not justification to weaken the production conservative context bound.
Required correction: retain twenty full-size documents but make only one match
the selected query, or use a correctly pinned single-excerpt local read.
The author retained `m7-load-context-red.txt`: **1 FAIL, 5 PASS in 0.09s** and
is implementing the distinct-document correction. The inspected current helper
uses one Revenue document and nineteen Inventory documents, all still 4 KiB.
Final correction/test closure is pending inspection of its finished GREEN.

## R7 — Medium — Reference PASS did not require the reference pressure

**Location:** original load controller target expression and campaign subscriber/
read/worker monitoring.

The initial subscriber counter incremented before receiving HTTP 200; it counted
attempted slots rather than established/concurrent streams. The controller's PASS
expression ignored that counter, read count, recorded errors and child-process
health. A failed read task was later gathered with its exception ignored; one
worker could exit while the remaining four slots still served one task/second.
As a result, successful tasks plus a few direct/proxy SSE samples could produce
PASS without the required 100 SSE, sustained 10 reads/second or both workers.

Required correction: establish/measure actual stream concurrency, monitor worker
and pressure-task liveness throughout submission and drainage, and make the
reference target fail when the specified pressure was not exercised. Report
failures rather than silently retrying/reducing pressure. Stop the measurement
clock when load stops, so post-load SQL analysis does not falsely increase the
expected read denominator.

The author retained `m7-load-pressure-red.txt`: **5 FAIL, 6 PASS in 0.10s**;
initial `m7-load-pressure-green.txt`: **11 PASS in 0.08s**. The inspected evolving
code now separates target validation, waits for 100 successful active streams,
records peak concurrency, and rejects missing read pressure/errors/dead workers.
Monitoring during drain and the measurement-end clock were sent back to the
author; final code closure remains pending. Passing pure target tests alone does
not establish that an actual benchmark achieved the reference profile.

## Remaining exact privilege edge

The new shared `runtime_privileges` helper currently checks required INSERT and
UPDATE grants. A runtime role with SELECT(tasks) revoked can still satisfy that
helper while administrative delivery reads succeed under the admin identity.
Normal runtime `check_database` separately detects this through actual SELECT
queries, but administrative delivery does not execute those runtime reads. If
preflight is the complete explicit runtime-privilege gate, extend the maintained
negative test to a revoked SELECT grant and audit required reads as well. This
source-level edge was reported promptly; no reviewer DB mutation was performed.

## Load correction follow-up — R6/R7 code findings closed

The reviewer inspected the completed correction after reporting R6/R7:

- `corpus_content` keeps twenty 4-KiB documents per owner, with exactly one
  Revenue match. The maintained regression builds the actual second-turn
  context using the production excerpt/context helpers. The unchanged production
  context bound is preserved.
- Connections are counted only after successful HTTP 200; a readiness Event
  establishes all 100 streams before nominal measurement. Peak/slot observations,
  read count, errors and worker liveness now participate in the PASS predicate.
- `pressure_alive` checks subprocesses and background pressure tasks during
  submission, drainage and immediately before measurement ends. No failed worker
  is silently replaced. The end timestamp is frozen before post-load SQL/journal
  analysis, avoiding an inflated expected-read denominator.

The retained author GREEN `m7-load-pressure-green.txt` has **11 PASS in 0.08s**,
including the context regression and the five pressure-gate negatives. The
reviewer independently ran the current seven status and eleven load unit tests:
**18 PASS, 0 FAIL/ERRORS/SKIPPED in 4.57s**, retained in
`m7-final-global-review-load-corrections.txt`.

**R6 and R7 are closed as code findings.** This does not establish measured load
latency, sustained reference concurrency, resource behavior or an actual campaign
PASS. Those remain results to inspect from the full reference run, alongside the
separate overload/degraded profiles and final cumulative regression.

## SELECT edge closed; delivery and supply-chain follow-up

The author retained `m7-preflight-select-red.txt`: **2 FAIL, 5 deselected in
0.38s**, demonstrating that revoking SELECT(tasks) from either runtime role
previously left administrative preflight ready. The shared helper now checks
the full required table reads in addition to exact INSERT/UPDATE grants. For
`accounting_decisions` it checks only the required `call_id`, `kind`, `revision`
SELECT columns, preserving the existing private evidence/details boundary.
No broad grant was added. **The SELECT-privilege edge is closed** for this
inspected correction. Author evidence `m7-select-transport-green-final.txt`:
**18 PASS in 2.26s**; the reviewer did not rerun the shared database.

The unsuccessful installed-image cumulative run (**790 PASS, 1 FAIL in
279.52s**) remains unsuccessful evidence. The failed physical-send fixture
constructed its 200ms permit before constructing the HTTP client; client/CA
asset initialization could legitimately consume that entire permit and produce
zero sends. The corrected fixture constructs the client first, then samples the
same fresh 200ms permit immediately before its HTTP experiment. Assertions,
physical request/effect counters and the independent already-expired-permit
zero-send test are unchanged. This is a correction to the experiment boundary,
not weakened product expiry behavior. A final complete rerun remains required.

The new actual migration-failure test copies migrations into an isolated test
directory and runs a child Alembic forward revision that creates a table then
calls an intentionally absent function. It requires a real child failure, no
partial table, unchanged real schema revision, closed admission/dispatch and an
explicit compatible preflight afterward. Unlike changing the revision marker
alone, this exercises transactional DDL rollback. Its connected result is still
pending from the primary author; source inspection is not a claimed PASS.

Supply-chain evidence inspected: a maintained locked-certifi regression fails
against version 2026.7.22 (**1 FAIL**) and passes against the lock-only update to
2026.10.23 (**1 PASS**), with hashes retained in uv.lock. Image artifact checks
meaningfully found an actual bundled ensurepip module and an unpatched libssl3
version before their Dockerfile changes. The Dockerfile removes ensurepip and
pins the demonstrated Debian libssl3/openssl patch versions. Final built artifact
and rescan evidence remains pending; the earlier scan is not treated as clean.

**Artifact-check coverage caveat sent to the author:** invoking PATH `python`
checks the copied application's virtual environment. Its `find_spec("pip")`
cannot establish absence of the base interpreter's installer. The earlier real
scan independently lists base pip at
`usr/local/lib/python3.14/site-packages/pip-26.2.1.dist-info/METADATA`; removing
only ensurepip does not remove that base package/scripts. Verify the base
interpreter/files too before claiming the entire runtime excludes installer
tooling. Extra certifi/setuptools observations in the earlier scan include
bundled-SBOM entries, so final actual-file inspection must distinguish retained
packages from stale or non-runtime metadata. This is a verification-claim gap;
no additional exploitability or new product permission bypass is inferred.

Latest independent offline full host-unit rerun after these source corrections:
**258 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED in 7.77s**, retained in
`m7-final-global-review-units-current.txt`. Focused Ruff and format checks pass
for five files (health, delivery, release artifact verifier, actual migration
failure test, supply-chain test); focused mypy passes for the three application/
helper modules. These checks use the already-baked local tokenizer asset and
perform no network, DB or Docker operation.

## Final core evidence and artifact closure

The reviewer inspected the current focused commit chain:
`d0a7eca` (runtime/dependency security), `92d6c07` (retention/recovery core),
`a40fc65` (proxy replacement), `e9781ec` (physical-send fixture boundary),
`d744b39` (encrypted recovery drills), on feature/m7. The approved plan and
pre-existing AGENTS.md edit remain outside these owned changes.

`m7-final-regression.txt` now records the complete installed-image regression:
**802 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED in 278.20s**. It also retains the real
Redis-degraded profile: 100 streams/20 followed tasks, 120 reads over 12.017s,
new creation 503, retained replay 202, control 200, a renewed heartbeat, zero
stream errors and one independently counted physical request/effect with committed
execution. `m7-delivery-restore-races-final.txt` records **2 PASS in 2.11s** for
actual failed forward migration and an observed real-lock reserve race. The race
uses independent connections, observes two blocked reserve transactions, requires
one winner/one revision conflict and replays the winner's stable evidence without
another decision/revision. That final replay demonstrates application idempotence
after an ignored result; it is not a network-level dropped-commit injection.

The release verifier now invokes `/usr/local/bin/python` explicitly as well as
the virtualenv interpreter. Actual base-pip RED was retained before removing
unused base pip/package metadata/scripts. `m7-runtime-artifact-green.txt` confirms
the checks against immutable runtime ID
`sha256:1aad0765df4ef336b4e066ef7f418422957ccc756b2d0725e50654b8886863e4`.
It also checks the actual libssl3 patch version. **The artifact-check edge is
closed**, without mistaking virtualenv isolation for whole-image absence.

The reviewer parsed both final raw Trivy JSON reports independently:

| Final inventory | Result |
|---|---|
| Locked dependency libraries | 0 findings |
| Runtime image Python libraries | 0 findings |
| Entire runtime image | 244 findings: 2 CRITICAL, 59 HIGH, 91 MEDIUM, 89 LOW, 3 UNKNOWN |
| HIGH/CRITICAL records with a scanner-supplied fixed version | 0 |

This is **not a clean-image claim**. `M7-supply-chain-review.md` retains the
unresolved SQLite inventory risk and Debian's minizip scope explanation, raw
records and sources. Lack of a supplied fix is not proof of non-exploitability.
The immutable Compose overlay removes application builds/pulls and requires a
reviewed image identity. No registry publication, signed release, production TLS
deployment or production acceptance of the remaining inventory is inferred.

The final restore driver now samples one common incident before either SQL
restore. `m7-restore-drill-final.txt` reports PASS for both scenarios with the
same request/effect/isolation oracles. Before B: RPO **2.971s**, restore-through-
safe-reopening **4.334s**. Before creation: RPO **6.289s**, restore-through-safe-
reopening **3.397s**. Prior credential isolation is reported separately as
**0.966s**. Off-host delivery remains honestly SKIPPED. Earlier values above
remain historical and are superseded for the final drill by these measurements.

## Pending load evidence and overload coverage

The current overload harness is a coherent queue-burst experiment: after stopping
workers it submits 120 concurrent requests across twenty owners, requires at most
100 acceptances and at least twenty rejections, and compares durable counters to
retained open/executable work. This proves the tested queue-admission boundary,
not the full separate saturation profile. The plan also calls for slow physical
calls through their absolute timeouts and very small shared provider limits;
the inspected overload function does not yet restart those boundaries or change
provider limits. Existing targeted transport/capacity results remain useful but
must not be presented as that combined overload measurement.

One reporting robustness issue was sent to the author before the long run: the
controller should preserve the sanitized nominal result before running overload
and report overload failures as result fields. Otherwise a suspended nominal
task contributes to the open counter but not the queued/running-only executable
count, causing overload's equality assertion to abort the controller before its
public result is written. Unfavorable nominal measurements must remain published.
This is a harness/reporting concern, not a newly discovered durable-task defect.

The exact nominal campaign, remaining overload scope, named gates and final CI
are still being executed/prepared by the primary author. No reference latency,
1000-task completion, final CI or overall M7 completion is claimed before those
results are inspected. No DB, Docker or network operation was performed by this
reviewer during these follow-ups.

## Publication and saturation follow-up during the isolated reference run

Reviewed revision: pushed `5026574`, including the load increment `7397183`.
The reference campaign is being run alone by the primary author; this reviewer
performed only source/evidence reads and did not run host tests, DB, Docker or
other load during measurement. Final benchmark and CI outcomes remain pending.

The maintained unfavorable-result publication regression records
`m7-load-publication-red.txt` (**1 FAIL, 11 PASS**) and
`m7-load-publication-green.txt` (**12 PASS**). The controller writes sanitized
nominal metrics with conservative FAIL status before entering overload; a later
phase exception cannot erase them. **The publication concern is closed**.

The expanded saturation function now waits 61 real seconds for prior provider
windows to age naturally, without truncating call history or resetting counters.
It retains the 120-request queue burst and counts suspended tasks as nonterminal
for the durable open-counter comparison. It also configures 1 RPM/1,200 TPM and
one active call, starts a physical slow-drip model/two workers and checks bounded
exposure and request counts. This adds the previously missing combined timeout/
tiny-provider scope. Its actual outcome remains to be observed.

Two precise harness timing risks were sent to the author before that phase:

1. The slow model and workers are initially spawned together without the nominal
   path's read-only HTTP readiness wait. A worker can consume the sole B permit
   before the model listens, receive connection refusal, retain uncertainty and
   spend the one-RPM window with **zero** independently observed HTTP requests.
   The resulting exact-one-request failure would concern fixture startup, not
   hidden retries or durable accounting. Establish the model boundary before
   workers (and before starting short task deadlines).
2. A fixed nine-second observation after six-second task deadlines leaves only
   approximately three seconds for maintenance, whose ordinary polling interval
   is five seconds before scan/lock time. A remaining expired queued row at that
   instant would not alone prove the plan's recovery bound was violated. Record
   phase timing and use the actual documented recovery margin when interpreting
   an unfavorable result; do not silently change the nominal reference profile.

The operator reported VM disk pressure around 92% with approximately 1.7 GiB
free during this isolated run. The actual resource report/runbook response must
remain attached to its measurements. Neither successful task progress nor a
small current PostgreSQL volume establishes unrestricted production capacity.

## Saturation orchestration correction — timing concerns closed

The author added a maintained ordering regression before changing the overload
function. `m7-load-readiness-red.txt` records **1 FAIL in 0.44s**: workers were
observed before HTTP readiness. `m7-load-readiness-green.txt` records **13 PASS
in 0.42s**, including the twelve existing load-metric cases. The reviewer read
the test and corrected function only; no tests or infrastructure were run while
the nominal campaign was measuring.

The slow model is now spawned and verified through a bounded read-only HTTP 200
before the burst establishes task deadlines or either worker can authorize B.
After the burst and tiny provider configuration, two workers start. Cleanup is
polled within an explicit fifteen-second bound, accommodating the six-second
task deadline and ordinary five-second maintenance cadence. The independent
physical-request assertion remains exactly one, and retained exposure is now
required to equal **1,000 micro-USD**, preserving uncertainty rather than merely
allowing zero. All children are owned by the outer finally block, including
failures during startup/readiness/burst, and receive bounded termination/kill
cleanup. The nominal function/profile was not changed by this correction.

**Both previously reported saturation timing concerns are closed at source/test
review level.** Actual saturation and nominal results still determine the
measured outcome; these pure fixture tests do not substitute for real PostgreSQL,
Redis, worker processes and HTTP effects. The next full regression must include
the new maintained test; earlier 802-case results do not certify the new total.

## First complete reference result — FAIL retained; R8 normal SSE expiry

The first actual complete reference campaign is **FAIL**, not an overall PASS.
The reviewer parsed its raw JSON while retained at `m7-reference-load.json`:

- 1,000 nominal tasks accepted and succeeded; admission p95 **0.078824s**;
- SSE commit-to-observation p95 **0.978558s direct / 0.978656s proxy**;
- 2,120 distinct physical model requests and 1,060 distinct demo effects/writes
  including the separate sixty warmup tasks; no duplicate effect/model send;
- 100 established SSE slots/100 peak, 10,016 reads, 400 stream sessions;
- exactly **300 `sse:transport` errors**, making `reference_pressure` false;
- separate overload PASS: 120 submissions, 100 accepted, 19×429, 1×503,
  one slow physical request, 1,000 micro-USD retained exposure and zero remaining
  executable tasks after bounded cleanup.

**R8 — Medium, normal expiry produces an incomplete chunked response.**
`Stream.__call__` starts the hard lifetime timeout before its scheduled producer
first runs; `_run` then starts its own later clock with the same lifetime. The
outer timeout normally wins, catches TimeoutError and returns without the final
`http.response.body` with `more_body=False`. This mechanism explains why the
first long campaign observed framing errors at normal session closures. It does
not imply cross-owner disclosure, failed durable task completion or a remote
effect retry. The original failed campaign must remain published before rerun;
framing exceptions must not be reclassified as successful normal EOFs.

The primary author added maintained behavioral tests before changing code:
`m7-sse-expiry-asgi-red.txt` has **2 FAIL, 8 deselected in 0.62s** for absent
terminal ASGI frames, and `m7-sse-expiry-tcp-red.txt` has **1 FAIL in 0.90s**
with real HTTPX `incomplete chunked read`. The real TCP fixture runs the unchanged
production Stream and requires three clean read-to-EOF reconnects and three
connection-ticket releases, with no database involved.

The minimal correction reserves `min(write_timeout, lifetime/2)` for closure
inside the same hard lifetime. It lets ordinary production finish and send the
existing empty terminal frame before the outer deadline. Data/authentication
bounds, independent write timeout, ticket release ordering and stalled-source/
disconnect/backpressure cancellation remain intact; no stale data or extended
lifetime is authorized. `m7-sse-expiry-green.txt`: **11 PASS in 1.51s**.
**R8 is closed at source/TDD review level.** The complete post-fix regression and
unchanged long reference campaign still need rerunning; prior successful suites
do not cover this new correction.

## R9 — Low — Legacy fixed HTTP transports still read implicit TLS environment

**Location:** `transport.py` and `report_transport.py` explicit
`AsyncHTTPTransport(retries=0)` construction. Their AsyncClient has
`trust_env=False`, but supplying a transport leaves that transport's own default
environment setting intact. The OpenAI transport already explicitly disables
environment use at both layers and is unaffected.

The reviewer independently reproduced this without any network or database call:
set `SSL_CERT_FILE=/nonexistent/synthetic-ca.pem`, then construct the actual
SimulationTransport for loopback HTTP or ReportTransport for fixed demo HTTP.
Both constructors raise **FileNotFoundError** while constructing the TLS context,
before any request, despite using HTTP-only fixed destinations. This violates
the explicit implicit-network-configuration boundary and can prevent worker
startup. No model-selected URL/proxy/credential forwarding or live OpenAI exposure
was demonstrated; scope/severity remain limited to configuration isolation.

Required correction: retain zero retries, fixed destinations and redirects off,
but explicitly disable environment use in those supplied transports as well.
The author is preparing a maintained real-SSLContext/valid synthetic certificate
regression before application edits. Its RED/GREEN, full regression and physical
request-count preservation remain pending at this update. No implementation or
test mutation was performed by this reviewer.

## R9 closure — explicit transport environment isolation

The author added the maintained regression before changing either transport.
It constructs a real SSLContext from a valid synthetic single-root certificate
bundle and compares the actual HTTPX transport context to the explicit installed
certifi context. This is stronger behavioral evidence than inspecting a flag or
relying on an invalid-file setup failure. `m7-transport-environment-red.txt`:
**2 FAIL in 0.36s**, showing one environment root instead of the configured
118 roots; `m7-transport-environment-green.txt`: **2 PASS in 0.36s**.

The application correction adds only `trust_env=False` to the supplied
AsyncHTTPTransport in SimulationTransport and ReportTransport. Zero retries,
redirect refusal, fixed HTTP destinations, credentials, deadlines and byte bounds
are unchanged. OpenAI already disabled environment use in its own transport and
client and was not affected. **R9 is closed**, with the real risk scoped to
implicit configuration/constructor behavior in legacy fixed HTTP adapters; no
live-provider TLS interception or secret forwarding was established.

The reviewer also inspected the asynchronous TCP-fixture refactor: it retains
three real reconnects, exact response bytes and opened/released ticket counts,
with bounded child startup/cleanup. The author's complete post-correction host
unit record `m7-release-host-units.txt` has **265 PASS, 0 FAIL/ERRORS/SKIPPED in
8.78s**. Reported static scope is 154 source files with mypy/Ruff PASS. These
results do not replace the pending rebuilt-image full PostgreSQL/Redis regression,
exact reference rerun or CI on the corrected final commit; earlier green CI runs
predate these latest source changes.

## Post-correction full run — unsuccessful contaminated-state attempt retained

`m7-release-regression.txt` records **808 PASS, 1 FAIL, 0 ERRORS, 0 SKIPPED in
285.70s**. It is an unsuccessful complete run, not a passing gate. Its only
failure is the first integration test,
`test_concurrent_repeated_evidence_settles_once_and_audits`, at the unscoped
`SELECT state FROM tasks` scalar expecting suspended but obtaining cancelled.

The reviewer independently inspected that test, its `pending()` setup, the
`command_owner` fixture and the preceding successful Compose smoke source/log:

- `pending()` asserts claim, B authorization and unknown completion succeed;
  concurrent reconciliation targets its stable external call identity. The
  preceding accounting-decision/evidence/cost assertions passed.
- The final state assertion does not filter by this task, owner or ordering. It
  therefore assumes the entire disposable task table is otherwise empty.
- `smoke_compose.py` intentionally accepts and cancels durable tasks, verifies
  their retained responses, closes admission and revokes its key; it does not
  truncate those task rows afterward.
- The command-owner fixture truncates transient task/call/accounting tables only
  after the yielded test. Thus the first test can see older smoke rows, whereas
  that test's teardown clears them for subsequent cases even after failure.

These mechanisms, the unscoped assertion and the absence of any other failure
strongly support disposable-stack contamination rather than a new settlement
transition defect. Pre-failure task row IDs were not retained, so the exact row
selected cannot be forensically proven from these logs alone. The original
failure is not discarded, assertions were not weakened and application/tests
were unchanged before the next run. `m7-release-before-clean-rerun.txt` records
an actual task count of **0** before the unfiltered rerun, which is still pending
at this update.

The same unsuccessful run separately records a successful F19 profile: 100
streams, 120 reads over 12.018s, read p95 0.0143s, new creation 503, replay 202,
control 200, a renewed heartbeat, zero stream errors and one physical request/
effect with committed execution. That focused evidence does not make the full
808/1 result PASS. No DB, Docker or application mutation was performed by the
reviewer to establish this attribution.

## Clean rebuilt cumulative rerun — PASS and current artifact identity

The unchanged complete rerun, after the recorded zero-task precondition, is now
observed in `m7-release-regression-clean.txt`: **809 PASS, 0 FAIL, 0 ERRORS,
0 SKIPPED in 280.48s**. No application/test change was used to make the preceding
808/1 attempt pass. This result supports the disposable-state attribution above,
while preserving that unsuccessful attempt independently. The same successful
run records F19 with 100 streams, 120 reads over 11.994s, read p95 **0.0153s**,
creation 503/replay 202/control 200, renewed heartbeat, zero stream errors and one
physical request/effect. This rebuilt suite includes the corrected normal SSE
expiry, real TCP reconnect and actual SSLContext environment regressions.

The current release manifest is `m7-release-manifest.json`, recording source
commit `8050f452d461ba2ae2f5da08d30a07e0f2b53eb0`, schema
`0011_recovery_authority`, linux/arm64 and local-only distribution. It supersedes
the earlier image identity in this report:

```text
OCI manifest  sha256:599c377eff3414eeebb334cfdddbf7e3ce9242e37294b62ccf9e92067b61f60f
Configuration sha256:37f2c958ae0543766e6454733f59c4d02ee001a7c041cb87419bd8642d5915e9
Saved archive 71498943112f907238b023bc137ea23cd6842c8f25743ef33aac8e4856c7b01c
```

The reviewer independently read only the archive's small OCI metadata/config
blobs, without extracting layers or invoking Docker. Its index names the recorded
OCI manifest; the SHA-256 of that manifest blob matches; its configuration pointer
names the recorded configuration; that configuration blob's SHA-256 matches.
The final Trivy report's Metadata.ImageID is exactly this configuration digest.
The different Docker/OCI and scanner configuration identifiers therefore identify
the same archived artifact; they are not silently treated as equal strings.
The full archive checksum/source-commit provenance remains the recorded build
evidence, not an independently reproduced or signed-source claim by this review.

The reviewer parsed the new release image scan independently: Python libraries
**0 findings**; raw OS **244 = 2 CRITICAL, 59 HIGH, 91 MEDIUM, 89 LOW, 3 UNKNOWN**.
Its dependency scan also reports zero findings. The published unresolved
base-image limitations still apply; no clean-image/production certification is
made. The supply-chain report now points to the current manifest/archive and
explains the configuration identity relationship.

Named gates are currently running sequentially; the corrected exact reference
campaign and final commit CI are still pending. Overall M7 approval/completion
remains pending those observed results. The reviewer ran no DB/Docker, network
or heavy host checks during these source/artifact inspections.

## CI recovery observation — failed run retained, diagnosis not overstated

The push CI run `40631990690` on source revision `8050f45` records **543
integration PASS, 1 FAIL in 330.56s**. Its only failure is F19's final, single
`after-recovery` POST returning 503 instead of 202. The recorded outage profile
itself passed: 100 streams, 120 reads, continued control/replay/heartbeat and one
independently counted execution/effect; its read p95 was 0.1447s. This focused
profile does not make that CI run successful. The separate PR run remains pending
at this review update.

The reviewer inspected the fault coordinator, F19 recovery sequence, API health,
creation limiter, notification/polling paths and the pinned redis-py pool source.
The coordinator's marker means Docker start completed. The subsequent existing
five-second loop probes a separate Redis client owned by the test, whereas the
final POST uses the API process's distinct bounded pool. That server ping alone
does not establish the API's submission-readiness contract. A stale API connection
or overlapping recovery operation is plausible; redis-py already checks/reconnects
stale idle connections, so that explanation is not proven merely by reading the
failure. The response body was not retained at the assertion, and generic 503
can also arise from DB/control/lock failures. No specific new application defect
is therefore asserted without additional evidence.

A justified fixture correction is a **read-only API readiness barrier within the
same existing five-second recovery budget**, checking actual API
`submission_available=true` (status 200 alone is insufficient because Redis-
degraded readiness intentionally remains 200), then making the original business
POST exactly once and still requiring 202. No POST retry, added wait budget or
weakened outage assertion is approved. A maintained behavioral RED must establish
that a healthy independent Redis ping cannot bypass a still-degraded API status;
the helper must await the actual API and fail within the unchanged bound if it
never recovers. If the single POST still returns 503 after that observed readiness,
retain safe response diagnostics and investigate rather than assuming the barrier
proved a product fix. No DB, Docker or heavy tests were run by this reviewer.

## Recovery observation boundary correction — reviewed without weakening F19

The author first extracted the existing independent Redis-ping behavior into a
test-support helper without changing it; the retained refactor check passed
once. Two maintained behavioral regressions then failed meaningfully:
`m7-degraded-api-readiness-red.txt` — **2 FAIL, 1 PASS in 0.12s**. A successful
independent ping caused zero API probes/false early success, and an API that
remained unavailable did not cause the expected bounded failure.

The corrected `wait_for_recovery` wraps both independent ping and read-only
`GET /health/ready` probes in the **same five-second absolute timeout**. It
requires HTTP 200 plus strict `redis is True` and `submission_available is True`,
without issuing any command. The original F19 POST remains outside that helper,
is issued exactly once with its original key/body, and still requires 202. All
outage, stream, read, control, heartbeat and independent-effect assertions remain
unchanged. Focused GREEN: **3 PASS in 0.33s**.

The reviewer inspected source/diff and evidence only. This corrects the fixture's
observation boundary and adds negative coverage; it is not claimed to prove a
particular production cause of the earlier CI 503 or to make every subsequent
creation succeed. The next complete regression and final CI must exercise the
corrected helper and retain any remaining failure without retrying the POST.

`m7-release-gate-status.json` separately records 22 command exit codes of zero on
the earlier fixture revision. Exit code zero is not a universal PASS: the paid
`smoke-openai` command explicitly returns **SKIPPED** without authorization.
Those records therefore represent 21 executed gates with successful exits plus
one paid-smoke SKIPPED, with additional off-host/production substeps explicitly
unexecuted. The author's post-helper full regression/gate rerun, unchanged exact
reference campaign and corrected-source CI are still pending at this update.

## Latest post-barrier cumulative verification — 812 PASS

The reviewer inspected `m7-final-regression-after-readiness.txt` on pushed source
`e05e676` after the read-only F19 observation-boundary correction:
**812 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED in 286.89s**. This is the latest complete
regression; the earlier 802/809 successful runs and the retained 808/1 attempt
are historical results, not substitutes for this final source revision.

Its real degraded profile records 100 SSE connections, 120 reads over exactly
12.000s, read p95 **0.0144s**, creation 503/replay 202/control 200, heartbeat
renewal, zero stream errors and one independent physical request/effect with
committed execution. The F19 recovery assertion remains a single POST requiring
202 after the bounded read-only API barrier. No business retry or application
behavior change was used to obtain this result.

Named gates are being rerun sequentially on frozen source before the isolated
exact reference campaign. The primary author reports the old source's PR CI
`40631994731` succeeded while push CI `40631990690` failed as retained above;
neither status is final-source CI evidence. New source runs `40633167358` and
`40633162409` are pending at this update. Overall M7 approval remains pending
the corrected reference result, final gates and observed final-source CI. No
heavy host check, DB or Docker operation was performed by this reviewer.

## Final scope check — demo retention is not certified by application purge

The reviewer rechecked approved plan §§5.1–5.2, its accepted review response,
`demo.py`, `infra/demo-schema.sql`, the README and maintained F20 tests. The
earlier retention approval explicitly covered application migrations 0007/0008
and their purge boundary; it did not certify lifecycle operations in the separate
demo PostgreSQL service.

Confirmed current facts:

- Demo `report_effects` keeps the full serialized report payload (including
  cited source quotations), a minimal proof response and first-receipt timestamp.
  Save/lookup do not consult that timestamp or implement expiry/redaction.
  The service role has SELECT/INSERT, with no bounded administrative purge path.
- Application content purge does not reach that independent remote database.
  It correctly retains local call/accounting identities; remote effects must not
  disappear through application rollback or an indiscriminate local cascade.
- The approved policy distinguishes sensitive-content retention, content-free
  operation tombstones and demo deduplication for 90 days from first receipt.
  The accepted review response does not require indefinite sensitive text.
  The source-revocation caveat for derived reports is not, by itself, a general
  waiver of every scheduled-retention statement.
- No maintained test was found that actually expires/removes the remote demo
  record and proves that an old local operation is not resent beyond its
  task-deadline/24-hour bound. The unchanged production source does contain
  deadline/first-authorization checks; source inspection is not that boundary
  test's execution evidence.

Interpretation must remain precise. A 90-day deduplication guarantee need not
mean destructive deletion exactly on day 90; retaining minimal IDs/hash/effect
proof longer is conservatively stronger for duplicate prevention. Indefinitely
retaining full quoted report bodies is a separate privacy/resource policy. The
remote service also lacks the task's terminal timestamp, so this review does not
invent a new remote 30-day clock or administrative role design under the guise
of a bug fix. The source-copy caveat prevents a universal remote-erasure promise.

At minimum, close the independent F20 expired-remote-record boundary coverage
and accurately disclose which demo payload lifecycle is not implemented.
Whether the remote full-payload policy is an accepted remaining limitation or
requires implementation must be resolved explicitly against the approved scope;
it must not be silently relabeled as an approved indefinite-retention tradeoff.
The README's historical statement that the full 90-day workflow "belongs to M7"
must not coexist with an unqualified claim that every such operation is complete.
No new implementation, test, DB or Docker operation was performed for this scope
check, and no demonstrated replay/data-loss exploit is asserted from it alone.

### Reviewed M7 remediation design for the demo boundary

The author's proposed operator-only `demo-retention` command is consistent with
the approved M7 rather than a new architectural feature: it applies the existing
**task terminal time plus 30 days**, supplied in a trusted, explicitly evidenced
owner/operation/hash manifest, instead of inventing a remote first-receipt clock.
A separate operator DB credential and narrowly scoped no-login privilege group
keep this authority out of API/worker credentials. The proposed action clears
only the full payload, while retaining operation/effect identities, hash, minimal
proof and original first-receipt time for at least the supported deduplication
window. It does not invent automatic deletion exactly at day 90.

Design-review conditions returned to the author: sample fresh demo DB time after
the row lock; reject future/naive times and mismatched owner/operation/hash;
commit matching private audit and redaction atomically; bind stable evidence
identity/content and reread after uncertain commit acknowledgement. The narrow
UPDATE privilege must not become arbitrary report rewriting: use an enforceable
guard or bounded definer path for empty redaction with matching retention audit.
Concurrent same-key/same-hash save must still return the original proof, changed
content must remain 409, and accounting/local uncertain exposure must be untouched.
Tests for expired remote records may use privileged test-only deletion, without
granting remote-effect DELETE to the service. A claim of retained provision should
include actual nonzero uncertainty, not merely a retained zero-valued demo row.

This is approval of the proposed scope/design only. No such implementation,
privilege/migration result, RED/GREEN or final regression is certified by this
paragraph. The author is preparing tests before application changes; the final
demo implementation and complete rebuilt delivery evidence remain pending.

### Demo erasure implementation review — initial connected evidence

The reviewer inspected `demo_retention.py`, the administrative CLI case,
`infra/demo-retention.sql`, Compose secret/init changes and the maintained real
demo tests. The implementation follows the reviewed scope: strict explicit UTC
terminal evidence, separate `DEMO_RETENTION_DATABASE_URL`, fully qualified SQL
relations with fixed `pg_catalog` search path, and a no-login operator group
granted bounded function execution/private audit reads only. API/worker retain
no erasure credential or raw payload UPDATE; the demo service retains its existing
SELECT/INSERT boundary. The test-only profile receives a root fixture secret to
exercise denied roles and controlled external-record expiry.

The SQL function validates exact evidence keys/types, IDs/hash/proof/reason, locks
the exact owner/operation row, checks its retained payload hash and stable private
decision fingerprint, then samples fresh DB time before the 30-day decision.
Audit insertion and empty-payload redaction are in one commit. A trigger permits
only empty redaction with qualifying audit while keeping all other effect fields
unchanged; audit updates/deletes are denied. Existing effect ID/hash/minimal
response/first receipt survive. Identical evidence replays the original decision;
changed content conflicts. The terminal/proof manifest is an explicit trusted
operator assertion/export, not claimed automatic cryptographic verification of
the platform's external records.

Meaningful `m7-demo-retention-red.txt`: **1 FAIL, 7 deselected in 0.39s**, where
the actual sensitive remote payload remained. Initial concurrency-observer
failures were fixture query mistakes, not product RED. The corrected observer
still requires two actual blocked transactions, matching the outer function
visible in pg_stat_activity rather than an inner table statement.
`m7-demo-retention-green-final.txt`: **12 PASS in 25.73s**, including exact real
HTTP resend/conflict and limited-role checks.

The expanded expired-record/commit-ack run currently records **14 PASS, 1 FAIL
in 30.30s** (`m7-demo-retention-f20-green.txt`), not a completed PASS. The horizon
fixture tries to age immutable `first_authorized_at` through an ordinary UPDATE
and correctly encounters the production guard. This is a fixture setup failure,
not a reason to weaken that guard. The source of the expanded test independently
deletes only its own >90-day remote record, asserts no new physical request under
deadline/horizon rejection, retains nonzero local money/token uncertainty and
injects actual server commit followed by local acknowledgement loss for the new
retention decision. Final connected evidence remains pending.

One source-level boundary question was returned before closure: the separate GC
engine does not set UTC like the main application engine, while the function and
guard use `clock_timestamp() - interval '30 days'`. Require a real non-UTC/DST
boundary check or fixed UTC/elapsed-time SQL semantics rather than assuming that
an explicit UTC evidence value also fixes the SQL session's calendar arithmetic.
No DB/Docker/heavy tests were run by this reviewer for this question. Final demo
approval, operator migration/runbook and rebuilt cumulative gates remain pending.

### Demo GC/F20/UTC code closure after final connected verification

The expanded F20 run now records **15 PASS in 30.18s** in
`m7-demo-retention-f20-green-final.txt`; its earlier 14/1 guard-protected fixture
attempt remains retained. Only the controlled test fixture temporarily disables
and reenables the relevant USER guard inside its migrator transaction to age
the first-authorization clock. No production constraint or privilege is weakened.

The returned timezone question was confirmed with a meaningful real PostgreSQL
behavioral RED: `m7-demo-retention-dst-red.txt` has **1 FAIL, 15 deselected in
0.46s**. The fixture sets a private-session POSIX timezone with a recent DST
transition, first verifies approximately **3,600 seconds** between calendar-day
and elapsed-720-hour cutoffs, then shows that a qualifying UTC terminal time is
incorrectly refused. It does not change the system clock. The correction sets
TimeZone UTC on the bounded definer function, legal-redaction/audit guards and
the administrator connection. Therefore even a direct permitted operator call
cannot inherit different calendar arithmetic. `m7-demo-retention-dst-green.txt`:
**16 PASS, 0 FAIL/ERRORS/SKIPPED in 30.29s**.

The reviewer inspected all sixteen cases and final ACL/SQL/Python source. They
cover exact owner/operation/hash, bad/future/young/non-UTC evidence, strict fields,
private immutable audit, denied service-role authority, bounded operator function
authority, two independently observed row-lock waiters, real HTTP exact resend
and content conflict after erasure, and retained first receipt/effect/hash/proof.
The expired-remote-record cases remove only their own aged independent record
under test-only root authority, then require no new HTTP invocation under both
deadline and 24-hour rejection while preserving **nonzero local money and token
exposure**. Actual server commit followed by injected local acknowledgement loss
is separately verified through one durable audit record and stable decision
reread. No fake B/call is added by retention and no new business send is made.

**The demo payload-lifecycle and missing F20 boundary concerns are closed at
implementation/TDD review level; no remaining material code finding was found
in this increment.** Minimal remote dedup proof is retained at least 90 days
(conservatively longer), while sensitive payload erasure follows trusted platform
termination evidence. No automatic exact-day-90 effect deletion policy was
invented. Operator evidence is still trusted input/export, not independently
verified terminal truth inside the remote database.

Completion remains conditional on the explicit existing-demo forward SQL
migration/limited-login/evidence-export runbook, rebuilt runtime and test images,
new artifact identity/scans, complete **828-case** regression, named gates,
unchanged exact reference campaign and current CI. Bootstrap mounting of the SQL
only handles a fresh demo volume; it does not migrate an existing one. The earlier
812-case and runtime599c results remain historical and cannot certify this new
application/independent-schema increment. The reviewer performed no DB, Docker
or heavy tests during this closure review.

### Current GC runbook and artifact provenance — reviewed

Reviewed pushed GC commit: `c4e129735dc4501af1d0a64462399b380120e748`.
For final finding tracking, **R10** denotes the demo sensitive-payload/F20 scope
gap and **R11** the demonstrated caller-timezone/DST cutoff defect. Both are
closed at code/TDD review level by the sixteen-case connected result above.

`docs/runbooks/demo-retention.md` accurately separates application and remote
redaction. It explicitly applies the compatible SQL to an existing independent
database (fresh-volume init is not presented as migration), closes/drains sends,
backs up and stops on error. It provisions an identified separate LOGIN with
database CONNECT and only the no-login function/audit privilege group, using
interactive password entry. It does not give API/worker/demo HTTP services the
operator credential or raw UPDATE/DELETE. The terminal-evidence export joins
retained platform task termination, operation key and exact demo-call hash without
source bodies; it requires private provenance/one hash and correctly explains
that a SHA-256 reference is not independent authentication. Stable CLI evidence,
uncertain-result reread, no business resend and bounded manual scheduling are
documented. No new terminal clock or automatic day-90 deletion policy is implied.
The reviewer found no remaining material runbook/privilege conflict.

The new current `m7-release-manifest.json` records:

```text
OCI manifest  sha256:d8693ab05363959e257f3a0f7ad73f8a622f2c8a3fb4b45a4ffea6324b8a8007
Configuration sha256:12b70c6f18c88b666cff47c3d7917e9bf0b71ff865d443af4bf04d6b36f8feb0
Saved archive a9c32af73c9a0ed402d0f87aad9809d650c1a29d1296fc3b9eaa1a6b3341bbfb
Source        c4e129735dc4501af1d0a64462399b380120e748
Demo addition infra/demo-retention.sql
```

The reviewer again independently verified the small OCI index/manifest/config
blobs and their SHA-256 relationships against the final Trivy configuration ID,
without extracting layers or invoking Docker. The configuration digest maps to
this OCI manifest and the scan; the earlier runtime599c identity is historical.
The current raw scan remains **0 Python-library findings**, **244 OS findings
(2 CRITICAL, 59 HIGH, 91 MEDIUM, 89 LOW, 3 UNKNOWN)**. No clean-image, signed
provenance, registry publication or production deployment is claimed.

The primary author has rebuilt frozen runtime/test images and is executing the
current **828-case** cumulative suite and named gates. Those results and the
unchanged exact reference/overload campaign plus final CI remain pending. The
earlier 812-case source is the last *observed completed* full regression, and
does not certify this newer GC application/schema increment. No heavy host test,
DB, Docker or network operation was performed by the reviewer for this update.

## Current installed-image cumulative result: 828 tests passed

The reviewer inspected `m7-final-regression-after-demo-gc.txt` on the frozen
application source `c4e129735dc4501af1d0a64462399b380120e748`:
**828 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED in 315.86 seconds**. This is now the
latest complete regression, including the sixteen new independent-demo retention
and F20 cases. Earlier 812-case and 809-case results remain historical evidence.

The same run records the real Redis-degraded profile with **100 SSE streams**,
**120 reads over 12.019 seconds**, read p95 **0.0291 seconds**, a renewed
heartbeat, zero stream errors and one independently counted physical request and
effect. New admission returns 503 during the outage, retained replay returns 202,
control returns 200 and durable execution commits. The recovered business command
remains a single POST after the bounded read-only API barrier.

The reviewer independently checked the refreshed documentation metadata:

- The SHA-256 of `docs/compatibility.json` matches the current release manifest.
- The SHA-256 of `infra/demo-retention.sql` matches the manifest's demo addition.
- The documented schema revision agrees with the actual health constant
  `0011_recovery_authority`.
- The analysis version 1 definition equals the source's pinned definition, and
  the release manifest and compatibility document contain matching definitions.

One documentation precision point was returned to the author: the analysis
version 2 `provider` value is a descriptive requirement for an exact immutable
ProviderProfile, rather than an actual configured profile dictionary. It should
be labeled a parameterized compatibility template or accompanied by a clearly
identified configured synthetic profile; it must not be presented as proof that
a production provider project/model/tariff was provisioned. This is a metadata
labeling issue, with no requested application change or paid call.

The current OCI manifest/configuration/archive identity remains the reviewed
d8693ab/12b70c6 artifact above. The author is running all named gates sequentially
on these same images. **Overall review remains pending** until those gates, the
unchanged exact reference campaign and final CI are observed. No heavy test,
database or Docker operation was performed by the reviewer for this update.

## CI GC contention observation — preserve the failure, isolate startup

The primary author reports both c4e1297 CI runs (`40635790442` and `40635788236`)
failed only the new demo-GC concurrency observer. The retained full log
`m7-demo-gc-ci-failure.txt` records **559 integration tests passed and 1 failed
in 372.37 seconds**. The failing condition is the observer's five-second timeout
while waiting to see two blocked function calls. It is not a failed payload,
audit, duplicate-effect or SQL privilege assertion. The successful local sixteen-
case targeted and 828-case full runs remain separate evidence and do not turn
those CI runs into PASS.

The reviewer inspected the actual test and production handler. The test acquires
the root row lock before launching two full CLI subprocesses; their import/startup
time is part of the observer's five seconds, while the production handler keeps
its four-second lock timeout. This creates a plausible scheduling window in
which both actors never overlap as blocked waiters. The discarded child stderr
prevents establishing the exact cause from the retained log, so no new application
defect or definitive startup explanation is asserted.

The proposed harness-only correction is acceptable: start independent trusted
actors, pre-import the actual production erasure handler and validate private
evidence, receive flushed readiness from both, then acquire the blocker and
release both through an explicit stdin barrier into the unchanged handler.
Observe the same two actual `pg_blocking_pids` waiters within the same five seconds,
release the root lock and require both retained True results and one audit record.
Bound readiness separately, clean up both children on every failure and retain
sanitized stderr/return-code diagnostics. Other tests retain end-to-end CLI
coverage. No production lock/statement timeout or assertion reduction is approved.

This paragraph initially approved the test-design correction only. The unchanged
maintained contention test's observed CI failures provide the fixture regression
evidence; no additional mock test mirroring the harness or retrospective
application RED is required. Final connected GREEN, complete regression, named
gates and current CI remain required. The reviewer performed no application/test/DB/Docker mutation
or heavy execution during this diagnosis; the runtime artifact need not change
for a strictly test-only correction.

### Actual preloaded actor correction — source reviewed

The reviewer inspected `tests/support/demo_gc_actor.py` and the actual modified
integration test. Two independent child processes import the real production
handler, read/validate their own private evidence and flush a readiness line.
Only after both readiness lines, within a separate ten-second setup bound, does
the parent acquire the same real row lock. It then releases both actors through
stdin into the unchanged `erase_demo_content` function. The same five-second
observer must still see two actual blocked PostgreSQL waiters; after releasing
the blocker, both actors must return True and exactly one immutable audit row
must exist. Production SQL lock and statement timeouts are not changed, and other
maintained cases still exercise the end-to-end CLI.

`m7-demo-gc-actor-barrier-green.txt` records the connected contention case passing
in **3.55 seconds**. A later actor-diagnostic Python syntax mistake caused a
separate **15 passed / 1 failed** setup attempt; it is explicitly not product RED
or evidence that retention failed. The syntax error was corrected before final
verification. The actor now reports only a sanitized exception class on failure,
and parent result assertions include return codes. A nonblocking diagnostic note
was returned: early EOF before the ready line currently shows an empty line and
its cleanup discards stderr; collecting the sanitized class/return code there
would improve future setup diagnosis without changing the experiment.

The actual fixture correction is approved. The two existing CI failures remain
unfavorable harness evidence with their precise underlying startup cause unproven.
No retrospective application RED, new application feature, SQL/privilege change
or runtime-image change is inferred. Final actor target/full-suite/gate/CI results
and the exact long campaign remain pending. The reviewer ran no DB, Docker or
heavy test during this source review; R10/R11 implementation approvals remain
valid.

## Latest actor source verification and compatibility labels

The final actor target now records **16 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED in
29.82 seconds** (`m7-demo-gc-actor-full-green-final.txt`). The unfiltered
installed-image run on verification source
`8c00f11f32fa619fef5abfc4236a3a0b0980ffde` records **828 PASS, 0 FAIL, 0 ERRORS,
0 SKIPPED in 318.52 seconds** (`m7-final-regression-after-actor.txt`). This is
the latest complete regression and supersedes the earlier 315.86-second result
as final-source test evidence. Application/independent SQL remains at the reviewed
runtime source c4e1297; the actor correction changes only verification files.

The same run records F19 with 100 streams, 120 reads over 12.009 seconds, read
p95 0.0156 seconds, renewed heartbeat, zero stream errors and one independently
counted physical request/effect. The affected final gate log records successful
lint, typecheck, unit, concurrency and retention exits. Other named gates had
already passed on the identical runtime; the final summary must retain their
actual source/runtime relationships rather than claim that a failed CI passed.

The compatibility-label concern is closed. Both documents now name
`workflow_compatibility_templates`, explicitly describe analysis version 2 as a
parameterized requirement rather than an instantiated provider definition, and
state `configured_production_provider_profile` as NOT_PROVISIONED with live smoke
SKIPPED. The reviewer verified current compatibility/demo-SQL hashes against the
release manifest and equality of those templates. The manifest separates runtime
source c4e1297 from verification source 8c00f11 and records the rebuilt test-image
identity. No production provider profile or registry publication is invented.

Before the exact reference campaign, the author found that the actual Docker
data filesystem was 97% used with about 687 MiB free; an older VM-root probe
showing 17 GiB free did not measure that filesystem. The author stopped only the
six owned services and is growing this Colima profile's data disk from 20 GiB
to 40 GiB without deleting data, preserving 4 vCPU, 8 GiB RAM and the host-backed
SSD class. This resource change must be disclosed with actual before/after data-
filesystem capacity and does not authorize silently changing the task/SSE/read/
worker profile. No final benchmark capacity or performance result is claimed
before the verified-capacity run completes.

The review remains pending the corrected exact reference/overload results and
successful final-source CI. No heavy test, DB, Docker or network operation was
performed by this reviewer during these inspections.

## Focused CI stream establishment — steady pressure versus handshake burst

The primary author reports that source 8c00f11 CI run `40705153980` passed all
560 integration cases, including corrected real GC contention, and later passed
the accounting/provider/SSE gates, but failed the subsequent focused F19 gate
before injecting the Redis outage. `m7-actor-ci-failure.txt` records **3 readiness
unit tests passed and 1 F19 test failed in 26.89 seconds**. The five-second Redis
recovery barrier is not involved in this new failure. The initial twenty-five-
second setup waited for 100 initial stream events; the retained queue shows only
99 readiness events. A subscriber's precise status or exception is hidden by
the parent waiting solely on that queue, so bounded-pool rejection, transport
failure or residual stream cleanup cannot be distinguished from this log. No
specific new application regression is asserted without that evidence.

Approved plan section 7.2 and F19 require **100 concurrently held SSE streams,
five per owner, plus sustained reads during the outage**. They do not specify a
burst of 100 simultaneous initial HTTP handshakes. Request/reconnect storms and
bounded admission are separate overload/resource controls. Establishing those
100 streams sequentially or in bounded batches, still within the original
twenty-five-second setup bound and with one attempt per stream, is therefore
acceptable fixture setup. It must not reduce the measured steady pressure or
retry a refused handshake until it appears successful.

The proposed post-benchmark correction must retain all 100 live streams before
the fault, the 101st-stream rejection assertion, the 100-connection metrics,
twenty followed tasks, sustained reads, current authorization, control/replay,
heartbeat and independent physical effect. It should propagate a failed
subscriber immediately with only its index/status/sanitized exception class,
instead of concealing it behind a queue timeout. This improves diagnosis while
preserving the failed result if a single handshake still fails.

The author is keeping source/tests/scripts frozen until the exact reference
campaign finishes. This review approves the proposed setup boundary only, not
an unexecuted correction or final CI result. The failed focused gate and the
separate successful full integration run remain distinct evidence. No heavy
test, DB, Docker, application or fixture mutation was performed by the reviewer.

### Concrete steady-stream setup patch — approved for after the benchmark

The reviewer read the prepared replacement outside application/test source at
`.secrets/m7-tools/test_degraded_ready.py` and compared its complete diff with
the maintained F19 test. No source/tests/scripts mutation was made during the
running reference campaign. The patch keeps the same global 25-second setup
timeout, makes exactly one handshake attempt per stream and retains every prior
connection while establishing the next. It races each new subscription against
its initial event, propagates an explicit safe stream index/status or HTTP
exception class, cancels/gathers the readiness waiter on all paths and requires
all 100 subscription tasks to be live before fault injection.

All original connection-cap, outage, read, control, heartbeat, recovery and
independent physical-request/effect assertions are unchanged. There is no POST
retry, stream retry, production change or exact-load-harness change. This is an
approved implementation of the steady-profile setup boundary above. A small
nonblocking diagnostic limitation remains: an older already-ready stream failing
during setup is caught by the final live-task assertion rather than immediately
surfaced with its exception. The original missing-initial-event CI case now has
direct status/exception propagation.

The existing maintained CI startup failure is the unfavorable harness evidence;
no extra mock test that merely mirrors this control flow or retrospective
application RED is required. Apply only after the isolated campaign finishes,
then execute the real F19 target, complete regression, affected gates and current
CI. Benchmark evidence remains tied to its actual unchanged runtime/profile and
must not be relabeled as executing a later test-only fixture.

The primary author has observed PR CI run `40705143167` **SUCCESS** on source
8c00f11. The separate push run `40705153980` remains failed at the focused
startup gate, despite its successful full integration stage. Both outcomes stay
visible; the passing PR run does not erase the failed push run or prove the
untested replacement passed CI. Overall closure still awaits the actual corrected
reference result and final post-patch verification evidence.

## Final independent local code and evidence closure

**Verdict: APPROVE for the reviewed code and local evidence.** No unresolved
confirmed material finding remains. Runtime source is
`c4e129735dc4501af1d0a64462399b380120e748`; final verification source is
`4d3fdafcf34e99ec0ea4471043e63863282d8592`. The implementation author applied
the approved F19 setup patch only after the isolated benchmark finished. The
reviewer inspected the actual diff: production behavior and the exact benchmark
harness are unchanged, one attempt per stream and the 25-second setup bound are
preserved, and all 100 streams must be alive before fault injection.

The final rebuilt, unfiltered result is **828 passed, 0 failed, 0 errors and
0 skipped in 313.43 seconds**, retained in
`m7-final-regression-after-sse-startup.txt`. It comprises 268 unit cases and 560
real-service integration cases. Its F19 profile records 100 streams, 120 reads
over 12.009 seconds, read p95 0.0157 seconds, a renewed heartbeat, zero stream
errors and one independent physical request and effect with committed execution.
The focused final F19/readiness gate also records **4 passed in 14.45 seconds**.
The consolidated gate record has 21 executed gates passing and the separate paid
OpenAI smoke explicitly skipped. Overlapping focused counts are not added to the
unique 828-case total.

The reviewer independently parsed the final public reference JSON. It records
the unchanged approved profile and **PASS**, with every acceptance predicate
true: 1,000 nominal tasks accepted and succeeded, no nominal rejection or stream
error, 100 established connection slots, a peak of 100, 400 sessions and 10,016
reads over 1,001.033 seconds. Creation p95 is **0.083166 seconds**. SSE commit-
to-observation p95 is **0.996918 seconds direct** and **0.997384 seconds through
the proxy**, from 63 and 69 observed samples respectively. These are the observed
followed-task samples, not every event across all tasks or a production SLA.

Independent journals record 2,120 distinct model POSTs and 1,060 demo writes and
effects for the nominal phase plus 60 warmup tasks. The separate overload phase
sent 120 requests: 99 returned 202, 17 returned 429 and 4 returned 503. Its 99
open tasks remained below the 100-task bound. Under the tiny shared provider
limits, exactly one slow physical model POST occurred, 1,000 micro-USD uncertain
exposure remained and no executable task remained after 12.948 seconds. No
provider-window history was truncated or reset.

The reviewer located the privately retained physical model journal by its
recorded SHA-256, independently checked that hash and counted **2,121 lines and
2,121 unique transmission IDs**: 2,120 nominal/warmup POSTs plus the one separate
overload POST. No raw call ID, credential or private path was published. This
corroborates `m7-reference-independent-journal.json` rather than relying only on
application database counts.

The environment report identifies the actual 4-vCPU, 8-GiB Linux VM on macOS
ARM/Lima VZ and its host SSD-backed disk image. Data-disk growth from 20 GiB to
40 GiB occurred before measurement, with no data deletion or workload reduction;
the actual data filesystem then reported 48% used and about 20 GiB free. The
earlier failed campaign and its smaller disk remain disclosed, so this is not a
controlled A/B latency claim. Resource samples are observations rather than
universal peak-memory or production-capacity guarantees.

The release metadata question is closed. The final tests were rebuilt rather
than source-mounted: the current test image is
`sha256:68df107a1c86319926bb9232fc09a8c2be43bebb49586cabef81a6360fdd0ad9`.
The benchmark used verification source 8c00f11 and test image
`sha256:4e6cc07b2b642941f320292d27af8161cd14174c7ac5bac9ccacb8e66adeccc2`.
The manifest separates both identities and sets `verification_uses_source_mounts`
to false. The author retained an actual-image check of the baked F19 source;
the reviewer independently computed the local source hash and confirmed it
matches that recorded value:
`78094e8141b1d39360e870a68ec6e427d912d5e105de13800096dd1f59a07d58`.
Runtime remains the reviewed d8693ab OCI manifest and 12b70c6 configuration, with
the verified compatibility/demo SQL hashes. The parameterized provider template
is explicitly not a provisioned production provider configuration.

Remaining limits are material and remain published: paid OpenAI and real model
quality evaluation were not authorized; off-host backup delivery/scheduling and
production TLS, deployment, operator provisioning and registry publication were
not performed. The runtime Python inventory has no reported library findings,
but the raw OS inventory still contains 244 records, including 2 critical and 59
high records requiring operator triage. No clean-image or general exploitability-
absence claim is made. Restoration evidence concerns small synthetic local
archives and measured safe reopening, not zero loss or production RTO.

The final-source GitHub CI result is the remaining handoff check. The successful
old-source PR run, failed push runs and corrected local results remain separately
identified; no earlier result is substituted for that final head. The primary
author must observe and record final CI before declaring delivery complete. No
merge, auto-merge or production deployment is approved by this report.
