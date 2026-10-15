---
name: agent-security-review
description: Review AI Task Execution Platform tool calling, prompt injection, permissions, isolation, secrets, network access and resource limits against the approved plan, with reproducible attack scenarios and security tests.
---

# Agent Security Review

Audit the requested milestone or agent/tool boundary without modifying implementation code or starting another milestone. Complement the existing `adversarial-review` skill and report executed checks through the existing `verification` skill. Resolve their locations through root `AGENTS.md`; do not recreate them.

## Scope and threat model

1. Read root `AGENTS.md`, `docs/architecture-plan.md`, the requested milestone and its gates. Consult `reviews/architecture-plan-review-response.md` for accepted tradeoffs; do not reinstate superseded findings as requirements.
2. Record revision/files, entry points, credential roles, supported tools/workflows, effective configuration and tests. With no implementation, report a specification review and proposed tests, not runtime security certification.
3. Trace untrusted input/documents, provider items, tool results and reports through authorization, dispatch, storage, logs and clients.

The attacker controls their own API requests and may influence documents/model/tool outputs. Application code and administrators are trusted. Owner isolation applies against API callers, not a compromised host or application SQL role. RLS and arbitrary-code/plugin sandboxes are outside this MVP; do not add them as implicit requirements.

Use plan §§3.1–3.6, §§4.3–4.5, §5 and §§7.1–7.3. Coordinate overlapping dispatch/budget findings with `execution-reliability-review` rather than duplicating them.

## Tool authority and validation

- Agent tools are only `search_documents` and `read_excerpt`. `save_report` belongs to the deterministic orchestrator after report/citation validation; a model naming it cannot make it callable.
- No arbitrary shell, executable generated code, free SQL, model-chosen URL/header/secret. Trace dispatch to fixed handlers/destinations; schemas alone do not authorize actions.
- Validate names, argument types, unknown fields, nesting/size, item types and cardinality server-side. Requested `strict=true` and `parallel_tool_calls=false` do not replace enforcement.
- Refusal, incomplete/malformed output, invalid schema, unsupported items or multiple tool calls must yield an explicit outcome with zero tool execution. No partial dispatch, hidden repair generation or silent fallback. Account for provider usage independently of functional rejection.
- Bind arguments to authenticated owner and pinned versions. Check every nested resource ID, excerpt range and citation against current rights. A supplied owner/task/operation/call ID is not authority.
- Preserve required provider protocol items/call IDs across resumed turns. Opaque reasoning items remain protocol data, not decoded instructions or client/log content. Check citation owner/version/offset/excerpt, without treating a valid citation as proof of the conclusion.

## Prompt injection and permissions

- Task texts, retrieved excerpts, tool outputs and reports are untrusted data, never system instructions or commands. Attempt role spoofing, forged tool envelopes and instructions inside valid JSON/documents.
- Verify deterministic enforcement even when a model follows malicious text. A prompt asking it to ignore injections is not a security boundary. Record permitted but misleading output as semantic contamination, distinct from unauthorized action.
- B rereads current key/principal/workflow/document permissions under the plan's locks and records versions. Local reads also check current rights and consume authorized tool counters transactionally; immutable content does not imply irrevocable access.
- Order revocation/B in both directions. A revocation committed first blocks authorization; already-committed B may still produce a delayed first send. Do not misclassify that documented limit as a bypass or claim local fencing stops remote work.
- Enforce owner scopes across list/snapshot/step/event/tombstone/usage/cancel/resolve routes, corpus and demo lookups. Cross-owner tasks return `404`; nested IDs/cursors cannot disclose their existence/content.
- Owner resolution needs expected version, current rights and remaining limits; it cannot fabricate external success, perform administrative accounting reconciliation or bypass restore quarantine.
- For SSE, check authentication validity before each write, revalidation/closure within the plan's 10-second bound and behavior during DB failure. No secret in URLs; owner-scoped replay remains enforced after purge. State the limit for bytes already handed to transport.

## Isolation, network and secrets

- Inspect actual images/Compose/runtime: non-root, dropped capabilities, minimal mounts, CPU/RAM bounds, private DB/Redis and production TLS. Shared image does not imply shared secrets or privileged host access. Worker isolation is not an arbitrary-plugin sandbox.
- Separate API/worker/admin/migration/demo credentials and least privileges. API receives no OpenAI credential; runtime roles cannot inherit migration/admin access. Test with synthetic canaries, never print real secrets.
- Demo service uses fixed destination, worker-only credentials and validated trusted owner/operation binding. Redirects are disabled. Verify no model-controlled destination/header/proxy or implicit environment-network configuration with isolated capture servers.
- HTTPX transport/proxy layers must not retry or redirect invisibly. Inspect configuration and physical request counts; first-send authorization cannot justify a hidden repeat.
- Audit errors, logs, instrumentation, traces and audit fields for secrets/source bodies/opaque items. Preserve correlation IDs without raw sensitive content or unbounded metric labels.
- Sanitize fixtures, keep live secrets away from untrusted PR code and verify production excludes test fault-injection endpoints. Review retention separately from backups/provider policy: `store=false` is explicit, not a zero-retention guarantee.
- Restoration invalidates resurrected credentials, isolates old workers and enforces an external dispatch lock/new epoch before reopening. Owner `resolve` cannot bypass quarantine.

## Resource abuse

Read effective limits and compare to §§3.3, 3.5, 3.6 and 4.4. The following are plan defaults, not evidence of deployed settings.

- Bound input before expensive parsing: 20 texts/1 MiB per task, strict JSON without duplicate keys/nonfinite numbers, HTTP responses 1 MiB before decoding, tool results/reports 64 KiB and SSE events 16 KiB.
- Bound complete context to the stricter model limit and 12,000 estimated input tokens, output 2,000 and task cumulative estimate 40,000. Overflow is explicit `context_limit`, not arbitrary protocol truncation or hidden summarization.
- Count retries/regenerations toward 8 LLM authorizations and 8 ordinary tools/task; reconciliation has a separate bounded probe policy. Deadline includes queue/suspension and cannot be extended by resolve.
- Check atomic SQL budgets, storage/queue/provider limits across owner and deployment; Redis loss cannot remove them. Record actual usage overruns and block affected admissions; no exact invoice ceiling is promised.
- Exercise HTTP limits, 5/owner and 100/global SSE caps, 256 KiB stream buffers, bounded client writes/session lifetime, pool isolation and slow-drip HTTP. Reads/streams must not starve control/heartbeat.

## Reproducible attack scenarios and tests

Use disposable real PostgreSQL/Redis and production-like roles/configuration for claims depending on them, synthetic owners/documents/credentials, deterministic provider fixtures and controlled HTTP receivers. No public-system attacks, production secrets or paid model campaign by default.

Each case records attacker capabilities, input/fixture, ordered actions, enforcement point, expected response/state, observed tool/HTTP calls, disclosure oracle and cleanup. Run existing tests; create test files only when authorized, otherwise provide reproducible regression specifications. Do not scaffold features to satisfy a review.

| Scenario / plan | Attack setup | Required oracle |
|---|---|---|
| Tool escalation / F13, M4 | Inject instructions on each input surface to call `save_report`, shell/SQL or unknown tool; simulator returns a forged tool-call item. | Server rejects prohibited dispatch despite model compliance; zero unauthorized invocation/effect. |
| Protocol confusion / M2/M4/M5 | Duplicate JSON keys, nonfinite/wrong-type/nested arguments, extra fields, forged call IDs, multiple calls, refusal/incomplete/truncated output. | Bounded parsing, explicit failure, no partial tool execution or hidden repair; known usage retained. |
| Cross-owner access / F11/F13 | Owners A/B with versioned docs; A supplies B's IDs directly/nested in arguments, citations, routes, cursors and demo lookups. | No unauthorized bytes/effects or existence leak; authorization is not solely an outer HTTP check. |
| Revocation race / F06/F11 | Pause before B, commit source/key revocation, then release; reverse ordering; revoke after context persistence. | First ordering blocks authorization; second respects late-send limit, rejects stale progression and retains attributable evidence. |
| SSRF/egress / F13 | Model outputs loopback/private/metadata-style URLs and header/proxy instructions; controlled demo redirects to another receiver. | No model-selected request, followed redirect or forwarded credential. Use isolated receivers, never real metadata endpoints. |
| Forged report/effect / F12–F13 | Valid-schema report with wrong owner/version/span or unsupported conclusion; forge demo binding; resend same key/different body. | Invalid references/binding rejected, body conflict; valid yet false conclusion separately recorded as semantic failure. |
| Secrets/runtime / M1/M6/M7 | Role-specific canary secrets, transport/validation errors and tracing; forbidden role operations; inspect production profile/container settings. | No canary in unauthorized process/output/log/trace/URL; forbidden SQL denied; fault endpoints absent and runtime restrictions effective. |
| Resource exhaustion / F09/F16/F18–F19 | Oversized/nested bodies, repeated regeneration, slow-drip HTTP, slow SSE/reconnect storms, Redis loss under load. | Limits before unbounded work, durable cumulative caps, new creation `503`, bounded replay/control/heartbeat preserved. |
| Stream disclosure / F17–F18 | Other-task/future cursor, revoke during emission, stall authorization DB reads, purge mid-stream. | No cross-owner event or write beyond valid auth window; documented reset/close and bounded buffering. |
| Restore/retention / F20–F22 | Restore before revocation/B with remote journal retained; attempt old API/worker credentials and owner resolve against quarantine. | Old access cannot reopen dispatch; epoch/operator/quarantine gates hold; purged bytes cannot be reconstructed for resend, minimal evidence protected. |

For semantic evaluation use §7.2's separate corpus of 20 synthetic dossiers and 20 adversarial variants with expected facts/citations. Report schema/reference validity, contradictions and instruction contamination separately. Simulator tests prove deterministic enforcement only; an authorized real-model campaign remains separate and cannot establish universal injection immunity or truthfulness.

Map cases to relevant milestone gates and actual commands (`make test-tools`, `make test-provider-contracts`, etc.). Missing commands/infrastructure and later milestones are explicitly unexecuted/deferred, never passing. An audit does not authorize remediation or broader implementation.

## Report

Each finding includes Critical / High / Medium / Low severity, file/line or boundary, attacker prerequisites, exact reproduction, violated plan/permission contract, impact, cause, existing coverage, minimal recommendation and regression test. Separate exploitable defects, hypotheses, configuration gaps and documented residual risks.

Include revision/scope, covered trust boundaries, exact commands/configuration, passed/failed/errors/skipped counts, unrun checks/reasons, forbidden invocation/disclosure counts, semantic results and an evidence-supported verdict. Use existing `verification` reporting for executed checks. Return fixes to the implementation author; an empty finding list is not universal security assurance.
