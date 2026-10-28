# Independent review — initial M7 retention policy

**Scoped verdict: APPROVE.** No material defect was found in the inspected pure
retention-clock increment. This verdict does not certify M7, F20–F22, a purge
operation, backup/restore, deployment or any production readiness guarantee.

## Scope and reference

The authorized review covers only `src/task_platform/retention_policy.py`,
`tests/unit/test_retention_policy.py`, their retained RED/GREEN evidence and the
initial M7 matrix. The supplied baseline is merged M6
`b86111386e4c6e46683e9ffac2fc31d24da9bdc7`; the reviewed increment is the supplied
working tree on feature/m7. No Git mutation, application/test edit, database,
network, provider request or broader unimplemented M7 audit was performed.

AGENTS.md and approved plan §5.2 govern semantics. The reviewer reread
`adversarial-review`, `execution-reliability-review` and `agent-security-review`.
The M7 matrix explicitly keeps real purge/concurrency/restore/delivery/load and
full-regression evidence pending. Those absent features were not treated as
implemented defects or silently approved.

Inspected source SHA-256:

```text
retention_policy.py       5b5650b4b859c1791e394c425c9f87a42a5b5ec60e761026cc23ced491084691
test_retention_policy.py  2a5e78e523a163aaa3f4ae46a0a724d160ea679392b82a8c32ba22ac6e001c18
```

## Assessment

- Content age begins at termination, not acceptance; a nonterminal task does not
  become content-due merely by aging. Retained HTTP response age separately
  begins at acceptance. Tombstones have their own 90-day minimum clock.
- Financial age requires an explicit resolution time at least 90 days old,
  exactly zero bounded integer exposure and no open reconciliation. Missing
  resolution, positive exposure or open reconciliation keeps `financial=False`
  regardless of age. Booleans/floats/negative/overflow exposure are rejected;
  reconciliation status must be an actual boolean.
- Current and optional timestamps must be timezone-aware; future clocks and
  clocks before acceptance fail explicitly. Conversion to UTC occurs before
  arithmetic, so DST wall-clock changes cannot shorten a retention duration.
  Elapsed subtraction avoids overflowing a valid maximum UTC datetime merely
  by adding 30/90 days to it.
- The result is an immutable set of due-date booleans. The module has no SQL,
  deletion, permission, network, dispatch, accounting mutation or exposure-release
  capability. A content clock may be due while financial exposure remains open;
  this is compatible with purging sensitive bytes while retaining minimal
  financial identities/evidence. It is not financial-delete authorization.

No authentication, locking, idempotency, crash recovery or purge correctness is
claimed from these tests. They are properties of later connected operations,
not of a deterministic function without effects or durable state.

## TDD evidence inspected

| Retained evidence | Recorded behavior |
|---|---|
| `m7-retention-policy-red.txt` | 21 failed, 7 passed: missing expiry/validation behavior; no import or connection failure counted as RED |
| `m7-retention-policy-green.txt` | 28 passed |
| `m7-retention-dst-red.txt` | 1 failed, 28 passed: Paris spring transition expired after only 29 days 23 hours |
| `m7-retention-dst-green.txt` | 29 passed after UTC normalization |
| `m7-retention-max-red.txt` | 1 failed, 29 passed: valid maximum UTC date overflowed the expiry addition |
| `m7-retention-max-green.txt` | 30 passed after elapsed-duration comparison |

The reviewer observed the final maximum-date correction during the review and
included it in the independent run below. The recorded meaningful failures
remain evidence of the author's sequence; they were inspected, not recreated
by modifying implementation code.

## Independently executed verification

```text
.venv/bin/pytest tests/unit/test_retention_policy.py -q --tb=short
30 passed, 0 failed, 0 errors, 0 skipped in 0.01s

.venv/bin/ruff check src/task_platform/retention_policy.py tests/unit/test_retention_policy.py
PASS

.venv/bin/ruff format --check src/task_platform/retention_policy.py tests/unit/test_retention_policy.py
PASS, 2 files already formatted

.venv/bin/mypy src/task_platform/retention_policy.py tests/unit/test_retention_policy.py
PASS, 2 source files checked
```

An additional read-only, ad hoc oracle compared all four booleans against UTC
elapsed durations across March/October starting points, six time zones, the
30/90-day boundaries at minus/zero/plus one microsecond, zero/max exposure and
open/closed reconciliation: **288 cases passed**. These are diagnostic oracle
cases, not additional maintained pytest counts or database evidence.

No full M1–M7 suite, database/Redis, privilege/migration, network, external effect,
backup/restore, load, CI or live model check was executed by this reviewer. The
primary author's final-image regression remains a separate completion gate.

## Requirements for subsequent integration

These are scope boundaries for later work, not findings against this increment:

1. Treat `Due` as time eligibility only. Actual deletion must recheck owner,
   state, references, permissions and current retention/exposure under the
   approved transaction locks. Date eligibility cannot waive an active corpus
   reference or authorize deletion of minimal evidence.
2. Financial callers must account for both money and token uncertainty, all
   unresolved calls and reconciliation state. The scalar `exposure` argument
   cannot stand in for omitted durable exposure dimensions or unknown records.
3. Expiring a retained response must preserve required command/task tombstones
   and prevent silent recreation. Purging exact payloads must disable resend
   rather than reconstruct bytes; neither deletion nor restore resets the
   original deadline/replay horizon.
4. Verify connected operations with real PostgreSQL, synchronized competing
   transactions, preserved external effect journals and crash/restart tests.
   Backup retention/provider retention are separate policies; passing this pure
   clock review does not prove those systems erase or preserve data correctly.
