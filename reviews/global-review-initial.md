# Independent global review — initial M1–M7 assessment

Date: 2026-10-27. **Initial verdict: CHANGES REQUIRED** for the confirmed
authorization freshness defect G1 below. This is an initial review of the
existing M1–M6 baseline and the pure M7 retention policy, not final acceptance of
M7 or a production-readiness certification.

## Scope and independence

Reviewed baseline: local HEAD `b86111386e4c6e46683e9ffac2fc31d24da9bdc7`
(`Merge pull request #6 from WYLLLY/feature/m6`), on `feature/m7`. The initial
working tree contained the pre-existing AGENTS.md edit, untracked retention
policy/tests and policy review/verification artifacts. The reviewer did not
alter application, tests, infrastructure, Git state, credentials or AGENTS.md.
Only this report was written. GitHub merge/CI status was not independently
queried in this review; a local merge commit is not a fresh remote-status check.

The reviewer read AGENTS.md, the approved architecture plan, the architecture
review response, the three requested review skills (`adversarial-review`,
`execution-reliability-review`, `agent-security-review`), M5/M6 review and
verification records, the initial M7 policy report, and the relevant code/tests.
The shared disposable PostgreSQL/Redis environment remains owned by the primary
author. No database, Compose, network, provider, migration or fault test was run
by this reviewer. Isolated local tests and a read-only diagnostic oracle were
used; their scope is stated explicitly.

Initial SHA-256 anchors:

```text
execution.py        e0709b8ad13e55267f81070773f16fcc002e30ebc19cd3e33b21b5697bdbe10b
workflow.py         17c9f8825a870032280511998d9a07673df2226ca358135909ce6bbc4699188e
responses.py        f38f6c05ae3883a7c4ad5ae0f67660e7b5233c587206194e30cd9df3e664b19e
sse.py              1ab3bd71e4300d9e1c94e4f54ccd9280000ab622f3da96824a8c0945eceeb6ee
retention_policy.py 5b5650b4b859c1791e394c425c9f87a42a5b5ec60e761026cc23ced491084691
```

## G1 — High — B can issue a first-send permit after its lease expires

**Location:** `src/task_platform/execution.py`, `Executor.authorize`, initial
ownership check at lines 295–298 and subsequent admission/validation/insertion
at lines 301–388 of the reviewed baseline.

**Contract:** approved plan §§2.4, 3.2–3.3 and F06/F09 require valid ownership,
generation, epoch, unexpired lease and remaining deadline for progression. An
authorization committed while ownership is valid may subsequently produce a
delayed first send; that documented limitation does not authorize creating a
new B after local ownership has already expired.

**Scenario:** a worker enters B shortly before its lease expires. The first
`owns()` returns true under task/step/attempt locks. Reservation work, admission
queries or the awaited `authorization_reason()` source/report validation then
cross the expiry. The heartbeat cannot renew the same locked task while B holds
it. The remainder of B never rechecks ownership and stages an external call,
provision, selected-call identity and event, commits and returns a send permit.
For lease expiry with a still-future task deadline, the HTTP transport's task
deadline calculation does not prevent that first request. A key expiry during
the same validation interval also needs a fresh check.

**Cause:** `owns()` runs only once before potentially delayed B work. The
timestamp for key validity and the permit is also sampled before awaited
authorization validation. In contrast, `complete()` explicitly refreshes
ownership after the cost-row wait and verifies time after workflow validation.

**Independent diagnostic:** a read-only in-memory boundary oracle executed the
actual `Executor.authorize`, with a real UTC clock, an initially valid 5 ms
lease, and an overridden `authorization_reason` that awaited 20 ms. Only
database-bound helpers/session operations were replaced; no application file
was modified. Observed result:

```json
{"permit_after_lease_expiry": true, "ownership_checks": 1,
 "staged_records": ["ExternalCall", "CallCost", "TaskEvent"],
 "elapsed_after_lease_ms": 36}
```

This confirms the algorithmic omission; it is not PostgreSQL durability,
locking, physical-request or maintained-test evidence. The first diagnostic
attempt had an incomplete fake task (`state` missing) and raised AttributeError;
that setup failure was corrected and is not counted as behavioral RED.

**Impact:** a newly issued permit can initiate work after local ownership has
expired, contrary to the intended B boundary. Recovery can then classify the
new call as uncertain and suspend the task despite the sender having just been
authorized by stale ownership. Deadline crossings can create avoidable late
provisions even when the transport subsequently declines to send.

**Existing coverage:** expiry before entering B, waits on the prefix locks,
source revocation while B waits, and C/local-tool validation crossing a deadline
are covered. The inspected tests do not pause B validation after its first
ownership check and before recording the authorization.

**Recommendation:** finish B validation with a fresh under-lock ownership and
time-based permission check before recording the external authorization. Ensure
the outgoing permit uses the corresponding fresh DB/monotonic time pair. Any
post-validation flush/lock boundary must not leave a stale decision accepted.
Preserve the documented ability of a *previously valid committed* B to have a
delayed first send; do not misrepresent a local recheck as remote fencing.

**Required regression:** use real PostgreSQL, shorten an acquired lease to
roughly 150 ms, and delay the source/authorization validation for 300 ms after
the initial ownership check. Require no returned permit, no new external-call
or cost row and zero independent HTTP invocations. Repeat for task deadline
and authorizing-key expiry; assert the normal valid path still passes. Retain
meaningful failing results before correction, then run the full regression.

## Initial F01–F22 inspection map

This table maps code and maintained tests inspected, not newly executed real
integration results. Existing release reports remain historical evidence for
their stated revisions. A green isolated test does not establish the entire row.

| Scenarios | Mechanisms inspected | Initial review assessment |
|---|---|---|
| F01–F02 | `tasks.py`, strict canonicalization, current authentication before retained replay, platform/owner serialization, scoped unique command identity, command/API race tests | Replay precedes mutable new-admission checks; stable command lookup handles ambiguous commit acknowledgement. No new confirmed finding in inspected paths. |
| F03–F05 | Coupled state constraints, cancellation cleanup, `Execution.authorize/maintenance`, external-call/provision identities, crash tests | No external I/O inside application transactions; uncertain B retains exposure and suspends rather than regenerating. G1 limits B ownership correctness. |
| F06–F10 | Lease/generation/epoch checks, protected heartbeat pool, C savepoint/time check, cancellation/recovery/resolution, late settlement and concurrent tests | G1 is open. Local fencing does not promise remote cancellation; terminal/old evidence cannot select a continuation. |
| F11–F13 | `workflow.py`, `corpus.py`, `agent_protocol.py`, pinned handler/context, exact citation checks, fixed demo transport, protocol/security tests | Only server-permitted read tools execute; deterministic report save stays separate. Current source rights and nested owner/version references are checked. Structural validity does not prove truth or injection immunity. |
| F14–F16 | `accounting.py`, `provider_admin.py`, Responses parsing/transport, integer pricing, usage provenance, evidence/revision conflicts and quota tests | Complete usage survives functional rejection; unknown/partial usage conservatively retains provision; admission sums durable records and old exposure. No new confirmed accounting defect found in inspected paths. |
| F17–F19 | `event_reads.py`, `event_hub.py`, transparent ASGI boundary, `sse.py`, notifications, private proxy, metrics/log allowlists, network/fault harness tests | Single-statement projections, owner-first errors, bounded stream writes/auth/lifetime, lossy hints plus SQL polling and protected pools are explicit. Existing M6 independent findings have documented corrections. No new confirmed stream defect found in this pass. |
| F20–F22 | Approved retention/restore/delivery contracts, pure `retention_policy.py`, initial M7 policy tests/report | Real purge, restoration, delivery and cumulative load are still being implemented. They are required verification needs, not findings against an unfinished increment. |

## Independently executed checks

```sh
.venv/bin/pytest tests/unit/test_agent_protocol.py \
  tests/unit/test_command_boundaries.py tests/unit/test_event_cursor.py \
  tests/unit/test_sse_stream.py tests/unit/test_event_hub.py \
  tests/unit/test_operations.py tests/unit/test_telemetry.py \
  tests/unit/test_rate_limits.py tests/unit/test_retention_policy.py -q --tb=short
```

**124 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED in 0.79s.** This explicit selection is
not the full unit or M1–M7 regression suite. No maintained test was changed,
disabled or weakened. The diagnostic oracle above is separate from pytest counts.

```text
.venv/bin/ruff check src tests: PASS
.venv/bin/ruff format --check src tests: PASS, 111 files already formatted
```

No independent mypy, dependency/image scan, full suite, GitHub CI, paid model
smoke or benchmark was executed in this initial pass. No secret values were
read or printed. The paid provider smoke remains outside authorization and is
not substituted by synthetic fixtures.

## Final M7 and global review requirements

These are completion conditions, not additional findings against pending work:

- Review the actual SQL purge service under complete lock order, concurrent late
  settlement, unresolved money **and tokens**, active corpus references, retained
  owner evidence, command/task/operation tombstones and exact-payload deletion.
  Prove that old resend cannot reconstruct a purged payload and that repeated or
  interrupted purge remains idempotent.
- Review real backup/restore with an independent remote invocation/effect journal,
  external dispatch lock, stopped old workers, rotated credentials, new epoch,
  revoked restored API access and quarantine of every nonterminal task including
  tasks with no call rows. Owner resolution must not bypass quarantine. New
  creation/regeneration must enforce absent/stale epoch behavior (428/409).
- Inspect evidence-based reopening, conservative exposure for lost history,
  expired deadlines, immutable terminal results, explicit restored point/loss
  window, and measured RPO/RTO including safe reopening. No recovery of IDs
  absent from a backup or zero data-loss claim is supported.
- Inspect failed migrations, handler manifest/preflight and application rollback;
  dispatch stays closed on failure, without automatic destructive downgrade.
  Validate production TLS/private services, pinned artifacts, encrypted off-host
  backups/retention, effective limits and credential boundaries.
- Run the exact §7.2 cumulative nominal/degraded/overload profiles, or clearly
  publish any unexecuted profile/environment difference and failed targets.
  Separate deterministic synthetic enforcement and semantic quality results
  from any separately authorized real-provider campaign.
- Resolve G1 using TDD and rerun the complete M1–M7 suite, named gates,
  migrations/privileges, static checks, scans and final independent review against
  an identified final revision before claiming M7 complete.

The initial assessment does not authorize implementation changes or a merge;
the primary author must use the user's existing authorization and completion
gates. Final review of the completed M7 increment remains pending.

## G1 correction follow-up — 2026-10-27

**G1 is closed for the inspected correction.** The initial finding and original
CHANGES REQUIRED verdict above remain as historical evidence. This closure is
not a final M7/global-release verdict; the complete corrected-image regression
and unfinished M7 gates remain the primary author's responsibility.

Inspected correction anchors:

```text
execution.py                     14fa94d4ae33a778f2bb3196fece7bf32c7ac88fa15499f7c2177755bacd7ef9
test_dispatch_freshness.py        53f3ff9728e1bfe5ff7e681af36bcde439654c6f90e7bbd20f554ef2f7f5251a
```

The author added real PostgreSQL regressions for lease, deadline and
authorizing-key expiry at each of two boundaries: an awaited authorization
validation and the external-call INSERT flush. Each uses the independent HTTP
simulator journal; if a permit is wrongly returned the test actually exercises
the transport before rejecting that result. The corrected result requires no
permit, zero requests/effects, zero call/cost rows and no call.authorized event.
The source delays are explicit 250 ms waits against a 150 ms authority window;
these tests establish passage of time at the boundary, not observed database
row-lock contention, and do not claim the latter.

The reviewer inspected the retained author evidence:

- `m7-g1-clocks-red.txt`: **8 FAIL in 3.56s**, comprising six behaviorally
  significant late-permit failures and two separate missing M7 clock failures.
  These were not import, connection or infrastructure errors.
- `m7-g1-clocks-green.txt`: **42 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED in 8.75s**,
  covering the new regressions, clock behavior and existing call tests.

`Executor.authorize` now refreshes ownership after awaited authorization
validation, refreshes its DB/monotonic time pair, rechecks time-dependent key
validity and admission, then flushes every staged B write before a final
ownership/key-expiry guard. If the flush consumed authority, the entire B
transaction is explicitly rolled back: no provision, call, selected call,
counter charge or event is retained. Permit elapsed-time accounting remains
conservative because the HTTP adapter deducts time since the refreshed pair.
Lost commit acknowledgements still cannot authorize a blind resend.

As with every local transaction check, time can pass after the last accepted
under-lock decision and before commit or transmission; the correction does not
promise remote fencing or cancellation of a previously committed authorization.
It specifically closes the reproduced validation/flush interval in which the
old implementation accepted already-expired local authority.

Independently executed after correction:

```text
.venv/bin/ruff check src/task_platform/execution.py tests/integration/test_dispatch_freshness.py
PASS
.venv/bin/ruff format --check src/task_platform/execution.py tests/integration/test_dispatch_freshness.py
PASS, 2 files already formatted
```

No PostgreSQL or integration test was rerun by the reviewer, to avoid competing
with the author's active full regression. The retained real-environment RED and
GREEN are inspected primary-author evidence, not independently executed counts.
