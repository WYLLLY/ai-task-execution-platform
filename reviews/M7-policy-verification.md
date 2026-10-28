# M7 initial policy increment — scoped verification PASS

This report covers only the pure retention-clock increment on `feature/m7`.
It is not an M7 completion report. No purge, restoration, deployment, benchmark
or backup operation is exposed by this increment.

M6 is merged via PR#6, commit
`b86111386e4c6e46683e9ffac2fc31d24da9bdc7`, after independent APPROVE and two
SUCCESS CI runs on exact head `2dbcdb6b907112113e0828e3eceb943e44ef4370`:
[PR CI](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40422415853),
[push CI](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40422398818).
Both validated193unit+495integration tests and the54/59/76/1/19 named gates,
static checks and Compose smoke. All six milestone PRs#1–#6 are confirmed merged.
The pre-existing AGENTS.md bytes remain unchanged and excluded.

## Tests first

- Initial scaffold RED:21FAIL,7PASS,0ERRORS/SKIPPED,0.04s.
- Minimal GREEN:28PASS,0FAIL/ERRORS/SKIPPED,0.01s.
- DST regression RED:1FAIL,28PASS,0ERRORS/SKIPPED,0.02s. Same-zone datetime
  arithmetic shortened the interval by an hour. Normalize all clocks to UTC.
- DST GREEN:29PASS,0FAIL/ERRORS/SKIPPED.
- Maximum timestamp RED:1FAIL,29PASS,0ERRORS/SKIPPED,0.02s, meaningful overflow.
  Compare elapsed durations instead of adding days to the upper timestamp.
- Final focused GREEN:30PASS,0FAIL/ERRORS/SKIPPED.

The function is a pure age predicate. An unresolved financial record never
becomes due merely by age; a resolution clock, zero exposure and closed
reconciliation are required. A future PG caller must recheck both money and
token exposure, ownership/rights, current references and concurrent state under
the full lock order. These unit tests cannot authorize deletion or prove SQL
locking, retention, privileges, durable accounting, recovery or crash behavior.

## Independent review and checks

`M7-policy-review.md`: scoped APPROVE;30unit tests PASS,288 independently
calculated timestamp/exposure cases PASS, focused Ruff/format/mypy PASS. No
material finding. Review does not certify unimplemented F20–F22 behavior.

Repository Ruff PASS; format155files PASS; mypy123sources PASS. The installed
image is rebuilt with the final source after the upper-timestamp correction.
Full regression and exact environmental setup findings are retained below.

Remaining M7 gates in `M7-test-matrix.md` are pending, not silently skipped or
passed. Paid OpenAI validation remains unexecuted without separate spending
approval. No M7 merge authorization has been granted.


## First complete run — contaminated disposable state

`m7-policy-regression.txt`:717PASS,1FAIL,0ERRORS/SKIPPED in161.26s.
The failed existing `test_concurrent_repeated_evidence_settles_once_and_audits`
used an unfiltered `SELECT state FROM tasks` and observed a cancelled task left
by the preceding M6 Compose smoke, while expecting only its own suspended task.
All other717tests passed, including the new30policy cases and real F19 profile.
No application behavior, existing assertion or test was changed in response.
This is retained as an environment/test-isolation failure, not retention RED.

Only the explicitly disposable `atp-m1-test` stack/volumes were recreated;
no workspace secrets, AGENTS.md, external service or live provider was altered.
The same installed test image is rerun against an empty migrated/initialized
PostgreSQL/demo database and Redis. Setup logs: `m7-clean-stack.txt` and
`m7-fresh-stack.txt`. Exact rerun result is recorded when complete.


M6 post-merge main CI also confirmed SUCCESS on exact merge commit
`b86111386e4c6e46683e9ffac2fc31d24da9bdc7`:
[main CI40423549512](https://github.com/WYLLLY/ai-task-execution-platform/actions/runs/40423549512).
M6 therefore has observed local, PR, push and post-merge verification evidence;
this does not certify any later M7 behavior.


## Unchanged rerun on clean disposable stack

`m7-policy-regression-clean.txt`: **718PASS,0FAIL,0ERRORS,0SKIPPED in161.27s**.
This is the complete current repository suite:688previous tests plus30new pure
policy tests. The image/source/tests were unchanged from the first run; only the
disposable environment was recreated. Real F19 again passed with100streams,
120reads, heartbeat/control/replay continuity, one physical request/effect and
committed execution while Redis was stopped. All baseline migration/role checks
also passed in this regression.

**Scoped increment verdict: PASS. Overall M7: IN PROGRESS, not complete.**
Real retention/purge, restore, delivery, load and dependency/image scans remain
unimplemented/unexecuted as listed in the matrix; they are not certified by this
718-test regression. No M7 PR or merge is claimed. Work remains local on
`feature/m7`; M6's PR and main are already published/merged and verified.
