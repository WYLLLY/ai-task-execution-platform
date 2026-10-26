# M4 test matrix and TDD record

Baseline: M3 `046ebc4`; branch `feature/m4`. The existing AGENTS.md edit is user-owned.
Only M4 is authorized; M5/OpenAI, SSE and disaster restoration remain outside scope.

| Increment | Coverage / acceptance |
|---|---|
| Corpus | PostgreSQL immutable versions, owner isolation, import/revoke CLI, storage admission, task pinning, concurrent revocation (F11/F13) |
| Agent contract | Strict names/arguments/item counts, protocol checkpoints, context bounds, citations, hostile documents and semantic-quality distinction (F13) |
| Durable workflow | A/B/C continuation, local-tool authorization/counting, restart at every turn, stale C, unavailable handlers, current document rights (F11) |
| Demo boundary | Authenticated fixed destination, independent PostgreSQL effect/deduplication, same-key conflicts, concurrent resend, lost response/probe, horizon/deadline (F12) |
| Verification | Full M1–M4 suite, roles/constraints, clean migration/upgrade, API/E2E, real Redis regression, static checks and independent review |

Unit, integration, API, E2E, concurrency, security, resilience and failure tests apply.
Real provider protocol/tokenizer and paid smoke are not applicable until M5.
Each implementation increment records a behavioral RED before application edits, followed by
GREEN/refactoring. Infrastructure/import errors are excluded from RED evidence.

## Recorded Red → Green increments

All RED runs used the new tests before the corresponding implementation/correction. Typed
interfaces with no behavior allowed collection where necessary. Original RED output (or explicitly labeled observed summaries after temporary log loss) is
retained under `reviews/evidence/m4-*-red.txt`; generated fixture credentials are redacted and trailing whitespace is normalized.
No import, connection or setup failure counts as RED.

| Increment | Behavioral RED | Focused GREEN |
|---|---|---|
| Corpus schema / import CLI / pinned references | 2 failed | 2 passed |
| Strict simulated protocol / complete context | 4 failed, 18 passed | 22 passed |
| Durable turns / rights / citations / local tools | 8 failed | 8 passed |
| Authenticated transactional demo | 1 failed (eight 404 responses) | 1 passed |
| Physical report transport / lost response / proof | 1 failed, 1 passed | 2 passed |
| Frozen final save / proof binding | 2 failed | 2 passed |
| Bounded probes / same-operation resend | 2 failed, 2 deselected | 4 passed including save tests |
| Complete two-boundary workflow | 1 failed | 1 passed |
| Missing handler / output storage / report regeneration rejection | 3 failed, 2 passed | 5 passed |
| CLI restarts between durable units | 1 failed, 1 deselected | 1 passed, 1 deselected |
| No tool count before B / document rights at resolve | 2 failed, 5 deselected | 7 passed |
| Independent review: local deadline / bounded SQL source loading | 2 failed, 7 deselected | Included in combined 18-pass review regression |
| Independent review: required grants / duplicate report storage | 4 failed, 9 deselected | Included in combined 18-pass review regression |
| Independent review: probe deadline after delayed commit | 1 failed, 4 deselected | Included in combined 18-pass review regression |
| Independent review: legacy handler rejects corpus and continuations | 3 failed, 15 deselected | Combined 4 passed, 15 deselected |
| Independent review: model C deadline after source validation | 1 failed, 18 deselected | Combined 4 passed, 15 deselected |

The reusable integration command prefix is:

```sh
DOCKER_CONTEXT=colima-ai-task-platform docker-compose -p atp-m1-test \
  -f compose.yaml -f compose.demo.yaml run --rm --no-deps \
  -e PYTHONPATH=/app/src -v "$PWD/src:/app/src:ro" -v "$PWD/tests:/app/tests:ro" \
  test pytest -p no:cacheprovider
```

The protocol RED uses `.venv/bin/pytest tests/unit/test_agent_protocol.py -q --tb=short`.
Each retained RED names the exact failed test paths. The final installed-image full suite,
focused gates, clean migrations and smoke results are recorded in M4-verification.md.

Refactoring reused the M3 executor/heartbeat/supervisor with explicit analysis specialization,
kept transport and protocol validation separate, and used `no_autoflush` around C's result
validation so terminal rows are written once with their final data. PostgreSQL terminal guards
were preserved. An early demo conflict test accidentally replaced `42` inside random UUIDs;
it was corrected to replace only the report summary, without changing the conflict assertion.

Existing test-contract updates add `documents` to the exact table-set assertion and move the
expected current migration to `0004_workflow`. No equality was weakened to subset matching,
no existing assertion/test was removed, and the M1/M2 historical TDD exception was not extended.

## F-scenario evidence and boundaries

- F11: new worker/executor at every unit, five separate CLI starts, hard process exit after
  every committed unit, immutable versions, revoked source, unavailable handler, protected
  checkpoints, reconstructed call IDs/history, complete-context cap and concurrent local C.
- F12: eight concurrent exact HTTP submissions, one retained response/effect, conflicting
  body rejected, independent PostgreSQL invocation journal, response lost after remote commit,
  identity-bound lookup, at most three probes, same payload/hash on new transmissions.
- F13: prohibited tools/URL/header fields, invalid cardinality/schema/status, nested unauthorized
  references, forged citations/evidence, revoked reads and observed `pg_blocking_pids` ordering
  before B. Opaque items round-trip internally and are absent from terminal reports.
- Semantic evaluation: 20 normal scripted dossiers and 20 intentionally misleading variants.
  Each has pinned owner/reference/version/hash, expected source facts and exact citations;
  injected instructions remain untrusted data and foreign/forged citations are rejected.
  All 40 have valid schema; 20 normal expected-fact matches and 20 adversarial contradictions
  are reported separately. This is an evaluator/fixture campaign, not real-model performance.
- M1–M3 tests remain enabled, including real Redis outage, process faults, unknown exposure,
  stale ownership, budget settlement, role denial, migration and cancellation regressions.
- M5 real OpenAI protocol/tokenizer/billing, M6 SSE and M7 retention/restore/load are deferred.
