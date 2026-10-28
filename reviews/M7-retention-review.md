# Independent review — M7 retention increment, G1 and proxy recovery

Date: 2026-10-27. **Scoped code-review verdict: APPROVE.** No additional
confirmed material correctness or security defect was found in the inspected
retention increment. G1 remains closed for the inspected correction. This is
not approval of overall M7: restore, delivery, corpus deletion, full load,
scanning and final release verification are still being implemented or awaited.

## Scope and review boundary

Baseline: local HEAD `b86111386e4c6e46683e9ffac2fc31d24da9bdc7` on `feature/m7`.
Reviewed working-tree files: migrations `0007_retention.py` and `0008_purge.py`,
`retention.py`, `retention_policy.py`, dispatch-freshness correction in
`execution.py`, changes to `api.py`, `event_reads.py`, `tasks.py`, task/health
models, and `infra/nginx.conf`, together with associated retained test evidence
and test source. The relevant original task/step/call guards and bootstrap grants
were read alongside the migration exceptions. The pre-existing AGENTS.md edit
was left unchanged.

The reviewer applied `adversarial-review`, `execution-reliability-review` and
`agent-security-review` against AGENTS.md and approved plan §§3.2–3.6, 5.2 and
F06/F09/F17/F18/F20. The reviewer wrote only this report; no application/test,
infrastructure or Git mutation was performed. No database, Compose, network,
paid provider or fault test was run because the primary author owns the active
disposable stack and image build.

Inspected SHA-256 anchors:

```text
0007_retention.py   a4f4217f1055a07d5f1cbae46d721c8a6894141bc3de5894d45a8b1b2ea3a856
0008_purge.py       5c6181e8f9fb8cb178974070d2c871df1b87d07095d20d9c79a5f82003b00ed1
retention.py        3f549c80682858e6ab091d87eeff6ba8abc3ffcc1ca18b8e8c201b0c35731bd9
retention_policy.py 5b5650b4b859c1791e394c425c9f87a42a5b5ec60e761026cc23ced491084691
execution.py        14fa94d4ae33a778f2bb3196fece7bf32c7ac88fa15499f7c2177755bacd7ef9
api.py              16b68eb1c616e5762f4cc9b34a4e5334bab365d5d4f67e88f43f8b14d11b85ba
event_reads.py      c8d850233c617a257364c63b687fb6d93349d158e834c7cb8f229576b7caa6ca
tasks.py            984427f46dd80cc1dc09eaeab19cffe45c6bbddf65e73095c2967eb4f8b4f82c
nginx.conf          be1b89a6803bf0f5683bc8323fe96f3decf73f31c63da24b22d04c0da41af1b2
```

## Mechanisms inspected

- **Retention clocks:** terminal time is recorded at the terminal transition;
  command response age has a separate timestamp; confirmed resolution records
  its own time. Legacy termination/resolution timestamps are conservatively
  initialized at migration time rather than guessed backwards. Estimated or
  exposed costs have no financial-resolution clock. The pure clock helper uses
  aware UTC elapsed time, rejects invalid exposure/clock data and does not
  authorize deletion by itself.
- **Authority and SQL guards:** the Python entry point requires the dedicated
  administrator role and bounds each run to at most 100 candidates. The SQL
  operation is `SECURITY DEFINER`, owned by the migration role, with a fixed
  search path and fully qualified application relations. Public execution is
  revoked and only admin receives EXECUTE. The public schema is not writable
  by API/worker/admin in bootstrap. Ordinary API/worker roles receive no new
  general payload UPDATE or DELETE permission. Guard exceptions require the
  effective migrator role and constrain the changed columns; they do not waive
  immutable owner, state, version, payload hash, call identity or operation time.
  Saved pre-purge guard functions are not publicly executable.
- **Concurrency and transactions:** each task gets a separate transaction with
  platform → owner → provider → task → sorted steps → sorted attempts → sorted
  calls → sorted costs, matching the plan's permitted subset and order. No
  earlier category is acquired after child locks, no HTTP crosses the
  transaction, and fresh DB time is sampled after locks. Current terminal state,
  30-day age and prior purge status are rechecked under lock. All redactions,
  tombstone insertion, response expiry and logical-storage reclamation commit
  together. A competing operation sees either the original or committed purge;
  repeating the purge does not reclaim storage twice.
- **Exposure and evidence:** purge does not delete call, cost, cost-evidence,
  accounting-decision, attempt or owner identities and does not release money
  or token exposure. Exact external and step payloads, step results/protocol
  items, source input, terminal result and progress history are redacted; hashes,
  first-authorization time and operation identities remain. The external-call
  replay flag has a SQL consistency constraint. Existing report recovery
  requires the exact step payload, so a NULL payload prevents a reconstructed
  resend. This increment does not yet perform financial-record expiry.
- **API and SSE:** purged tasks are excluded from list/snapshot/event reads;
  owned direct routes use owner-bound tombstones/HistoryExpired and foreign
  resources stay hidden. Existing command keys return 410 once their retained
  response is redacted, so the same key cannot silently create another task.
  Retained call records still support owner usage and late accounting. The
  event watermark/oldest marker is updated atomically with deletion, allowing
  an active stream to reset. List cursor anchor use is still owner-filtered.
- **G1:** ownership is refreshed after potentially delayed validation, key and
  admission clocks are refreshed, every B write is flushed before a final
  ownership/key-expiry guard, and an expired flush interval rolls back the
  complete B transaction. Stable committed authorization never grants a second
  send. See `global-review-initial.md` for the original finding and closure.
- **Proxy:** upstream resolution now uses Docker's fixed local resolver with a
  bounded timeout/cache instead of retaining the initially resolved API address
  for the Nginx process lifetime. The destination remains the fixed `api:8000`,
  with no request/model-selected host. Explicit `proxy_next_upstream off`
  prevents proxy failover from invisibly repeating a request. Existing response
  buffering, write and stream-read bounds, metrics exclusion and no-access-log
  settings remain. This private fixture is not a production TLS deployment.

## Evidence inspected, with limits

| Primary-author retained evidence | Observed result | Interpretation |
|---|---|---|
| `m7-purge-red.txt` | 3 FAIL, 1 PASS, 2 deselected, 0.68s | Behavioral RED: no purge effect and absent administrative boundary. Not a complete suite. |
| `m7-purge-green.txt` | 6 PASS, 0 FAIL/ERRORS/SKIPPED, 0.96s | Initial connected purge, clocks and Python role checks after migration. |
| `m7-proxy-replacement-red.txt` | 1 FAIL, 4 deselected, 0.20s | Direct API returns 200 while stale proxy returns 404; meaningful regression against the replaced-container setup. |
| `m7-purge-proxy-green.txt` | 16 PASS, 1 FAIL, 17.52s | Proxy/freshness tests pass; purge failure is an absent SQL function because migration 0008 had not been applied. This combined run is **not PASS** and the setup error is not RED. |
| `m7-g1-clocks-red.txt` / `m7-g1-clocks-green.txt` | 8 FAIL / 42 PASS | Six late-permit cases plus two initial clocks, then connected correction; details in the global report. |

The reviewer inspected these primary-author results but did not rerun them in
the shared database. The direct/proxied regression checks a replacement already
performed by its surrounding setup; the test function alone does not orchestrate
container replacement and must not be described as doing so.

Independently executed:

```text
.venv/bin/pytest tests/unit/test_retention_policy.py tests/unit/test_event_cursor.py tests/unit/test_sse_stream.py -q --tb=short
72 PASS, 0 FAIL, 0 ERRORS, 0 SKIPPED in 0.64s

.venv/bin/ruff check <8 inspected migration/application files>
PASS
.venv/bin/ruff format --check <same 8 files>
PASS, 8 files already formatted

.venv/bin/mypy src/task_platform/retention.py src/task_platform/retention_policy.py src/task_platform/execution.py src/task_platform/event_reads.py
PASS, 4 source files checked
```

The eight static-check files are migrations 0007/0008 and retention, policy,
execution, API, event reads and task services. An earlier broader Ruff invocation
also included the actively edited integration files and observed one I001
import-order error in new `test_retention.py` at line 205; it was reported to the
author without editing the file. Its chained formatting command did not execute
after that failure. This transient observation is not a release/static-suite
PASS; the primary author must run the final full checks after edits settle.

## Remaining verification for this increment

No additional confirmed code finding blocks this scoped review. The following
unexecuted assertions still belong to the primary author's completion gate:

1. Exercise the SQL boundary directly as API and worker: `SELECT
   purge_task_contents(...)` must be denied, and direct sensitive-payload
   mutation must remain forbidden. The inspected initial negative test exits
   at the Python `current_user` check and therefore does not prove the SQL
   grants/trigger behavior despite its broad test name.
2. Retain real concurrent purge/purge and purge/late-settlement tests with
   actual observed contention, idempotent counters/tombstone count, current
   owner 410/foreign 404 and retained money **and token** uncertainty. New tests
   were being added during this review and their results are not invented here.
3. Run the final installed-image full regression after applying the complete
   schema. An old image with a new health revision or a missing migration is
   an environment mismatch, not a valid completed gate. Preserve prior failed
   results and distinguish them from final success.
4. Review corpus deletion/active-reference guards once implemented. No corpus
   erasure feature is included in this approval. Likewise, command tombstone,
   operation retention, late-accounting behavior and replay flags must remain
   compatible with the subsequent restore/delivery work.

Overall M7/global approval remains pending the implemented restore/delivery/load
scope, scans, complete final tests and final independent review. No paid OpenAI
request, production deployment or merge is certified by this report.
