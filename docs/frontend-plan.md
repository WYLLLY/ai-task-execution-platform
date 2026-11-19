# Local frontend V1 — approved scope extension

User authorization: 28 October 2026, implement the frontend and its plan, strictly
free; repository/local delivery if hosting cannot be free. This is a frontend
extension to completed M1–M7, not a change to their accounting/execution design.
No paid hosting, subscription, deployment, provider call or live OpenAI credential.

## Delivery and UX

Keep Next.js/React/TypeScript in frontend/ in this repository. Reuse the portfolio's
editorial dark design: serif display typography, sage accents, thin rules,
numbered sections and legible tables. A responsive dashboard contains task list,
status, task detail, step history, structured result and per-task accounting panels.

Two explicit modes: Sample preview (deterministic illustrative data, never labelled
backend execution) and Local API (actual owner-scoped reads, creation/cancellation
when authorized by the existing backend). Sample scenarios include success,
suspension and failure. No public anonymous access or login system in V1.

The local server binds 127.0.0.1:3000. A fixed-destination server gateway reads an
operator API key from ATP_API_KEY; it never exposes it to browser code/storage.
Only exact existing API paths/methods are allowed. Requests have bounded body and
absolute fetch timeout, no redirects or transparent mutation retry. Browser writes
must have the exact same Origin; use stable idempotency identities after ambiguous
results and do not silently regenerate. No admin routes or arbitrary destinations.

Fresh API snapshots are polled every 5 seconds only while the page is visible;
no claim of SSE integration in V1. Cancellation uses the observed task version.
The UI preserves backend errors, disabled admission, unknown outcomes and queued
work. It does not enable dispatch or provision a simulator. Existing analysis
workers require the explicitly isolated test profile; starting the UI never starts
those workers. Local API mode is not a promise of autonomous report completion.

## Stages and acceptance

1. Write meaningful failing tests for state/usage presentation and gateway security.
2. Implement minimal helpers/gateway, rerun to green, then build the interface.
3. Validate missing credentials, access denial, failed reads, long text, terminal
   vs suspended state, versioned cancellation, ambiguous writes and sample labels.
4. Run frontend tests, TypeScript, lint and production build; review desktop/mobile
   in a real browser. Delegate independent security/reliability review and fix
   confirmed findings test first. Run repository regression on real services where
   available; report exact outcomes/skips. Preserve AGENTS and existing reports.
5. Commit/push a focused PR; no merge without separate user approval. No hosted
   deployment is part of this free/local delivery. Recruiters need a local clone
   with repository access; the public portfolio remains a case study. Public source
   or a future public hosted demo requires a distinct decision and access model.

No database migration, backend weakening, OpenAI use, file upload, arbitrary corpus
import, administrative settlement, regeneration or public multi-user session is
introduced. V1 is deliberately not a production internet-facing application.

Free-scope gate: gateway creation is restricted to the simulated analysis:1
workflow, and the selector filters out paid versions. No new provider runtime is
introduced. Optional owner credential provisioning requires explicit approval.
