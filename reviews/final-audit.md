# AI Task Execution Platform and Taskroom — remediation audit

**Verification dates** 2026-11-14 and 2026-11-15
**Backend base** `96f0c4bdde03672d1a62b8d0eaa5ceb72feef631`
**Taskroom base** `f16e9cdfec46c595267b2e9a94b9c12117e78907`
**Verdict** **APPROVE WITH CHANGES** for the isolated demonstration

| Severity | Evidence, impact and resolution |
| --- | --- |
| High | Redis 8.2.2 was pinned before the published server fix. Compose now runs Redis 8.2.11 from an immutable Alpine manifest. The runtime scan includes Redis, PostgreSQL, the proxy and the app. All four ARM64 images report zero High and Critical findings with the cached audit snapshot. [Redis vendor release](https://github.com/redis/redis/releases/tag/8.2.3) |
| Medium | Retried ambiguous Taskroom commands could clear the sole replay identity. The corrected browser retains the original body, key and execution epoch. A credential change blocks retries, and a confirmed command-history expiry requires explicit acknowledgement. |
| Medium | Taskroom stopped at the first 50 tasks or steps. Cursor navigation now exposes both histories and is covered by a browser fixture with 51 of each. |
| Medium | Admission aggregated settled past calls while holding the global lock. Revision `0012_admission_indexes` adds partial and task indexes. The query uses an indexed UTC range union for unresolved exposure and keeps cap and lock semantics. A real PostgreSQL fixture adds 12,000 calls, forces generic plans and verifies exactly one concurrent near-limit reservation. |
| Medium | Next.js is locked to 16.4.6 and Taskroom CI audits production dependencies. ATP also pins OpenTelemetry API and SDK 1.45.2 after a fresh advisory. [OpenTelemetry release](https://github.com/open-telemetry/opentelemetry-python/releases/tag/v1.45.2) |
| Medium | PostgreSQL moves to 17.12 on Alpine. Its service UID and locale implementation change. The runbook requires verified logical recovery on fresh volumes for both databases. A synthetic Debian-to-Alpine cutover restored fixture rows in both services. Existing production databases require their own migration and preflight. |

| Check | Result | Evidence |
| --- | --- | --- |
| Backend maintained suite | PASS | 271 unit plus 563 integration cases, 0 failed and 0 skipped, on the final local run. Includes permissions, concurrent accounting, migration, revision/fencing, SSE and delivery faults. |
| Real degraded mode | PASS | 100 SSE streams, 120 reads, 9.1 ms observed read p95, zero stream errors, one remote effect. New admissions closed while replay and control remained available. |
| Compose smoke | PASS | Two subsequent runs passed on the final images. An earlier run failed the Redis-outage control assertion. The available logs do not establish its cause. |
| Image verification and restore | PASS | Immutable runtime and installer exclusion, four image scans, age tamper rejection, restoring two points, credential isolation, quarantine and verified reopening. The first run on the final image set ran out of disk. The full suite and restore were repeated after expanding the disposable VM disk. |
| Taskroom | PASS | 17 Node tests, build, lint, format, types, production advisory audit and three browser fixture journeys. A separate browser run created and cancelled a synthetic task against the real loopback backend, PostgreSQL and Redis. |
| Real OpenAI, sustained reference-load replay, off-host restore and actual production cutover | SKIPPED | Local fixture and degraded-mode runs cover bounded scenarios. No paid provider request or off-host production resource was used. |

This project shows durable command replay and transactional accounting with explicit uncertainty boundaries. The standalone Taskroom patch starts from the separate feature branch.
