# M7 cumulative verification and release evidence

Date: 2026-10-28. **Local verification: PASS**, with the explicit skips and
remaining base-image inventory below. The complete unfiltered M1–M7 suite records
**828 PASS / 0 FAIL / 0 ERRORS / 0 SKIPPED in 313.43 seconds**:
268 unit cases and 560 real-service integration cases.

M1–M6 are merged on main; M6 is PR#6, merge
`b86111386e4c6e46683e9ffac2fc31d24da9bdc7`. There is no unmerged predecessor
dependency. M7 is feature/m7, PR#7. No M7 merge, auto-merge or production deployment
is authorized. The pre-existing AGENTS.md change is preserved outside task commits.

## Scope and durable guarantees

M7 adds bounded content/corpus erasure with retained financial evidence, audited
restore quarantine and disaster reserves, explicit close/drain/preflight/reopen,
compatible migration failure handling, encrypted recovery drills and immutable
artifact verification. The independent demo database now has evidence-based
content erasure using the verified task termination clock and a separate limited
operator credential. It preserves effect identity/hash/receipt and first receipt
time. No runtime role gains direct erasure or audit mutation.

The cumulative correction set also closes dispatch freshness after blocking work,
normal SSE lifetime framing and explicit transport environment isolation. All
existing tests, constraints, type checks and assertions remain enabled. PostgreSQL
owns queue/state/budgets/evidence; Redis remains an ephemeral optimization.

## Commands, configuration and exact gate results

Runtime source is `c4e129735dc4501af1d0a64462399b380120e748`. The runtime OCI manifest,
configuration digest, archive SHA-256, demo SQL hash and parameterized handler
compatibility requirements are in `evidence/m7-release-manifest.json`. A production
Responses profile is explicitly NOT_PROVISIONED; the template is not a configured
provider instance. Platform schema is `0011_recovery_authority`; existing demo
volumes explicitly apply `infra/demo-retention.sql` under the schema owner.

Python 3.14.8, uv 0.13.1, pytest 9.1.1, Ruff 0.17.0 and mypy 2.4.0 were used.
Real services are PostgreSQL 17.11, Redis 8.2.2, Nginx and the independent demo DB.
The context is `colima-ai-task-platform`, disposable main project `atp-m1-test`.
The API receives no OpenAI key. Ordinary CI and all load/recovery runs are synthetic.

```sh
# Coherent installed images; sources are frozen during both builds.
DOCKER_CONTEXT=colima-ai-task-platform docker-compose -p atp-m1-test \
  -f compose.yaml -f compose.demo.yaml -f compose.proxy.yaml build api test
COMPOSE=docker-compose DOCKER_CONTEXT=colima-ai-task-platform \
  .venv/bin/python scripts/run_verification.py -q --tb=short
# Actual fixed reference workload, run alone after stopping other local services.
make test-load
```

Local Make commands use COMPOSE=docker-compose, the same Docker context, the
checksum-verified age executable and a shell-quoted absolute uv path. Host tokenizer
checks use the already-baked cache. Missing tools/assets or connections never
count as behavioral RED. `run_verification.py` controls the actual Redis outage
outside the test container without exposing a Docker socket to the application.

| Command | Result | Pytest PASS cases |
|---|---|---|
| `make lint` | PASS | — |
| `make typecheck` | PASS | — |
| `make test-unit` | PASS | 268 |
| `make test-commands` | PASS | 183 |
| `make test-concurrency` | PASS | 35 |
| `make test-execution` | PASS | 75 |
| `make test-failures` | PASS | 24 |
| `make test-workflows` | PASS | 34 |
| `make test-tools` | PASS | 24 |
| `make test-e2e` | PASS | 3 |
| `make test-accounting` | PASS | 54 |
| `make test-provider-contracts` | PASS | 59 |
| `make test-sse` | PASS | 81 |
| `make test-degraded` | PASS | 4 |
| `make test-observability` | PASS | 19 |
| `make test-retention` | PASS | 56 |
| `make test-restore` | PASS | 46 |
| `make test-delivery` | PASS | 8 |
| `make test-load-contracts` | PASS | 53 |
| `make verify-release-image` | PASS | — |
| `make smoke-compose` | PASS | — |
| `make smoke-openai` | SKIPPED | — |

The table records 21 executed gates PASS and one paid smoke SKIPPED. Focused suites
overlap; their counts must not be summed into the unique 828-case total. The
concurrency gate deselects 525 unrelated integration cases; the complete suite is
unfiltered and skips none. After the final test-only actor/startup corrections,
the full suite and affected gates were rerun. Unchanged gates had already passed
on the identical runtime. Ruff/format and strict mypy pass across the configured
repository paths. Clean/upgrade/round-trip schema, structural guards, exact allowed
and forbidden privileges and failed migration rollback are included in those runs.

## Actual encrypted recovery

Two real custom-format PostgreSQL dumps were age-encrypted, authenticated and
restored into isolated databases. Live egress was blocked. Actual old DB/API/demo
credentials were rejected; new epoch/quarantine/reserve/preflight preceded safe
new admission. No old work was blindly resumed. An independent surviving journal
retains two model requests, one report write and one remote effect.

| Restored point | Quarantined tasks | Potential loss (RPO) | Safe reopening (RTO) | Accepted task absent |
|---|---:|---:|---:|---|
| atp_restore_before_b | 1 | 2.765 s | 4.021 s | False |
| atp_restore_before_creation | 0 | 5.906 s | 3.196 s | True |

Prior credential isolation took 0.870 seconds,
measured separately. RTO starts with archive verification/decryption/loading and
includes reconciliation and safe reopening; prior detection/isolation is excluded.
These small local synthetic archives (about 120 KiB) do not establish production
RTO or zero loss. Authenticated tamper rejection, preserved remote effects and
post-backup task loss are recorded in the restore gate's full JSON.

## Exact reference and bounded saturation

**make test-load: PASS.** The approved profile ran without downscaling: 60-second
warmup, 1,000 nominal tasks at one per second, 20 owners × 20 documents × 4 KiB,
4 KiB input, one API, two independent worker processes with four slots each,
100 simultaneous SSE connections and 10 reads per second. Synthetic boundary
delays are 500 ms model and 100 ms demo; reservation is 1,000 micro-USD and
1,200 tokens, with 600 RPM and 1,000,000 TPM configured for the nominal phase.

All 1,000 tasks were accepted and succeeded; zero nominal rejections or stream
errors occurred. Both workers remained alive. All 100 connection slots were
established, peak was 100 and 400 sessions include normal reconnects. There were
10016 reads. Nominal elapsed time was 1001.033 seconds.

| Measurement | p50 | p95 | p99 |
|---|---:|---:|---:|
| Creation | 0.023184 s | 0.083166 s | 0.165833 s |
| Queue | 0.032955 s | 0.164412 s | 0.194445 s |
| Completion | 1.431995 s | 1.567303 s | 1.612082 s |
| Reads | 0.007192 s | 0.029768 s | 0.037596 s |
| SSE direct (n=63) | 0.495007 s | 0.996918 s | 0.997204 s |
| SSE proxy (n=69) | 0.674309 s | 0.997384 s | 0.997497 s |

The SSE sample counts are published explicitly; these distributions cover observed
followed-task deliveries, not every event from all 1,000 tasks. Independent
journals record 2,120 distinct model POSTs and 1,060 report writes/effects including
60 warmup tasks. No duplicate call IDs or effects were found. The full raw result
is `evidence/m7-reference-load.json`; every acceptance target is true.

The separate saturation phase let the prior provider window expire naturally for
61 seconds without deleting history. It sent 120 requests: 99 HTTP202, 17 HTTP429
and four HTTP503, with 99 open tasks bounded below the 100-task cap. Shared limits
were 1 RPM, 1,200 TPM and one active call. Exactly one slow physical model request
occurred; 1,000 micro-USD uncertain provision remained, and zero executable tasks
remained after bounded deadline cleanup (12.948 seconds).
A timeout is not evidence that remote work stopped.

The Linux VM has four vCPUs and 8 GiB provisioned RAM (Docker reports
8,308,355,072 usable bytes), on macOS ARM/Lima VZ with a host SSD-backed APFS disk
image. No other local test workload ran during measurement. The actual Docker
DATA filesystem, distinct from the VM root, reached 97% (687 MiB free). Before the
final benchmark it was expanded from 20 to 40 GiB without deletion: 48% used,
20 GiB free, with CPU/RAM and workload unchanged. The prior failed run used the
smaller disk. This is not a controlled A/B latency experiment or a production
capacity/SLA claim. Exact environment and capacity evidence are retained.

## TDD, independent review and retained failures

`M7-test-matrix.md` indexes the maintained normal/boundary/security/concurrency/
idempotence/crash/recovery cases and meaningful pre-implementation RED/GREEN.
G1 and R1–R11 were independently reviewed using adversarial, execution-reliability
and agent-security skills. The implementation author does not count self-review
as independent approval. The global report and retention/recovery increments
retain findings, corrections, commands and limits.

The original full reference campaign remains FAIL in
`m7-reference-load-attempt1-fail.json`: all 1,000 tasks succeeded but 300 normal
SSE expirations produced transport errors. ASGI and physical TCP RED reproduced
incomplete chunk framing; closing grace inside the same hard lifetime fixed it.
The corrected campaign above has zero such errors. Original unfavorable metrics
were preserved instead of being relabeled or overwritten.

Other historical failures remain separate: 808 PASS/1 FAIL from an old cancelled
smoke row before a clean full rerun; CI 543 PASS/1 FAIL at a post-recovery API
observation; two CI runs with 559 PASS/1 FAIL from a concurrency fixture that
started heavy processes inside its observation window; and a focused CI F19
startup result of three PASS/one FAIL while the same commit's PR CI passed.
Precise transient causes are not invented. Explicit readiness barriers synchronize
real actors, and 100 SSE streams are established within the same 25 seconds before
the outage. Every old assertion, one-attempt rule and business timeout remains.
Syntax, immutable-clock fixture and missing-asset/tool failures are setup errors,
not product RED. No retrospective RED was manufactured.

## Supply chain, skips and operational limits

The locked runtime and rebuilt runtime Python inventory have zero vulnerability
findings. Actual artifact assertions verify no pip/ensurepip in either runtime
interpreter and pinned libssl3/openssl 3.0.22-1~deb12u2. The full raw OS scan remains
**244 findings: 2 CRITICAL, 59 HIGH, 91 MEDIUM, 89 LOW, 3 UNKNOWN**. No scanner ignore
list or severity suppression was used. The SQLite inventory issue remains a
production triage risk; Debian's zlib/minizip scope explanation does not hide the
raw scanner entry. See the supply-chain report and retained scanner DB/version.

- Paid OpenAI smoke and actual model-quality campaign: SKIPPED, without separate
  authorization, synthetic-data scope and spending cap. No paid request was made.
  Protocol/semantic fixtures do not validate actual provider billing or model quality.
- Off-host backup transport/daily production schedule: SKIPPED, no destination or
  production credentials. Local encryption and actual restore are tested.
- Production TLS/deployment, registry publication and production operator-login/
  maintenance scheduling: SKIPPED, outside the supplied configuration/authorization.
  The four runbooks describe explicit operator prerequisites and actions.

CI uses simulation, verified pinned tools and raw scan artifacts. The final PR
head is checked before handoff; its exact run links and final status are recorded
in the PR description. No merge or auto-merge is performed. Documentation/artifact
checks preserve AGENTS.md and scan public evidence for credential patterns and
known development secret literals; private archives, keys and receipts stay out
of Git.
