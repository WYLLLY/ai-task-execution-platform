# M6 initial cursor slice — independent review

Review date: 2026-10-26. Base revision:
`43f63971a83832150a0e2123b6df4706550ac443`; branch `codex/m6`.
Scope: the uncommitted `src/task_platform/event_cursor.py`,
`tests/unit/test_event_cursor.py`, initial matrix and retained TDD outputs.
This is an initial slice review, not approval or verification of M6.

The reviewer read AGENTS.md, approved plan §3.6 and M6/F17–F19, the accepted
architecture review response, and the `adversarial-review`,
`execution-reliability-review`, `agent-security-review` and `verification`
skills. Application code and tests were not modified during review.

## Findings and mechanisms

No confirmed correctness, security or reliability defect was found within this
pure helper's implemented scope. Cursor syntax is bounded to 56 characters before
regex/integer parsing; its full match excludes control characters and non-ASCII
digits. UUID comparison binds a parsed position to the supplied task. Sequence
generation and parsing enforce the nonnegative signed-63-bit range. Conflicting
header/query values and future positions yield generic 400 errors without
reflecting attacker input. A position below `oldest - 1` yields 410 and a snapshot
URL built solely from the supplied task UUID. Invalid server metadata raises an
invariant error rather than silently changing the replay position. The exact
retention boundary and an empty history are supported.

The helper deliberately does not perform authorization. Its caller must first
authenticate and authorize the task, then supply metadata from the same coherent
snapshot used for replay. The current module documentation and test matrix state
this ordering. No events route calls the helper yet; therefore owner isolation or
authorization-before-validation has not been demonstrated at the HTTP boundary.

## Verification and TDD evidence

Retained author evidence records a meaningful behavior RED: **32 failed, 2 passed,
0 errors, 0 skipped in 0.17s**, with an importable interface returning placeholder
positions/IDs. The shown failures concern missing cursor behavior, not setup or
infrastructure. Retained GREEN is **34 passed in 0.08s**. The initial whole-unit
run records **168 passed in 2.17s**. These are author-run evidence; the review does
not retrospectively reconstruct test-before-code chronology.

Independently executed:

| Command / check | Result |
|---|---|
| `.venv/bin/pytest -q tests/unit/test_event_cursor.py` | PASS — 34 passed, 0 failed, 0 errors, 0 skipped in 0.10s |
| `.venv/bin/ruff check src/task_platform/event_cursor.py tests/unit/test_event_cursor.py` | PASS |
| `.venv/bin/ruff format --check src/task_platform/event_cursor.py tests/unit/test_event_cursor.py` | PASS — 2 files already formatted |
| Independent Python enumeration of watermarks 0–24, all valid `oldest` values and positions 0 through `watermark + 1` | PASS — 5,850 cases; valid positions preserved, expired positions 410, future positions 400 |

The independent enumeration imported only the reviewed helper and exception
types. It checked `oldest - 1 <= position <= watermark` against returned values,
and checked the complementary expired/future conditions against exceptions.

## Limits and verdict

**PASS for the initial pure cursor slice; M6 remains incomplete.** PostgreSQL,
Redis, migrations, privileges and concurrency tests were not executed for this
helper because it has no I/O or state mutation. This does not mark those M6
categories passed or not applicable to the milestone. API access ordering,
consistent snapshots, replay gaps/reset, retention races, revocation, slow writes,
proxy flushing, shared polling, load/degraded operation and observability remain
pending. No provider requests or external effects occurred. No application/test
fixes were made, and the pre-existing AGENTS.md change was preserved.
