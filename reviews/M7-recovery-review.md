# Independent review — initial M7 recovery and delivery increment

Date: 2026-10-27. **Initial verdict: CHANGES REQUIRED.** R1 is a confirmed SQL
logic defect; R2 and R3 are concrete preflight/reopening omissions against the
approved plan. The primary author is implementing and testing this increment;
this report does not certify final M7, a backup/restore exercise or delivery.

## Scope and independence

The reviewer applied `adversarial-review`, `execution-reliability-review` and
`agent-security-review`, using AGENTS.md and approved plan §§3.2, 3.5, 5.2–5.3,
7.3 and F20–F22. Reviewed the new corpus/recovery/authority migrations,
`recovery.py`, `delivery.py`, disaster reserves in `accounting.py`, epoch guard
in `resolution.py`, public system recovery projection, grants/SQL guards and
new integration-test source. Baseline remains merged M6
`b86111386e4c6e46683e9ffac2fc31d24da9bdc7` plus the identified working tree.
The pre-existing AGENTS.md change was preserved. Only this report was written.

No source/test, Git, credential or infrastructure mutation; no database,
Compose, network, paid-provider or shared-stack test was performed by this
reviewer. Code continued to change during review; hashes below identify the
inspected snapshot and findings must be rechecked after corrections.

```text
recovery.py              127f33eba3e962019999b79a9ee070b858799b5f8b4c2a98562a66fc01eef056
delivery.py              d11eacb4e09f3a5d26fd24d760f3ba7662a78e64008b40c211fe09b177cc427c
accounting.py            266230e5bc3c1e88bd497ce8f879118d8fd99a5261e470e7a9522b23630a6324
0009_corpus.py           44e32f6c133268b9e4b70ed78596d8c9abbe902fce9b9c8da37407fd1c339158
0010_recovery.py         1661965e40d2b287710a4f5f98226a983811c4402f1fe8fe1bf3f05ab6a56af7
0011_recovery_authority  bec589b48ad2f2960d46bd8467c6c206542f47c6fe7574eb4a6a24ee4a902f03
```

## Findings

### R1 — High — A later restore reinserts an old terminal quarantine event

**Location:** migration `0010_recovery.py`, `quarantine_restore`, task UPDATE
and following INSERT into `task_events`.

**Scenario:** the first restore contains a nonterminal task whose deadline has
expired. Quarantine marks it `timed_out`, `reason=restore_quarantine`,
`restore_uncertain=true`, increments its sequence and inserts the event. A later
backup contains that retained terminal row. During a second disaster/new epoch,
the task UPDATE skips terminal rows, but the event INSERT selects every task
with the retained uncertainty/reason marker, including the unchanged old row.
Its existing `(task_id,event_sequence)` is inserted again, violating the event
primary key. The entire new quarantine transaction rolls back.

**Impact:** an otherwise valid subsequent restore cannot quarantine current
work or establish the new epoch because of unrelated retained history. The
external dispatch lock must therefore remain closed, delaying safe recovery.
The failed transaction does not partially revoke keys or fabricate success.

**Cause:** the INSERT's selected set is broader than the rows updated by this
incident. Stable-evidence replay within the same epoch does not cover a second
incident with a new epoch.

**Existing coverage:** initial tests cover one restore and exact repeated
evidence for that restore. They do not cover a second incident containing a
prior restore's terminal uncertainty marker.

**Recommendation:** insert events from exactly the task UPDATE's affected rows,
for example using `UPDATE ... RETURNING` in a CTE. Preserve old terminal rows
and their existing event sequence; do not erase uncertainty to avoid collision.

**Required regression:** real PostgreSQL, first restore with an expired task,
then a new-epoch restore retaining that terminal task and another nonterminal
task. Require unchanged prior terminal result/version/sequence, no duplicate
event, current task quarantined once, new epoch committed and keys revoked.

This is confirmed by tracing the SQL predicates and immutable unique key; no
database reproduction was run by the reviewer. The author accepted R1 and is
adding the required real PostgreSQL RED before correction. Closure is pending
inspection of that evidence and final code.

### R2 — Medium — Preflight ignores workflow versions still pinned by tasks

**Location:** `delivery.delivery_phase` preflight/reopen and
`recovery.reopen`, their available-workflow SELECTs.

**Contract:** plan §3.5 requires a handler manifest and preflight of versions
still in use, and F22 requires incompatible deployment/rollback to remain
closed or explicitly suspend affected work without substituting versions.

**Scenario:** a retained nonterminal task pins a workflow version withdrawn
from new admission (`available=false`). Another compatible workflow remains
available. The deployment has no compatible handler for the withdrawn pinned
version. Both preflights inspect only `available=true`, so the unavailable but
still-needed handler is omitted and they can report ready/open. The task is
only discovered and suspended later by a worker claim or explicit release.

**Impact:** the declared preflight compatibility omits accepted work that must
survive deployment or restore. Per-task handler checks still prevent silent
substitution, so this is an incorrect readiness/reopening decision rather than
an observed unauthorized tool execution.

**Cause:** new-admission availability is used as the complete required-handler
set; there is no union with exact workflow/definition versions pinned by
retained nonterminal tasks.

**Existing coverage:** delivery tests insert a missing *available* workflow;
they do not retain a withdrawn pinned version while another available version
is compatible.

**Recommendation and regression:** evaluate the available catalogue together
with every still-required pinned workflow/definition in nonterminal tasks.
Use a real retained task and a deliberately unavailable compatibility check for
its retired version; preflight/reopen must stay closed or explicitly report and
quarantine the affected scope under the approved policy. Never silently select
a replacement handler.

### R3 — Medium — Bounded evidence opens recovery before budget verification

**Location:** `recovery.reopen`.

**Contract:** plan §5.3 step 7 requires verification of budgets/capacities before
reopening new admissions; step 5 requires audited adjustment where restored caps
are insufficiently informative for disaster exposure.

**Scenario:** reserve evidence provides a finite disaster money/token bound
larger than the restored deployment or an affected owner's cap. `revise_reserve`
accepts nonnegative bounded values, correctly records them, and marks the
recovery bounded. `reopen` then checks only the bounded flag and available
workflow compatibility. It never reads provider budget/capacity configuration
or compares current reserves plus known exposure with restored caps before
setting admission and dispatch open.

**Impact:** recovery can claim/open safe acceptance before the plan's mandatory
budget verification. New tasks may be accepted only to remain capacity-blocked
until expiry. **No reservation overspend was observed:** the inspected
`admission_reason` correctly includes disaster reserves and still blocks B.
This distinction is material; the finding is premature reopening, not a bypass
of the accounting invariant.

**Existing coverage:** reserve admission tests cover global/owner/unallocated
money and token bounds at B. The inspected reopen test uses a reserve below the
default caps, not an insufficient restored budget or an audited adjustment.

**Recommendation and regression:** before reopening, validate current durable
known exposure and conservative disaster reserves against the required restored
budget/capacity configuration, or require an explicit audited adjustment that
establishes the allowed reopening scope. Real PostgreSQL regressions should
cover deployment and owner money/token insufficiency and successful reopening
after evidence-backed adjustment. The reserve must not be released or counted
twice just to obtain a green gate.

## Other inspected mechanisms and limits

- Corpus redaction uses an admin-only, fixed-search-path definer function.
  Platform/owner/document locks serialize admission/revocation with deletion;
  a nonterminal pinned reference blocks deletion. The version row/hash remains,
  content is emptied and unreadable, storage is reclaimed once and a fixed audit
  event is written. No new API/worker mutation grant was observed.
- Restore quarantine takes the complete sorted lock order, revokes restored
  API keys and active attempts, releases local slots without releasing exposure,
  marks authorized calls unknown and quarantines tasks even without call rows.
  Deadlines can terminate work while retaining the uncertainty marker.
- Recovery decisions retain stable evidence identities, hashes, kinds and
  revisions. Conflicting evidence is rejected; the deployment total is counted
  once globally, assigned owner portions at that owner, and the unallocated
  remainder conservatively against each owner. Reserves are separate from call
  facts; absent historic calls are not invented to balance accounting.
- Task release requires bounded/open recovery, current owner/key, compatible
  pinned workflow, current document rights, version, deadline and closed
  attempts. Narrow definer functions lock execution state without granting
  administrators unrestricted UPDATE on steps/attempts. A stored release
  decision permits reassociation of the key/epoch while old call identities stay
  fixed. Owner resolution remains explicit for retained unknown LLM calls.
- Regeneration now checks the current epoch for new commands, returning the
  planned absent/stale errors; authenticated idempotent replay still precedes
  mutable admission. Public recovery status allowlists timestamps and the lost
  acceptance-history marker instead of evidence details.
- The inspected release payload check spells the report suffix `/save_report`,
  while the actual workflow uses `analysis/save-report`. This is an implementation
  typo reported to the author. It is not promoted to a security finding here:
  the normal unknown-demo release is already rejected, and a reachable missing
  report checkpoint needs a specific scenario/regression rather than an assumed
  corrupt database.
- Boolean `Literal[True]` attestations accept integer `1` under the pinned
  Pydantic version; strings, false and null were rejected by an isolated check.
  This normalization is not treated as an authorization bypass: the evidence is
  provided by a trusted administrator and still normalizes to required truth.

## Independently executed verification

```text
.venv/bin/pytest tests/unit/test_retention_policy.py tests/unit/test_command_boundaries.py tests/unit/test_operations.py -q --tb=short
52 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED in 0.32s

.venv/bin/ruff check src/task_platform/recovery.py src/task_platform/delivery.py src/task_platform/accounting.py migrations/versions/0009_corpus.py migrations/versions/0010_recovery.py migrations/versions/0011_recovery_authority.py
PASS
.venv/bin/ruff format --check <same six files>
PASS, 6 files already formatted

.venv/bin/mypy src/task_platform/recovery.py src/task_platform/delivery.py src/task_platform/accounting.py
PASS, 3 source files checked
```

These are selected isolated unit/static checks; they do not exercise recovery
SQL or replace the full M1–M7 regression. No maintained tests were edited,
disabled or weakened. The initial author RED logs and current GREEN attempts
were read as development history, not reported as a final successful gate while
the increment is still changing.

## Remaining completion requirements

Close findings through meaningful real-environment TDD and inspect the final
correction. Real backup/restore before B and before task creation, independent
remote-journal survival, credential/session isolation, egress fencing, RPO/RTO
including safe reopening, failed migration and compatible rollback exercises,
full cumulative load, scans and final installed-image regression remain pending
separate gates. External-isolation evidence fields are attestations, not proof
that this Python function itself revoked provider credentials or killed old
workers. Those operational actions must be exercised or explicitly unexecuted.
No paid-provider campaign, production delivery or overall M7 approval is made.

## Correction follow-up — R1/R2 and partial R3 closure

The reviewer inspected the author's real PostgreSQL RED and subsequent targeted
GREEN records and the corrected working tree. The initial findings above remain
historical; the current scoped status is:

| Finding | Inspected status |
|---|---|
| R1 repeated terminal quarantine event | **Closed**: event insertion is sourced from the exact task UPDATE RETURNING CTE; retained terminal markers are excluded. |
| R2 still-pinned retired handler | **Closed**: shared `required_workflows` selects available catalogue entries **or** versions pinned by nonterminal tasks; both delivery and recovery call it. |
| R3 budget verification | **Partially corrected, remaining empty-owner case open**, described below. |

Retained author evidence inspected:

- `m7-repeated-restore-red.txt`: **2 FAIL, 26 PASS in 3.69s**, including the
  expected real duplicate event key on the second restore. The other failure
  concerns the simultaneously developing task-release increment.
- `m7-reopen-preflight-red.txt`: **5 FAIL, 28 PASS in 3.78s**, four insufficient
  budget scopes and one omitted pinned retired handler; all behavioral failures.
- `m7-reopen-preflight-green.txt`: **33 PASS, 0 FAIL/ERRORS/SKIPPED in 5.40s**.
- `m7-recovery-regression.txt`: **757 PASS, 8 FAIL, 0 ERRORS/SKIPPED in 277.16s**,
  an unsuccessful full installed-image regression. Seven failures expose the
  private-evidence planning regression below; one is the exact table whitelist
  missing the added `restore_decisions` table. This run is not a completed gate.

The R3 correction locks owners and provider after the platform prefix, then uses
the normal `admission_reason` formula with a nonpersisted prospective task and
zero additional reservation. No synthetic call/task is saved and no reserve is
counted twice. This covers known owners' deployment/owner money and token
exposure, overruns and provider availability under the existing formula.

**Remaining R3 edge:** the verification executes only inside `for owner in
owners`. A valid restored point before principal creation has no owners. The
administrator can still record nonzero **unallocated** disaster exposure with
`owners=[]`, representing potentially lost later owner/history records. Reopen
then executes zero accounting checks and can open even when the disaster total
exceeds the restored global money/token cap. The original premature-reopening
contract gap therefore remains in this edge; B still protects later authorizations.
Verify deployment/global exposure independently of enumerating retained owners.
Required regression: an otherwise initialized zero-principal backup point,
nonzero unallocated reserve above a global cap, and attempted reopen must remain
closed. The author was notified promptly; no reviewer DB test was run.

## R4 — High — Ordinary worker updates plan a private-evidence subquery

**Location:** `0011_recovery_authority.py` injected `guard_task` condition.

The complete regression and the dedicated retained
`m7-private-guard-red.txt` demonstrate a confirmed product regression:
ordinary runtime task transitions fail with `permission denied for table
restore_decisions`. The broad SQL boolean expression contains both
`current_user='atp_migrator'` and an EXISTS against private evidence. A false
runtime-role predicate does not prevent PostgreSQL from planning/checking the
privileged subquery when preparing that expression. Keeping restore decisions
private is correct; granting the runtime roles SELECT to silence the regression
would weaken the intended boundary.

Dedicated behavioral RED inspected: **1 FAIL, 20 deselected in 0.78s**. The
maintained regression first verifies API/worker lack SELECT and then exercises
normal B/C unknown progression. Existing ordinary tests also detect the failure;
it was not a connection/setup error.

**Recommendation:** a separate outer PL/pgSQL IF must guard the effective
migrator-only branch, with the private-table query in a distinct nested
statement evaluated only inside that branch. Preserve API/worker no-SELECT
grants, test ordinary transitions and valid administrative release, and rerun
the full suite. The author has already identified this approach. At the last
source inspection the migration still contained the old combined expression;
R4 closure and final full-regression PASS are therefore pending.

Additional independent checks during this follow-up:

```text
.venv/bin/pytest tests/unit/test_retention_policy.py tests/unit/test_recovery_cli.py tests/unit/test_backup_artifact.py -q --tb=short
46 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED in 1.08s
Ruff / format on recovery, delivery, accounting, tasks, migrations0010/0011: PASS, 6 formatted files
mypy on recovery, delivery, accounting, tasks: PASS, 4 source files
```

These selected units include the synthetic backup helper, not real PostgreSQL
dump/encryption/restore or off-host durability evidence. No network, database,
Compose or source mutation was performed by this reviewer. Overall M7 review
remains open for R3/R4 correction and the still-pending release gates.

## Final correction follow-up for this increment — R3/R4 closed

**All R1–R4 code findings from this scoped recovery review are now closed in
the inspected working tree.** This does not approve overall M7 or supersede the
required final installed-image regression, real disaster drills, load, scans
and final global review. The earlier failed results and initial verdict remain
historical evidence above.

Final inspected correction anchors:

```text
recovery.py                      563c3c52c3495000e36da7c7b0e16176cc682e2fabc64f6279a5b244a8838ea8
0011_recovery_authority.py        f2ac026cc166a6037b6c3402d78526f4f4792fa46436cc8134a933b47d079740
test_restore.py                  f2441e52f9b0bc605394bd3f164d308dc9224fec3feb4576c9ea77228fbcde3b
```

- **R3 empty-owner case:** `reopen` now runs the prospective admission formula
  for a fresh unpersisted owner identity before iterating retained owners. The
  deployment/global check therefore executes even for a zero-principal restored
  point; unallocated exposure is also checked conservatively against a possible
  new owner's cap. No synthetic task/owner/call is inserted. The maintained
  real PostgreSQL regression establishes an actual empty-principal state inside
  an outer transaction, records unallocated exposure above the global cap and
  requires reopen rejection, then rolls the destructive fixture back. A
  test-only definer helper is removed in cleanup. After the author supplied its
  exact path, the reviewer inspected `m7-empty-restore-red.txt`: **1 FAIL,
  21 deselected in 0.33s**, `DID NOT RAISE ValueError` at reopen. This is a
  meaningful real PostgreSQL behavioral RED, separate from the private-guard
  evidence.
- **R4 private query planning:** the role comparison is now a separate outer
  PL/pgSQL IF. Only its effective-migrator branch contains the SQL expression
  against `restore_decisions`; ordinary runtime transitions do not plan that
  private query. No API/worker SELECT grant was added. The regression asserts
  those privileges remain absent, explicitly selects `force_generic_plan` and
  exercises ordinary claim/B/unknown progression. The retained earlier custom
  plan probe passed (1 PASS, 20 deselected, 0.52s); it was not falsely labeled
  RED. Forcing the generic expression plan produced the meaningful dedicated
  privilege regression already recorded above.

Inspected primary-author post-correction result:
`m7-private-guard-green.txt` — **94 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED in 15.72s**.
These targeted connected tests cover the new regressions and prior integration
paths. They are not the complete M1–M7 suite. The next full installed-image
regression was still being built/run by the primary author at this review.
The exact database-table whitelist is being extended with the approved
`restore_decisions` table while retaining set equality; no coverage reduction is
approved or required by this review.

Independently rerun after the corrections:

```text
.venv/bin/pytest tests/unit/test_retention_policy.py tests/unit/test_recovery_cli.py tests/unit/test_backup_artifact.py -q --tb=short
46 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED in 1.52s
Ruff / format on recovery.py, migration0011, test_restore.py: PASS, 3 formatted files
mypy src/task_platform/recovery.py: PASS, 1 source file
```

No database, Compose, network, Git or source mutation was performed by the
reviewer. Final release verification remains separate, and no new operational
guarantee is inferred from these scoped corrections.

Owner-facing projection of disaster reserves is a subsequent pending increment;
this review has assessed admission accounting and reopening, not certified a
public disaster-usage reporting contract before that code/tests exist.
