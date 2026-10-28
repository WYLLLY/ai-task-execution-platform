# M7 test matrix and evidence index

Scope: approved plan §§5.2–5.3,7.2–7.3, F20–F22, plus cumulative M1–M6
regressions. Base is merged M6 `b86111386e4c6e46683e9ffac2fc31d24da9bdc7`
(PR#6, merged 2026-10-26T23:59:53Z). The pre-existing AGENTS.md change is
preserved and excluded from task commits. No M7 merge is authorized.

## Coverage map

| Layer/risk | Maintained verification | Evidence/status |
|---|---|---|
| Unit / UTC / boundaries | Retention strict clocks, timezone/DST/max date, exposure money AND tokens, 30/90-day minima; encrypted backup streaming/deadlines/failures/tamper metadata | Policy30, backup9; PASS |
| F20 / real PostgreSQL | Terminal/response/resolution clocks; bounded admin purge of unknown call; retained accounting, late invoice, unavailable exact payload; owner410/foreign404; command replay tombstone | test_retention; PASS targeted and final full 828 cases |
| F20 / concurrency/security | Independent purge transactions observed waiting on platform lock; current rights and active corpus references; API/worker raw SQL/function denial | test_retention; PASS; no runtime grant expansion |
| F21 / recovery | All-task quarantine, repeated disaster retaining old terminal marker, new epoch, old key/lease invalid, shared exposure, bounded/reserve revisions, empty owners, current explicit association | test_restore; PASS targeted |
| F21 / concurrency/idempotence | Two independent admin connections observed through pg_blocking_pids; same revision admits one decision; stable evidence reread after lost client ACK; conflicting evidence/revision | test_restore_races; 2-case delivery/race run PASS |
| F21 / actual encrypted restore | Snapshots before B and before task creation; actual pg_dump/age/pg_restore; old DB/API/demo credentials rejected; no blind replay; post-backup accepted ID missing; independent remote effect persists | Actual drill PASS; RPO/RTO intervals published in final report |
| Public API / failure boundary | Owner-only allocated disaster provision in one MVCC projection; no fabricated B; structured bounded /system503 on initial/racing DB/schema/network failure | usage +7ASGI status cases; PASS |
| F22 / delivery | Close/drain; exact schema/epoch/available and retained handlers; exact runtime reads/writes; failed forward Alembic transaction rolls back partial DDL and leaves dispatch closed | test_delivery +test_delivery_failure; PASS targeted |
| Artifact/supply chain | Immutable image-ID compose overlay; actual base+venv installer absence, pinned libssl patch; locked certifi advisory regression; raw dependency/runtime scans | Artifact PASS; Python vulnerabilities0; raw OS findings remain documented |
| Load / nominal | Exact1000/1s,60s separate warmup,100SSE/10reads/s,2worker processes×4slots,20owners×20docs×4KiB; actual commit timestamps and independent request/effect journals | Initial exact campaign FAIL for 300 SSE transport errors retained; corrected campaign PASS: 1,000/1,000 succeeded, zero errors |
| Load / harness | Fixed reference profile, no missing-sample zeros, protocol/citations/usage, second-turn context bound, rejected falsePASS under failed SSE/read/worker pressure | 13unit PASS; R6/R7 and saturation sequencing review closed |
| Load / degraded/overload | Real Redis outage:100SSE/10reads/s,503new/202replay/control+heartbeat+polling; provider tiny limits and concurrent reservations; queue/admission bounds, slow absolute HTTP timeout | Existing F16/F19 and failure suites rerun cumulatively |
| Semantic evaluation |20synthetic dossiers+20document-injection variants, authentic citation vs contradictory summary, foreign references refused; schema/permission vs factual correctness separated | Existing40fixture cases retained; real model SKIPPED: no distinct paid authorization/cap |
| Observability / runbooks | Existing real operations API and injected alerts, disk/backup unknown status, trace/log redaction; backup/delivery/recovery operator actions | Cumulative M6 gates +English runbooks; production provisioning SKIPPED |
| Final verification | All M1–M7, named gates, clean/roundtrip migrations, exact privileges, Ruff/format/mypy, independent global review and GitHub CI | Final report records exact outcomes; no M7 completion claim before evidence |

No category is replaced with mocks for database/Redis correctness. The model and
report failure boundaries use real TCP and independent journals. Provider fixture
success does not validate OpenAI billing/quality. Off-host delivery, production
TLS/deployment and a production backup schedule require external configuration;
they are explicitly unexecuted, not passed.

## TDD index

Every application behavior was tested before implementation. Meaningful RED
records are retained; import/SQL syntax, missing migration, connection, fixture
permission and obsolete temporary-tool failures are not counted as RED.

| Increment | Behavioral RED | GREEN evidence |
|---|---|---|
| Dispatch clock recheck (G1) | m7-g1-clocks-red:8FAIL | m7-g1-clocks-green:42PASS |
| Retention policy / UTC/DST / overflow |21FAIL+7PASS; then1FAIL+28PASS; then1FAIL+29PASS | Committed policy report:30PASS |
| Purge clocks/content/unknown usage | m7-purge-red:3FAIL+1PASS | m7-purge-green; later94-case combined GREEN |
| Corpus delete / concurrent purgers | m7-corpus-red includes behavioral refusal failure; separate pool setup failure excluded | Expanded retention10PASS; combined94PASS |
| Quarantine and epoch guards | m7-restore-red:8FAIL | m7-private-guard-green:94PASS |
| Disaster reserve / explicit release | m7-reserve-release-red:7FAIL+8PASS | m7-reserve-delivery-green; later94PASS |
| Repeated disaster event (R1) | m7-repeated-restore-red: meaningful duplicate event plus separate setup failure | Combined94PASS |
| Retained handlers / budget reopening (R2/R3) | m7-reopen-preflight-red:5FAIL+28PASS; m7-empty-restore-red:1FAIL | m7-reopen-preflight-green:33PASS; later94PASS |
| Private generic-plan guard (R4) | m7-private-guard-red:1FAIL | m7-private-guard-green:94PASS |
| System outage (R5) | m7-system-status-red:7FAIL | m7-system-status-green:7PASS |
| Delivery write grants / owner usage | m7-preflight-usage-red:3FAIL | m7-preflight-usage-green:42PASS |
| Delivery exact SELECT grants | m7-preflight-select-red:2FAIL | m7-select-transport-green-final:18PASS |
| Proxy recreated API | m7-proxy-replacement-red:1FAIL | Full731PASS and network gate |
| Backup streaming/argv/absolute deadline | m7-backup-red:9FAIL; m7-backup-command-red:4FAIL+5PASS | Final container9PASS |
| CLI commands | m7-cli-red:7FAIL | m7-cli-green:7PASS |
| Reference profile/metrics/protocol | m7-load-profile-red:5FAIL | m7-load-profile-green:5PASS |
| Corpus context (R6) | m7-load-context-red:1FAIL+5PASS | m7-load-pressure-green:11PASS |
| Reference pressure falsePASS (R7) | m7-load-pressure-red:5FAIL+6PASS | m7-load-pressure-green:11PASS |
| certifi advisory | m7-certifi-red:1FAIL | m7-certifi-green:1PASS |
| Runtime installer / base interpreter / libssl | Actual image assertions fail in three retained RED logs | m7-runtime-artifact-green:PASS |

Initial full runs and setup mistakes remain in evidence rather than being erased.
The first final candidate had790PASS/1FAIL: a200ms fixture permit was started before
client/TLS asset initialization and could validly expire before sending. Creating
the same200ms permit after client initialization preserves every assertion and
the separate expired-permit zero-send case. Independent review accepted the
fixture correction. Later final results supersede, but do not rewrite, that run.

The load publication regression additionally retained1FAIL/11PASS before the
controller wrote unfavorable nominal metrics ahead of its later saturation phase;
GREEN12PASS. This prevents an overload error from erasing nominal failure evidence.
Saturation now lets the preceding provider window expire naturally (61s, no call
history reset), then bursts120requests with6sdeadlines and1RPM/1200TPM/maxactive1.
Two workers and a slow-drip physical model process prove bounded timeout/retained
exposure without automatic regeneration. Actual results remain pending until the
long campaign finishes. This extra unit is retained in the final cumulative suite.

A final saturation-ordering test observed workers starting before slow-model HTTP
readiness (1FAIL/0.44s). The correction starts and probes the slow boundary before
submitting six-second tasks, then starts workers. Cleanup is polled within15s to
include the existing five-second maintenance cadence. Physical-request assertions
remain exact and retained uncertain exposure is strengthened to1000micro-USD.
GREEN13PASS/0.42s; independent reviewer inspected the correction. The final complete
suite at that point contained804tests (260unit+544integration). New SSE and
transport-environment regressions bring the final candidate to809tests
(265unit+544integration), pending the installed-image cumulative run. This ordering unit mocks only fixture processes/probes;
it does not claim PostgreSQL/Redis or physical HTTP evidence by itself.

## Final campaign corrections

The exact reference campaign retained1,000 successful tasks and no duplicate
remote effects, but failed for300SSE transport errors. The original unfavorable
report is retained as `m7-reference-load-attempt1-fail.json`. The normal300-second
stream expiry raced the outer hard lifetime and omitted the final ASGI body.
Two maintained ASGI cases first failed on the missing final frame; a physical
TCP case first failed with `RemoteProtocolError: incomplete chunked read`.
Reserving closing-write grace inside the unchanged absolute lifetime fixes
normal expiry without extending authentication, data-write or connection bounds.
The focused11-case GREEN includes stalled sources/writes, disconnects and three
physical reconnects with exactly one ticket release each.

Two actual SSL-context cases also failed before correction: a valid environment
CA bundle changed the simulation/demo transport's trust store from118roots to1,
despite the client-level environment flag. The explicit transport now also sets
`trust_env=False`; both cases pass. Their destinations remain fixed local HTTP;
the Responses HTTPS adapter already set both flags. No live provider call was
made. Evidence: `m7-sse-expiry-{asgi,tcp}-red.txt`, `m7-sse-expiry-green.txt`,
`m7-transport-environment-{red,green}.txt`. Host cumulative unit verification is
265PASS/0FAIL/0ERRORS/0SKIPPED; installed-image/global evidence follows in the
final verification report.

## API recovery barrier after CI observation

The push CI run40631990690 recorded543integrationPASS/1FAIL at the final
post-recovery creation; its single POST returned503 after only the independent
test Redis client had recovered. The exact503cause is not established by that
log; the separate PR run progressed past the same test. Independent review found
that the observation barrier did not establish recovery of the actual API.
The existing PING loop was extracted without changing behavior (1PASS), then
maintained cases showed2FAIL/1PASS: false early success while the API remained
unavailable and no bounded timeout. The corrected read-only barrier requires
API Redis and submission readiness within the same5-second absolute bound;
GREEN3PASS. All outage assertions and the original single POST expected202 remain.
There is no POST retry and no application product-fix claim. The final suite now
contains812cases (268unit+544integration); the post-correction full/gates are
recorded separately. This helper's HTTP fixture proves only its waiting semantics;
real service recovery is still exercised by the F19 integration campaign.

## Independent demo content erasure and F20 remote-expiry closure

The global review found that platform purge did not erase the independent demo's
full report payload. The approved correction reuses the verified task termination
clock, supplied as trusted administrative evidence, rather than inventing a
first-receipt retention clock or changing the business report protocol.

`m7-demo-retention-red.txt` first records a meaningful PostgreSQL-backed payload
assertion failure. The new operator command and compatible independent-DB SQL
addition atomically audit and erase content while retaining owner, operation,
effect/hash/proof and first receipt. A separate least-privilege operator group
can execute the definer but cannot rewrite/delete effects or mutate the audit;
the HTTP service, worker and API gain no erasure credential or privilege.

The expanded real-DB/TCP suite covers valid/young/future/naive/non-UTC/conflicting
identity/hash evidence, repeat/lost acknowledgement, two observed lock waiters,
private immutable audit, actual HTTP replay/conflict and one-effect retention.
A real driver commit followed by a synthetic lost acknowledgement leaves one
durable decision and replays the same result. Two controlled remote-expiry cases
retain nonzero money/token provision and local identity/hash while proving zero
HTTP after the task deadline or authorization horizon. Historical fixture-clock
aging is confined to one privileged test transaction and restores USER triggers;
production guards and assertions remain intact.

A further meaningful DST RED rejected eligible evidence under a non-UTC SQL
session: calendar30days differed from elapsed720hours by3600seconds. The maintained
POSIX transition fixture makes that boundary repeatable year-round. Definer/guard
and admin connection now explicitly use UTC; `m7-demo-retention-dst-green.txt`
records16PASS/0FAIL/0ERRORS/0SKIPPED in30.29s. Earlier strict-JSON decoding, lock
observation and immutable-clock fixture mistakes remain preserved, not counted
as product RED. Final cumulative suite: 828 cases (268 unit + 560 integration), all PASS.

See the demo-retention runbook for explicit migration of existing volumes, manual
trusted terminal extraction, separate login provisioning and bounded maintenance.
Automatic deletion exactly at day90 is not claimed: minimal dedup metadata is
conservatively retained beyond the remote guarantee. Paid provider validation
and production backup/TLS/credential scheduling remain separate explicit skips.

## Final observed result (supersedes earlier pending candidate notes)

The final unfiltered installed-image suite records **828 PASS / 0 FAIL / 0 ERRORS /
0 SKIPPED in 313.43 seconds**. All 21 executed named gates pass; paid OpenAI smoke
is separately SKIPPED. The exact reference campaign passes every target with
1,000 accepted/succeeded tasks, zero stream errors, 100 connected/peak slots,
400 sessions, 10,016 reads and independent request/effect counts. Nominal plus
warmup has 2,120 distinct model POSTs and 1,060 report writes/effects; separate
saturation adds exactly one model POST and retains 1,000 micro-USD exposure.

Two CI concurrency failures and one focused F19 startup failure are retained
as harness evidence, without inventing precise product causes. Independent actor
readiness is established before the unchanged SQL contention window. The F19
setup now establishes one stream at a time within the same global 25 seconds,
keeps all 100 open before fault, uses one attempt each and surfaces startup
failures. Every business timeout and prior assertion remains. Focused F19 passes
all four cases; complete regression was rerun after that correction. The exact
load harness and production runtime were unchanged by these test-only edits.

The final reference run followed a non-destructive expansion of the dedicated
Docker data filesystem from 20 to 40 GiB (97% to 48% used), keeping 4 vCPU/8 GiB
and the same SSD class. No other local test workload ran during measurement.
See M7-verification.md for exact commands, metrics, RPO/RTO, scans, skips and risks.
CI of the final documentation head is verified on PR#7 before handoff.
