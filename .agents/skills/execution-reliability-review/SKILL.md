---
name: execution-reliability-review
description: Review AI Task Execution Platform durability, concurrency, leases, retries, idempotency and crash recovery against the approved plan, with reproducible failure scenarios and test evidence.
---

# Execution Reliability Review

Audit the requested milestone or execution path without modifying implementation code or starting another milestone. Complement the existing `adversarial-review` skill and use the existing `verification` skill for executed checks. Resolve their locations through root `AGENTS.md`; do not recreate them.

## Scope and evidence

1. Read root `AGENTS.md`, `docs/architecture-plan.md`, the requested milestone and its acceptance gates. Consult `reviews/architecture-plan-review-response.md` for design tradeoffs; the original review covers an earlier specification.
2. Record the exact revision/files under review, implemented components, database roles, effective configuration and available tests. If code is absent, report a specification review and proposed tests, not verified runtime guarantees.
3. Trace task, step, attempt, external-call, provision and event lifecycles across API, worker, heartbeat, maintenance and reconciliation paths.
4. Map every claimed invariant to an enforcement layer and an independent negative test. Inspect constraints, service transitions and adapters together, without creating a second workflow engine.

Use plan §§2–4 for contracts, §5.2 for retention, §5.3 for disaster recovery and §§6–7 for milestone gates and F01–F22. Read configured limits instead of assuming initial defaults are deployed values.

## Durability and transaction boundaries

- PostgreSQL is the durable queue/source of truth; Redis supplies wakeups and HTTP limiting. Lost notifications must not lose work, reset durable limits or authorize duplicate progression.
- Creation commits task, first step, quota, event and retained response before `202`. Authenticate current credentials before replay. Scope idempotency by owner/route/key with versioned canonicalization; replay precedes mutable new-task admission. Check duplicate keys, changed bodies and distinct keys competing for the last quota slot.
- A commits claim, attempt, lease and generation. B commits exact call identity/payload, current permissions, provision and shared capacity. C commits accepted result, known usage, continuation and events. No HTTP call holds these transactions/connections open.
- Verify the lock order on every mutation: platform/global account → principal/account → API key → workflow → sorted document rights → provider admission → task/account → step → attempt → external call → call costs. Sort IDs within categories; never acquire earlier categories later.
- `READ COMMITTED` and `SKIP LOCKED` distribute work, not exactly-once execution. Check partial uniqueness of active attempts/executable steps, legal arcs, terminal-result immutability, expected state/version and one version increment per accepted business mutation.
- Retry complete local transactions only for confirmed `40001`/`40P01`, within bounds and excluding HTTP. Lost commit acknowledgement requires stable-identity rereads. Existing B authorization does not authorize another sender; ambiguous C requires checking committed completion before continuation.

## Ownership and state recovery

- Progression checks holder, generation, epoch, state and unexpired lease atomically under lock. Use fresh DB time after lock acquisition; never resurrect expired leases. Heartbeat uses a separate session/control pool, without task-version increments or public progress events.
- Maintenance rereads candidates under the correct locks, explicitly closes old attempts and cannot recover the same ownership twice. Test pauses and DB/pool failure as well as process exits.
- B is the authorization linearization point. Revocation before B blocks authorization; after B, a delayed first send remains possible. Fencing rejects stale local progression but cannot cancel remote work.
- Apply the coupled matrix in §2.4, including all nonterminal cancellation/deadline combinations. Check deadlines after locking before progression. Late evidence updates call/accounting records only when a task is terminal or the continuation was replaced.
- Before B, recovery is safe only with established rollback/no authorization. After unresolved B, preserve `unknown`, suspend the task and retain provision. Timeout, `5xx`, malformed/truncated response or absent remote lookup is not proof of non-execution.
- Ambiguous LLM calls never regenerate automatically. Explicit owner resolution needs expected version, accepted overlap/cost risk, current rights and remaining deadline/limits; immediately deselect the old continuation and retain its exposure.
- Demo-tool resend uses a new transmission ID but the same business operation key, exact bytes and hash. Verify remote transactional effect/key/hash/response storage, authenticated owner-scoped lookup and `409` for changed body. Enforce bounded probes/retries, guaranteed deduplication and the shorter of deadline/24 hours since first authorization; absent payload forbids resend.
- Local tools check rights and increment their own counter transactionally; no invented external-call provision. Capacity waiting consumes no dispatch authorization and creates dated eligibility rather than busy polling.
- Persist workflow/document/context versions, provider protocol items and tool-call IDs across turns. Missing compatible handler suspends; no silent substitution, hidden summary, repair generation or provider fallback.

## Budgets, timeouts and degraded operation

- B reserves task/owner/deployment money and tokens atomically. Shared provider windows and local active slots survive restarts. Idempotent local-slot release does not release unknown exposure or prove remote termination.
- Settlement replaces provision with confirmed usage, deduplicates evidence and checks revision. Partial/unknown usage stays provisioned through cancellation, UTC rollover and purge. Attribute charges to B's UTC day; daily admission includes all open provisions across days. Record overruns fully and block new affected authorizations as specified.
- Retries/regenerations count toward cumulative limits. Test absolute monotonic request deadlines plus pool/connect/read/write timeouts and size caps; slow-drip responses cannot run forever. Observe cooperative cleanup/supervised termination separately from remote activity.
- Redis failure yields new-creation `503`, while authorized replays, bounded reads/control and PostgreSQL polling continue. Verify protected heartbeat/control capacity under SSE pressure.
- Check coherent snapshot/watermark, per-task event sequence, owner-scoped replay and explicit `410`/`stream.reset` on retention gaps. Duplicate delivery is allowed; duplicate local transitions or unexplained missing events are not.
- Purge preserves minimal identities/evidence while exposure is open; missing exact payload disables replay. Restore requires external dispatch lock, isolation of old workers, new epoch/current credentials, quarantine of every restored nonterminal task even without call rows, conservative exposure and evidence-based reopening. No claim of recovering acceptances absent from backup or zero RPO.

## Reproducible failure scenarios and tests

Use disposable real PostgreSQL/Redis when their semantics matter, independent processes/connections, explicit barriers and observed lock contention. A start barrier alone does not prove contention. Use a deterministic HTTP simulator with a durable invocation/effect journal independent of application rollback and restored snapshots. No paid calls or live fault injection by default.

Each scenario records setup/seed, effective limits, injection hook/transaction boundary, ordered actions, timeout/margin, SQL oracle, remote-request/effect oracle and cleanup. Run existing tests; create regression harnesses only if the request permits test-file changes, otherwise provide reproducible test specifications. Do not implement features to make a review executable.

| Plan / milestone | Reproduction | Required assertions |
|---|---|---|
| F01–F02 / M2 | Fill last slot, drop accepted HTTP response, withdraw workflow and replay; race equal/different keys and bodies. | Original task/response and one quota charge; one last-slot admission; changed-body conflict. |
| F03/F06/F07 / M2–M3 | Pause old worker around B, expire lease; order heartbeat, two reapers, C, cancel/deadline and revocation with barriers. | One ownership/terminal outcome; no expired renewal, stale continuation or executable child after termination. Count already-authorized late sends separately. |
| F04–F05 / M3 | Kill before B commit, after B before bytes and drop B commit acknowledgement. | Before B: zero requests/orphan provisions. After uncertain B: unknown/suspension, retained exposure, no automatic replacement LLM generation. |
| F08/F10 / M3–M4 | Drop C acknowledgement; issue concurrent resolve; deliver old result after regeneration/termination. | One selected continuation/event sequence; deduplicated settlement; distinct provisions; old evidence cannot replace terminal output. |
| F09 / M3 | Slow-drip HTTP, stall event loop, exhaust execution pool, disconnect DB during heartbeat/result contention. | Measured absolute timeout/cleanup, visible lease loss, protected control capacity; no false remote-stop claim. Recovery margin includes remaining lease, scan and contention. |
| F11–F12 / M4 | Restart at every model/tool boundary, remove handler; commit demo effect then drop response and resend concurrently. | Same versioned context or explicit suspension; exact operation bytes/key; one independently counted remote effect. |
| F14–F16 / M3/M5 | Concurrent reserve/settle, repeated/conflicting evidence, UTC rollover, missing/excess usage, tiny shared quota and simultaneous restarts. | Aggregates equal call records, uncertainty retained, original-day attribution, overrun recorded, durable counters/bounded admission. |
| F17–F19 / M6 | Race snapshot/replay/purge; duplicate/drop notifications; cut Redis under the plan's SSE/read load. | Contiguous owner-scoped replay or reset; bounded resources; creation unavailable while replay/control/heartbeats work. |
| F20–F22 / M7 | Purge unresolved payload, expire replay window, restore before B/task creation with remote journal retained; remove handler/fail migration. | No reconstructed resend/blind dispatch; evidence/exposure retained, quarantine/external dispatch lock, measured loss window, compatible handler required. |

Cover all scenarios relevant to the authorized milestone/changed paths; label later-milestone scenarios deferred. Use actual repository commands corresponding to §6 (`make test-execution`, `make test-failures`, etc.) only when present. Missing commands/infrastructure mean unexecuted checks, not passes. For load/recovery claims preserve §7.2's workload and report actual resources, latency, rejection/suspension/timeout counts and measured RPO/RTO, not just task totals.

## Report

For each finding give Critical / High / Medium / Low severity, file/line or component, violated contract, concrete interleaving, impact, cause, existing coverage, minimal recommendation and required regression test. Distinguish confirmed defects, hypotheses and environmental blockers.

Report revision/scope, F-scenario coverage, exact commands/configuration, passed/failed/errors/skipped counts, checks not run with reasons, physical request/effect counts and simulator-versus-provider limits. Use existing `verification` reporting for executed checks and give an evidence-supported verdict. No findings does not prove universal durability. Return fixes to the implementation author and stop at the authorized scope.
