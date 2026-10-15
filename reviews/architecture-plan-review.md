# AI Task Execution Platform — Independent Architecture Plan Review

Review date: 2026-10-14  
Verdict: **APPROVE WITH CHANGES**  
Findings: **0 Critical / 4 High / 8 Medium / 3 Low**

## 1. Scope, evidence, and limits

This is a design review of the complete supplied plan, sections 1–7, including M1–M7 and the deferred features. It is not a code audit, implementation approval, or evidence that the proposed guarantees have passed testing.

Source: [original plan](</Users/william/.codex/attachments/6185158b-b221-404e-90e0-a9b30a8bc6fa/Texte collé.txt>). Line references below refer to this 609-line attachment.

Source SHA-256: `9d1d0edadb344d8afcc60a6262cfbee04fdd845937a1fa3e8fbb13f90d853a67`.

The target workspace was inspected: it was empty, with no Git repository or applicable AGENTS.md found in its ancestor directories. No implementation, migrations, dependency lockfile, provider configuration, benchmark, or test results were available. Only this review report was created. The source plan was not edited.

Method: trace each lifecycle across admission, authorization, external dispatch, completion, recovery, cancellation, expiry, reconciliation, deletion, and restoration; construct adverse interleavings; compare relevant technology assumptions with primary documentation. The scenarios below are analytical counterexamples or required tests, **not executed reproductions**. No application tests or paid API calls were run. Documentation was consulted on the review date; dependency and model compatibility still require validation against the versions eventually pinned.

Severity describes impact if a gap is implemented literally. High findings affect central correctness or security contracts and need a design decision before the affected milestone. Medium findings need closure before their feature is accepted. Low findings concern proportionality and operational clarity. No Critical defect is established by the plan alone.

## 2. Overall feasibility

The proposed stack is technically suitable for the stated moderate-volume, single-server MVP. A Python modular monolith with separate FastAPI and worker processes, PostgreSQL-backed scheduling, and sequential durable agent turns is proportionate. Redis can remain a disposable notification layer. Building this restricted executor is feasible; building a general workflow platform would be a different undertaking.

Several choices should be preserved:

- Persist work and domain transitions in one PostgreSQL transaction; do not hold transactions across external HTTP or SSE lifetimes.
- Use leases and generations for local ownership, remote idempotency for supported external effects, and explicit uncertainty for ambiguous LLM calls.
- Persist model decisions before executing tools. Keep the report-writing tool outside the model's tool set.
- Keep workflow definitions immutable and code-owned; defer DAGs, arbitrary code, dynamic subagents, and multi-provider fallback.
- Separate semantic report quality from execution reliability, and local cost admission from the provider's actual invoice.
- Test real PostgreSQL contention and failures, with deterministic provider simulation and separate live API checks.

PostgreSQL explicitly supports `SKIP LOCKED` for queue-like access, while warning that it provides an inconsistent view. This supports its proposed scheduling role, not a broader correctness guarantee. [PostgreSQL SELECT](https://www.postgresql.org/docs/current/sql-select.html)

The separate-session rule is correct: concurrent asyncio units must not share an SQLAlchemy `AsyncSession`. Heartbeats and reconciliation also need their own sessions. [SQLAlchemy asyncio](https://docs.sqlalchemy.org/en/20/orm/extensions/asyncio.html)

The architecture does not require Celery, Kafka, Temporal, vector storage, RLS, or an arbitrary-code sandbox for this particular MVP. Their absence is not itself a defect. The main work remaining is to make existing contracts consistent and testable.

## 3. Findings

### H1 — Idempotency replay is ordered after mutable admission checks

**Severity:** High. **Evidence:** section 3, lines 149–159; quota in lines 333–334. **Status:** explicit ordering defect, plus an unspecified concurrency mechanism.

**Scenario:** a principal has four open tasks. A fifth submission commits, but its HTTP response is lost. The client retries with the same key. The prescribed algorithm checks the open-task quota before resolving the key, so it can reject the retry instead of returning the saved `202`. A subsequently disabled workflow version can produce the same problem. Separately, concurrent submissions with different keys can both observe four open tasks unless admission is serialized; uniqueness on idempotency keys cannot enforce this aggregate quota.

**Impact:** the replay contract fails precisely during response-loss recovery; the five-task quota may be exceeded.

**Correction:** authenticate and enforce access to the existing command, perform bounded parsing and canonicalization, then resolve an existing idempotency record before mutable *new-task* admission checks. Replay must never bypass revoked credentials or ownership checks. For an absent key, serialize principal admission using a principal/quota row, recheck the key, and atomically create the task and stored response. Define the position of that lock in the global lock order. Retain a stable canonicalization version; do not recompute request identity using changing server defaults. A same-key/different-request collision remains `409`.

**Acceptance:** test replay at quota saturation and after workflow retirement; race distinct keys for the last quota slot; race identical and conflicting bodies; demonstrate only one new slot is consumed and a valid replay returns the original response.

### H2 — The state machines do not cover the promised recovery and cancellation paths

**Severity:** High. **Evidence:** section 2, lines 89–135; recovery in lines 354–369. **Status:** incomplete and partly inconsistent specification.

The following paths lack defined transitions or coupled-state rules:

- A queued task can become `cancelled`, but its `pending`, `ready`, or `retry_wait` step cannot become cancelled in the supplied step graph.
- A prepared call whose dispatch is prevented by cancellation has no explicit closure path. If preparation and authorization are one atomic operation, explain why `prepared` is durable at all.
- Attempts have no lifecycle, including how an expired active attempt is closed before a replacement satisfies the one-active-attempt constraint.
- A suspended task whose `unknown` step becomes `failed` has no direct task failure transition. If the intended path is through `queued → running`, that must be an explicit scheduler action rather than an assumption.
- Cancellation recovery, including a crashed worker leaving `cancelling`, has no specified finalizer or completion bound.

**Impact:** stuck tasks, orphaned active attempts, reserved resources never released, or contradictory task/step/call combinations. Terminal-state triggers could make the omitted paths impossible to implement later.

**Correction:** supply a transition matrix with actor, precondition, task version, lease requirements, child-state changes, reservation effects, and public event. Define attempts as historical ownership records with explicit completion/expiry. Mark unsent calls as aborted/not dispatched, or eliminate the unnecessary persistent state. Define cancellation and timeout cleanup for every nonterminal step state. Reconciliation may update call evidence and costs after task termination but must not reopen orchestration or overwrite the terminal task result. Distinguish immutable terminal results from still-evolving accounting metadata.

**Acceptance:** enumerate reachable combinations across all machines, then test cancellation, timeout, recovery, and resolution at every nonterminal combination. Every surviving nonterminal combination must have an eligible worker action, a scheduled recovery action, or an explicit user-intervention reason.

### H3 — Permission revocation has no defined ordering against durable dispatch authorization

**Severity:** High. **Evidence:** transaction B, lines 172–183; fencing limitation, lines 348–350; revocation promise, lines 394–399. **Status:** security contract ambiguity, not a demonstrated exploit.

**Scenario:** worker A commits dispatch authorization, then pauses before network transmission. The permission is revoked or the lease expires. A resumes and sends the request. A final PostgreSQL ownership check prevents its result from advancing the workflow, but cannot retroactively prevent that request. Rechecking immediately before HTTP reduces the window without removing it. The plan discusses external calls already received, but the gap also includes calls authorized locally and not yet transmitted.

**Impact:** a literal promise that revocation prevents every subsequent physical send is stronger than this design can deliver. Manual regeneration can also overlap with the original authorized request; its uncertain provision must remain.

**Correction:** make the authorization commit the explicit linearization point. Permission updates and dispatch authorization must synchronize through a defined permission version/lock protocol. Revocation prevents authorizations ordered after it; requests authorized earlier may still be transmitted or complete. Cancel local work on lease loss where possible, retain uncertain liabilities, and communicate possible overlap when a new generation is explicitly authorized. Do not describe a local fencing token as remote fencing. Strict rejection of old requests at the external target would require a receiver-enforced capability/epoch contract not established here.

**Acceptance:** pause before and after authorization commit, race revocation/cancellation/lease expiry, then resume the process. Assert the documented ordering and that no stale worker advances the task. Count physical provider calls independently of database transitions.

### H4 — Restore quarantine cannot rely on call records present in an old backup

**Severity:** High. **Evidence:** section 5, line 417; M7, lines 548–557; backup objectives, line 591. **Status:** recovery algorithm gap and overbroad acceptance wording.

**Scenario:** a backup contains a queued task with no external call. After the backup, the task generates a report and saves it remotely. Restoration makes the task queued again, with no evidence of either operation. Quarantining only restored `dispatching` or `unknown` calls would miss it. Tasks accepted entirely after the backup may disappear, together with their idempotency keys. Retrying such a command can create a new UUID and therefore a different remote operation key.

**Impact:** repeat generations, duplicate remote business effects under new identities, and untraceable accepted work. The plan correctly recognizes restore risk, but does not specify how to identify the affected set. A daily backup does not prove that no accepted work can be silently lost after host loss.

**Correction:** keep an explicit restore gate outside normal task recovery, stop or revoke old worker access, and establish a new operational epoch before enabling dispatch. Conservatively quarantine **all restored nonterminal tasks**, including those with no call rows, unless independent evidence narrows the set. Reconcile external operation keys before any replay. Document the unrecoverable post-backup interval and what clients/operators are told about lost idempotency history. If loss detection for every acceptance is required, an independently durable acceptance journal or stronger replication/archiving design is needed; it is not implied by daily backups.

Qualify the no-loss guarantee to ordinary process/dependency failure with committed PostgreSQL state intact. State the separate disaster-recovery loss envelope and measured RPO. PostgreSQL backup mechanisms are available, but application/external-system reconciliation is a separate obligation. [PostgreSQL backup and restore](https://www.postgresql.org/docs/current/backup.html)

**Acceptance:** restore a snapshot taken before dispatch, restore one before task creation, retain a remote effect while rewinding local state, and ensure no automatic regeneration occurs. Also test that an isolated restore exercise cannot send traffic to live external destinations.

### M1 — Cost settlement, token admission, and daily rollover need a precise ledger contract

**Severity:** Medium. **Evidence:** lines 80–81, 185–190, 331–338, 369–390. **Status:** underspecified invariant.

**Scenario:** an uncertain call is resolved twice through racing admin actions; or usage arrives after the task has timed out. Without unique accounting operations, a reservation can be released twice, charged twice, or remain permanently unresolvable. Task-result immutability must not prevent recording later financial evidence.

**Correction:** define unique reservation and settlement identities per external call, with atomic updates to task and principal exposure. Settlement replaces the reservation with known usage plus any remaining uncertainty; it does not add both in full. New generations get separate reservations. Require an authenticated reconciliation path for late observations that cannot advance the workflow.

Specify daily timezone, usage attribution day, and rollover formula. For example, admission can check today's settled charges plus all outstanding reservations/provisions across days plus the new reservation against the daily limit. Document how a later settlement attributed to an older day removes its outstanding exposure. Record actual overspend without clamping or rejecting the observation; block further admission and retain an auditable overrun even if available budget becomes zero.

Reserve tokens as well as money. Persist tokenizer/version, full input estimate, output cap, tariff, and rounding policy. Define whether the eight-call limit counts dispatch attempts, logical turns, or both; explicitly authorized regenerations must not reset task-wide limits. No exact provider-token cap follows from an unverified estimator.

**Acceptance:** concurrent reserve/settle/resolve, repeated settlement after lost commit acknowledgement, late usage after cancellation, rollover with multiple outstanding calls, and actual usage exceeding reservation. Assert conservation of exposure and no duplicate accounting entries.

### M2 — Absolute deadlines and lease ownership require an executable runtime contract

**Severity:** Medium. **Evidence:** lines 179–187, 325–330, 344–352. **Status:** implementation-sensitive gap.

**Scenario:** a provider sends small chunks indefinitely; a CPU-heavy tokenizer blocks the event loop; or heartbeat renewal waits behind an exhausted connection pool. These can outlive a nominal HTTP timeout or expire a lease while the process is still active.

HTTPX read timeouts apply to waiting for a chunk, not the total request duration. An outer elapsed-time deadline is required. [HTTPX timeouts](https://www.python-httpx.org/advanced/timeouts/)

**Correction:** derive the operation deadline from the minimum of task time remaining and per-call limit, using monotonic elapsed time locally and a consistent database clock for persisted leases. Bound connection-pool acquisition, database lock waits, response bytes, and cleanup time. Renewal must conditionally check holder, generation, current state, and non-expiry; an expired lease must not be revived. Reserve heartbeat capacity and stop scheduling new work when ownership is lost. Recovery and result processing must obey the documented budget-before-task lock order whenever both are needed; never take the budget lock later while already holding a task lock.

Async cancellation is cooperative, not a hard process kill. Python documents that cancellation completion can exceed a timeout. Keep trusted computations bounded/off the event loop; use a terminable process only where hard containment is actually required. [Python asyncio tasks](https://docs.python.org/3/library/asyncio-task.html)

The 60-second lease and 60-second LLM limit are not intrinsically inconsistent because renewal exists. They are nevertheless unvalidated defaults. Recovery latency includes remaining lease, scan delay, contention, and scheduling; it is not simply five seconds.

**Acceptance:** slow-drip HTTP, blocked event loop, exhausted pool, renewal/reaper race, expired renewal, database outage, and cancellation during cleanup. Measure the local stopping bound separately from remote execution duration.

### M3 — SSE replay lacks a complete snapshot, retention, and revocation protocol

**Severity:** Medium. **Evidence:** lines 248–261, 525–533. **Status:** protocol gap despite a sound persistence design.

**Scenario:** after `410`, a client fetches current state and reconnects without a matching sequence. An intervening transition can be skipped or older events applied to newer state. A stream authenticated only once may continue after key revocation. Retention can remove the next batch after a stream has already sent HTTP `200`.

**Correction:** expose a snapshot with its event watermark from one consistent database read; reconnect strictly after that sequence. Define initial/no-cursor behavior, invalid and future cursors, task scoping, oldest retained sequence, and deletion responses. Query bounded batches in sequence order and advance only through emitted events. Detect retention gaps throughout the stream. Return `410` before headers; after headers, emit a documented reset/control message and close. Recheck key/principal validity at a bounded interval and fail closed when validity cannot be established beyond that bound. Define whether a terminal stream closes while later accounting events remain available through replay/usage.

`Last-Event-ID` provides reconnection state, not database retention or business-level delivery acknowledgements. Browser `EventSource` also does not expose arbitrary Bearer-header configuration; the planned compatible client is necessary. [HTML SSE standard](https://html.spec.whatwg.org/multipage/server-sent-events.html)

**Acceptance:** snapshot/reconnect race, retention during batch replay, revocation on an existing stream, future cursor, reconnect storm, and slow client. Verify proxy flushing and bounded polling latency when Redis is unavailable.

### M4 — Durable context reconstruction and executable workflow versions are not fully specified

**Severity:** Medium. **Evidence:** lines 76, 85–87, 201–223, 276, 411, 587. **Status:** missing replay contract and ingestion interface.

**Scenario:** a deployment changes a handler or a corpus document while a task is paused. The next turn reconstructs a different context despite the stored workflow label. Saving only final model text and tool arguments can also omit provider items required by the next call.

**Correction:** pin document IDs, immutable versions, excerpt provenance, prompt/tool schemas, and handler compatibility for the task. Define a supported document/corpus provisioning path: either inline inputs plus an administrative corpus-import command, or a small authenticated text API. File ingestion and web fetching can remain excluded.

Persist the normalized provider response items needed for replay, tool-call identifiers, validated outputs, and context-builder version. OpenAI function calling requires matching call/output identifiers; reasoning-model continuations may require reasoning items, with encrypted state available for stateless use. Saving only `output_text` is not a sufficient general replay contract. [OpenAI function calling](https://developers.openai.com/api/docs/guides/function-calling), [OpenAI reasoning guide](https://developers.openai.com/api/docs/guides/reasoning)

Define deterministic context selection/truncation, including what happens when input is within the byte limit but exceeds model or cumulative token limits. Prefer bounded excerpts or an explicit input rejection over adding an unbudgeted summarization subsystem. Workers must claim only supported workflow versions; an unsupported version must become visible to operators rather than endlessly lease/retry. Preserve compatible handlers or deliberately suspend tasks during upgrades and rollback.

**Acceptance:** resume across deployment, mutate/revoke a source document between turns, reconstruct an identical request from persisted inputs, and replay a tool round trip after process restart using the pinned provider contract.

### M5 — Security controls constrain authority but do not constitute a sandbox or semantic injection guarantee

**Severity:** Medium. **Evidence:** lines 215–230, 392–415, 491–495, 574–578. **Status:** threat-boundary and test-oracle gap.

The plan correctly excludes arbitrary code and gives the model read-only tools. A container sandbox for model-generated code is therefore unnecessary. However, schema validation and an existing citation do not prove that a report is truthful or supported by that citation. A malicious document can induce a schema-valid, misleading report that is automatically saved through the authorized deterministic step.

**Correction:** define the attacker as controlling submitted text, document content, and model/tool output, while application code and administrators remain trusted. State explicitly that the worker process is not a sandbox for arbitrary plugins. Keep source text out of instruction roles; authorize every retrieved resource and limit returned excerpts; validate citation ownership/version and, where feasible, quoted spans. Evaluate semantic contamination separately and do not promise universal prompt-injection resistance. Persisted report content must remain untrusted to any future HTML renderer or downstream automation.

Specify the demo service's authentication and isolation, fixed destination handling, redirect policy, and rejection of model-supplied URLs/headers. Use non-root containers, minimal mounts/capabilities, and resource limits as ordinary deployment hardening, without claiming protection against host or application-role compromise. The same image can support multiple roles without giving the API process worker-only provider credentials.

OWASP supports reducing tool functionality, permissions, and autonomy; it does not turn model output into trusted content. [OWASP Excessive Agency](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/)

**Acceptance:** forged tool outputs, cross-owner references inside nested arguments, malicious instructions in allowed documents, redirect attempts, and stored active content. Unauthorized actions must be deterministically rejected; semantic evaluation gets measured criteria and disclosed residual failures.

### M6 — Purge and remote idempotency retention can destroy recovery evidence

**Severity:** Medium. **Evidence:** lines 159, 225–230, 336–338, 409–417. **Status:** lifecycle inconsistency risk.

**Scenario:** a task times out with an unresolved call. Thirty days later, its task/step/request rows are deleted but a cost provision remains. An operator cannot verify the original request or reconcile the operation. Alternatively, a remote key expires before a restore/manual replay and a same-key resend produces another effect.

**Correction:** define separate retention schedules for sensitive payloads, task tombstones, operation identities, idempotency responses, accounting, audit, and backups. Keep a minimal owner-scoped call/evidence record while a liability or reconciliation remains unresolved; avoid cascading deletion of it. If the exact resend payload is purged, mark replay unavailable rather than reconstructing a new payload. Set a finite automated/manual replay horizon and require the external deduplication contract to cover that horizon and permitted restoration window. After the horizon, reconcile or stop; never assume the old key is still effective.

The plan's `store=false` caveat is correct: it is not a blanket zero-retention promise. Document the selected model/features and account's actual provider data controls, separately from local deletion and backup expiry. [OpenAI data controls](https://developers.openai.com/api/docs/guides/your-data)

**Acceptance:** purge with unresolved calls, settlement after payload deletion, remote deduplication expiry, same-key/different-body replay, and restoration near the retention boundary.

### M7 — Per-user limits do not bound shared provider capacity or total platform exposure

**Severity:** Medium. **Evidence:** lines 300–317, 325–335, 373–390. **Status:** missing shared admission policy.

**Scenario:** many provisioned principals remain within their individual budgets but exhaust one provider project's capacity or collectively exceed the intended deployment spend. HTTP rate limiting does not regulate worker-side provider traffic. During Redis failure, unrestricted read/SSE polling can consume database capacity needed by heartbeats and cancellation.

**Correction:** add a small deployment-wide spend/exposure limit and shared provider concurrency/rate admission across worker processes. Respect project/model request and token limits, with bounded retry delay and deadline-aware scheduling. Provider limits can apply independently to requests and tokens. [OpenAI rate limits](https://developers.openai.com/api/docs/guides/rate-limits)

Set explicit global and per-principal caps for pending work, active calls, SSE connections, response/payload sizes, and retained storage. Five nonterminal tasks is not a daily throughput or storage quota. Choose a simple documented scheduling policy and measure starvation; a general weighted scheduler is unnecessary. Protect essential control routes and database capacity during degraded operation. Redis loss may reduce availability, but must not silently reset a durable financial cap.

**Acceptance:** multiple principals compete for a small provider limit, all workers restart together, Redis fails under read load, and many short completed tasks accumulate. Demonstrate bounded resource use and preserved control-plane access.

### M8 — Milestone sequencing postpones core contracts and acceptance remains partly non-measurable

**Severity:** Medium. **Evidence:** M2–M7, lines 441–559; validation, lines 565–591. **Status:** planning and evidence gap.

M3 tests external execution before M4 owns ambiguity resolution, while M5 introduces the budget part of transaction B. This is feasible with simulation, but leaves foundational schemas and transitions vulnerable to redesign. M4's injection test has no distinction between an unauthorized action and misleading yet authorized content. M7's workload does not state arrival rate, service latency, corpus size, or whether all 1,000 tasks must complete before their 15-minute deadlines.

**Correction:** specify cross-cutting state, call, token, and reservation contracts before M2/M3; introduce them incrementally with the deterministic simulator. Keep real provider access in M5. Make executable fault injection and basic execution metrics part of M3, not a final-week exercise. Describe every acceptance claim with workload, fault boundary, database/provider oracle, and allowed outcome, including `suspended` or `timed_out` where appropriate.

The plan already requires no hidden HTTP retries. Make this concrete if the official SDK is chosen: explicitly disable SDK retries, pin the transport stack, and test physical request counts. The current Python SDK documents automatic retries and a configurable `max_retries=0`; relying on its defaults would contradict the intended policy. A direct HTTPX adapter instead needs the same evidence for its transport/proxy layers. [OpenAI Python SDK reference](https://developers.openai.com/api/reference/python)

**Acceptance:** use the revised gates and failure matrix below. Report failures, errors, skips, and unrun checks; a green simulator suite does not establish actual provider behavior, throughput, or semantic quality.

### L1 — Redis is optional for execution correctness but required for submission availability

**Severity:** Low. **Evidence:** lines 296–317.

This is a valid deliberate tradeoff, not an architectural contradiction: fail-closed submissions differ from ongoing execution. State this distinction in health/readiness and deployment documentation. Do not mark the whole API unusable and prevent cancellation merely because Redis is unavailable. Keep polling as the reference execution path; Redis notifications can be enabled after correctness is demonstrated. Removing Redis altogether is optional and requires a replacement traffic-limiting policy, not an automatic simplification.

Redis Pub/Sub is at-most-once delivery, which confirms that notification loss must remain harmless. [Redis Pub/Sub](https://redis.io/docs/latest/develop/use-cases/pub-sub/)

**Acceptance:** readiness/degraded-status checks accurately distinguish submission rejection from task execution and read/control availability.

### L2 — Triggers and overlapping state representations need a narrow ownership rule

**Severity:** Low. **Evidence:** lines 89–97, 282–286.

Keeping `tasks`, `steps`, `attempts`, and `external_calls` is justified because they represent different lifecycles. Avoid duplicating the same transition logic in triggers, services, and adapters. Give triggers only invariant enforcement, services workflow decisions, and adapters external protocol translation. Define which step states participate in each partial unique constraint; a time-dependent lease expiry must not be treated as an automatically changing index predicate. An expired active attempt must be explicitly closed transactionally. Prefer a few specific operations over a generic state-machine framework.

**Acceptance:** each invariant has an identified enforcement layer and an independent negative test; no second workflow engine is hidden inside database triggers.

### L3 — Observability is well chosen but operational ownership and alert criteria are missing

**Severity:** Low. **Evidence:** lines 273, 520–523, 580–591.

Add alerts and runbook actions for oldest eligible work, recovery lag, failed renewals, database pool pressure, unresolved exposure age, disk/backup failures, and unavailable workflow handlers. Define trace links across process restarts and sampling/redaction tests, including exception bodies and SDK instrumentation. Identify who can reconcile costs and how evidence is attached. Protect administrative credentials and keep CI live-provider secrets unavailable to untrusted pull-request code. Pin deployment artifacts and CI action revisions; define migration failure behavior.

**Acceptance:** inject a small set of failures, observe the corresponding alert, and follow the documented operator action. Do not require an elaborate observability platform beyond what this deployment can operate.

## 4. Guarantee assessment

Every guarantee below is prospective: none has implementation evidence in this review.

| Claim | Assessment | Required condition or correction |
|---|---|---|
| `202` only after task and initial work commit | Achievable | Appropriate durable PostgreSQL configuration; lost acknowledgements handled by command identity. |
| No duplicate task for a repeated command | Achievable within a defined retention/recovery domain | H1; retained record; stable request normalization; no erased history after restore. |
| One accepted local completion | Achievable | Conditional ownership/state checks, atomic follow-up creation, explicit recovery states, all mutation paths honoring the protocol. |
| One physical execution | Not guaranteed, correctly disclaimed | Leases do not prevent paused processes or remote servers from continuing. |
| One demo effect per operation key | Achievable conditionally | Durable remote transaction, same key/body, supported retention, no incompatible restore. Not a guarantee across new task IDs. |
| Recovery after worker crash | Achievable conditionally | Eligible work plus live reaper/workers/database; ambiguous calls may suspend instead of complete. |
| Revocation prevents later dispatch | Achievable for later authorization commits | H3; no absolute promise about physical sends already authorized. |
| No automatic repeat after ambiguous LLM dispatch | Achievable in normal recovery | All retry layers disabled/controlled; unknown is retained; restore gate; physical-call counting tests. |
| Local budget admission | Achievable | Atomic task/principal/global accounting, unique settlement, explicit rollover and token reservation semantics. |
| Exact provider invoice or actual-token ceiling | Not established | Estimates, pricing, unknown usage, and remote behavior remain external assumptions. |
| Ordered/replayable SSE | Achievable during retention | Atomic events, snapshot watermark, gap handling and authenticated replay; consumers may see duplicates. |
| Tenant isolation | Achievable against application callers | Complete authorization/query discipline and tests; not protection against compromised application DB credentials. |
| Prompt-injection immunity / factual reports | Not guaranteed | Restrict authority; measure semantic quality and residual attack success separately. |
| No accepted work lost after host loss | Not supported by a 24-hour RPO | H4; explicitly separate disaster recovery from ordinary process recovery. |
| p95 creation <300 ms / SSE <2 s | Targets only | Defined workload/hardware, proxy behavior, polling interval, measurement origin and degraded-mode results. |
| RTO 2 hours | Target only | Restore, verification, reconciliation, and safe reopening must fit the measured exercise. |

## 5. Recommended organization and milestone gates

Keep seven implementation milestones. Add a short design-contract checkpoint before M1/M2 rather than another framework or large documentation project. This review does not rewrite or authorize the plan.

**Before implementation of affected components:** resolve H1–H4 and document the coupled state matrix, transaction/lock matrix, operation identity and replay horizon, accounting formula, permission ordering, and restore boundary. A compact set of tables is enough.

| Milestone | Recommended adjustment | Concrete acceptance addition |
|---|---|---|
| M1 — foundation and identity | Preserve scope; pin compatible runtime/library versions and define role-specific credentials, health/degraded semantics, basic container limits. | Empty-database startup, revocation/isolation, redaction, and migrations with the intended app role. No paid calls. |
| M2 — tasks and commands | Include H1, complete lifecycle constraints, atomic quota admission, and versioned idempotency normalization. | Saturated replay works; concurrent distinct keys cannot exceed quota; queued cancellation leaves no executable child. |
| M3 — durable execution | Own external-call intent states, simulated cost/token admission hooks, ambiguity classification, lease/reaper protocol, and basic metrics. | Fault injection at A/B/C boundaries, paused stale sender, double recovery, lost commit acknowledgement, no duplicate completion or unaccounted dispatch. |
| M4 — agent workflow and tools | Add corpus provisioning/versioning, persisted provider-item replay, authenticated demo service, bounded context, and resolution rules. | Full simulated workflow survives every turn boundary; remote effect count is one per stable key; unauthorized access is denied; semantic attack results reported separately. |
| M5 — accounting and OpenAI | Finish full accounting/reconciliation, shared provider limits, real adapter contracts, explicit transport retries/timeouts. | Concurrent and late settlement, rollover, request-count tests, incomplete/refusal/usage fixtures, separately reported synthetic live smoke test. |
| M6 — SSE and operations | Add snapshot watermark/reset semantics, stream revocation, bounded degraded polling, alerts and reconciliation CLI usability. | Replay/gap/revocation races, slow clients, Redis loss, and correlated operational evidence. |
| M7 — release and recovery | Restore and retention exercises, supported-handler migration/rollback, specified load profile and CI release gates. | Restore quarantine includes tasks with no call rows; old workers cannot resume dispatch; real RPO/RTO and latency results published with limitations. |

The original sequencing of a complete simulated workflow before live OpenAI is sound. Moving *contracts* earlier does not require paying for calls earlier or implementing all of M5 during M3. Keep the explicit milestone authorization gate.

## 6. Required failure scenarios and evidence

The plan already has a strong testing strategy. These additions make its most important promises falsifiable.

| Scenario | Required oracle / allowed outcome |
|---|---|
| Fifth task accepted, response lost, same-key retry | Original `202` and task ID; one quota slot and one task. |
| Two distinct commands compete for final open-task slot | One admission; no transient or durable quota overrun. |
| Crash before B commits | No external request; bounded safe recovery and no leaked provision. |
| Crash after B commits but before any bytes | Uncertain state permitted; no automatic non-idempotent generation repeat. |
| B commit acknowledgement lost | Re-read stable call identity; ambiguity must not be interpreted as permission to resend. |
| Paused old worker resumes after lease loss/revocation | No accepted stale completion; physical send behavior matches H3 contract. |
| Reaper races with heartbeat and result commit | A single valid ownership outcome; expired lease cannot be revived. |
| Demo effect succeeds, connection drops before response | Query/replay same key and exact body; independently count one durable remote effect. |
| C commits but acknowledgement is lost | Re-read completion; no second next-step creation, event, or charge. |
| Cancellation/deadline races with successful result | One terminal task outcome; remote evidence and costs remain explainable. |
| Double resolve / late response after new generation | Separate calls/provisions; only selected valid continuation advances task. |
| Slow-drip HTTP / pool exhaustion / blocked event loop | Bounded local deadline and visible loss of lease; no false claim of remote cancellation. |
| Rollover / settlement retry / unexpected usage | Correct exposure and auditable overrun; no double release or disappearance of uncertain amounts. |
| SSE snapshot race / purge during replay / revoked stream | Contiguous replay or explicit reset; bounded revocation; no cross-owner payloads. |
| Redis unavailable under 100 SSE connections | Bounded database load; execution/control remains functional; submission behavior documented. |
| Restore predating dispatch or task creation | No blind dispatch; unrecoverable acceptance interval disclosed; external state independently inspected. |
| Payload purge before uncertainty resolved | Minimal identity/evidence survives; impossible resend disabled. |
| Deployment lacks an old handler | Task is visibly blocked/suspended; no silent execution under a new definition. |
| Prompt injection yields valid schema but false claims | No unauthorized tool action; semantic quality failure recorded independently of execution success. |

Use independent processes/connections and explicit synchronization barriers. A barrier only proves simultaneous arrival; where lock behavior matters, record actual contention. Inject failures at named transaction boundaries and use a separately persisted provider invocation/effect log. Assert both database invariants and external call counts.

The load test must define machine resources, connection pools, input sizes, number of principals, submission arrival rate, simulated LLM/tool latency, number of turns, warmup, and run duration. With eight workers and long sequential calls, 1,000 simultaneous submissions may legitimately reach the 15-minute deadline; a load count alone does not establish a throughput objective. Report queueing, admission latency, completion/timeout/suspension counts, SSE commit-to-observation latency, and normal versus Redis-degraded results.

For CI, keep unit/integration/concurrency checks on PRs and a bounded end-to-end fault subset early. Run the broader crash/restore suite before release. Pin and sanitize provider fixtures, including refusal, incomplete output, multiple/invalid tool items, missing usage, delayed responses, and malformed bodies. The live test validates the selected model/endpoint/account configuration only; it does not establish deterministic semantics or universal reliability.

## 7. Missing capabilities and complexity to avoid

Required for the stated MVP but not sufficiently concrete: corpus provisioning; cancellation/timeout finalization; version-compatible worker claiming; late-evidence reconciliation; retained operation tombstones; shared provider admission; deployment-wide exposure control; snapshot-compatible SSE recovery; stream revocation; and a restore quarantine that works when call records are absent. These are completion of the stated scope, not advanced product additions.

Avoid expanding the MVP into arbitrary Python execution, workflow DSLs, DAG joins, automatic provider fallback, general approval systems, vector infrastructure, or an event-sourced accounting framework. A few explicit reservation/settlement records and reconciliation commands are sufficient. Keep OpenTelemetry deployment modest; logs and actionable metrics must work without a complicated collector stack. Redis notification optimization can follow a proven PostgreSQL polling path.

The domain model is not inherently overengineered: attempts, external calls, events, and cost records are justified by distinct failure boundaries. The unjustified complexity would be building generic abstractions before proving those specific boundaries.

## 8. Decision

**APPROVE WITH CHANGES.** Preserve the architecture and scope. Resolve H1–H4 before implementation of their affected components, incorporate the Medium corrections into explicit milestone gates, and qualify the guarantees as described above. The design is credible for a bounded, moderate-volume MVP, but its current state/permission/restore contracts are not yet precise enough to approve as an implementation specification without changes.

No current implementation safety, performance, provider compatibility, test success, or production readiness is claimed. No development was started and no plan changes were made.
