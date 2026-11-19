# Local frontend V1 verification

Date: 29 October 2026. Scope: free local interface, no hosted deployment, provider
call or new backend execution runtime. Branch: feature/local-frontend.

**Functional/static checks PASS. Full UI test-first chronology is UNVERIFIED.**
Gateway/state-helper behavior has recorded Red → Green evidence. Visual UI/tab
interactions were checked after implementation; no pre-implementation browser
Red is retained for them. This is an explicit TDD-completion limitation under
AGENTS.md, not a claim that final green checks establish full test-first history.
The PR must remain draft pending acceptance of that limitation. No tests or
checks were weakened to obtain these results.

## Executed results

| Check | Result |
|---|---|
| `cd frontend && pnpm test` | 14 PASS / 0 FAIL / 0 ERRORS / 0 SKIPPED |
| `pnpm typecheck` | PASS, strict; skipLibCheck=false |
| `pnpm lint` | PASS |
| `pnpm exec prettier --check app lib tests '*.json' '*.mjs' '*.ts'` | PASS |
| `pnpm build` | PASS; root/static error page and two dynamic server routes |
| Complete unfiltered M1–M7 regression | 828 PASS / 0 FAIL / 0 ERRORS / 0 SKIPPED in 310.83s |
| Root Ruff / format / mypy | PASS; 208 Python files formatted, 159 typed source files |
| Independent adversarial + security/reliability review | APPROVE after four confirmed findings were corrected |
| Authenticated Local API reads | PASS: dedicated owner provisioned after explicit approval; browser Online, tasks/workflows HTTP200 |
| Positive create/cancel browser flow | SKIPPED: admission/dispatch remain closed; HTTP503 observed, no worker execution enabled |
| Paid OpenAI / hosted deployment | SKIPPED: outside the zero-cost scope; no call or deployment made |

Backend command, isolated real PostgreSQL/Redis stack (development stays separate):

```sh
API_PORT=8001 DOCKER_CONTEXT=colima-ai-task-platform docker-compose \
  -p atp-m1-test -f compose.yaml -f compose.demo.yaml -f compose.proxy.yaml \
  up -d --wait api demo proxy
API_PORT=8001 COMPOSE=docker-compose DOCKER_CONTEXT=colima-ai-task-platform \
  .venv/bin/python scripts/run_verification.py -q --tb=short
```

No Python application code/schema changed. The existing installed test image was
used; no claim that frontend tests prove database locking/privilege properties.
The full suite supplies separate backend evidence. Output is retained privately
in `.secrets/frontend-backend-regression.txt`; frontend final check logs are in
`.secrets/frontend-final-*`. Earlier interrupted run/temporary logs were lost and
are not used as newly verified evidence.

## TDD and review corrections

Initial gateway/money/state stubs were run before implementation: **0 PASS / 8
behavioral FAIL** (501 instead of expected status, incorrect terminal-state or
money values). Raw initial output was temporary and is no longer retained; these
counts are the observed session record, not a retrospective rerun. Minimal
implementation produced **8 PASS**.

Review then found lost pending identities on mode/reload and hidden estimated
charges. Two tests failed against stubs before the corresponding helper fixes;
subsequently 10 passed. The original temporary raw outputs are unavailable.
Terminal trailing whitespace is normalized in public copies; private originals
remain unchanged. Further durable evidence is retained in `evidence/frontend/`:

- `rejection-red.txt`: definitive 409 incorrectly retained a command, locking the
  form permanently. **10 PASS / 1 FAIL** before the response-lifecycle fix.
- `free-workflow-red.txt`: analysis:2 incorrectly reached upstream (202 instead
  of 403). **11 PASS / 1 FAIL** before server enforcement of analysis:1 only.
- `green.txt`: final **12 PASS**, including zero physical upstream requests for
  the forbidden paid version and exactly one for the allowed simulated version.

The independent reviewer re-read current server/UI code and personally ran all
12 cases after the final free-workflow correction: APPROVE, no remaining confirmed
blocking security/reliability finding in the scoped local app. That review does
not erase the UI chronology or unexecuted authenticated-browser limits above.

## Matrix and actual browser checks

- Unit: integer micro-USD/Unknown, terminal vs suspended cancellation eligibility,
  command restoration, definitive vs ambiguous status, estimated accounting.
- Gateway/security: missing credentials, admin/traversal/events/method denial,
  same-origin writes, fixed destination, no forwarded private headers, size bounds,
  malformed upstream data, one physical call/no transparent retry, paid-version denial.
- Browser: sample success/suspended/failed selection, no fabricated report for
  suspended task, four example steps, rendered JSON report/citations, keyboard
  Space activation, explicit sample labels, unconfigured Local API disabled.
- Responsive: document width equals viewport at 320, 390, 768 and 1440px. Desktop
  and mobile visually inspected. Browser error log: zero observed entries.
- Integration/end-to-end: gateway transport tests are synthetic; no claim they
  are authenticated real backend flows. Existing real backend regression is
  separate. Worker crash/locking/migration concerns are unchanged and covered
  there, not reimplemented in JavaScript.

## Limits and access

Sample mode works without infrastructure. Local API requires a server-side owner
key, uses polling (not SSE), and exposes only the first 50 tasks/steps. It cannot
open admission/dispatch or run workers; tasks may stay queued. Session storage
contains pending synthetic input/body/key, never the owner API secret; closing
that tab or clearing storage loses its identity. No public/multi-user security or
free cloud hosting qualification is claimed. The backend's existing production
and image-inventory limitations still apply.

The approval rejection created no owner/key and was not bypassed. No credential
is present in tracked frontend files. User-owned AGENTS.md and both pre-existing
post-merge review reports remain excluded. No merge or auto-merge is authorized.


## Follow-up after explicit user approval

The user subsequently authorized a dedicated local owner/key and a draft PR.
Provisioning completed, with the owner-only key in ignored frontend/.env.local,
mode0600; its value was never printed. A scan of .next/static found no copy of the
key. Browser Local API now reports Online with an empty owner task list and the
analysis:1 catalogue. No sample data is mixed into live results.

Actual HTTP exposed a Next.js request-URL/Host normalization mismatch: legitimate
127.0.0.1 Origin was denied403 while internal request.url used localhost. The
regression failed403-vs202 before the gateway fix (12PASS/1FAIL). The server now
validates the browser Host as loopback and compares its exact origin, while still
rejecting nonlocal internal URLs and hostile Host/Origin combinations. Independent
review found malformed high-port Host values could throw; its regression recorded
13PASS/1FAIL (ERR_INVALID_URL), then explicit403 with zero upstream calls after a
bounded parse/catch fix. Current final frontend result is **14PASS/0FAIL/0SKIPPED**.
Independent re-review personally executed all14 and returned APPROVE, no remaining
confirmed findings. Public red copies normalize terminal trailing whitespace.

Actual gateway checks: paid analysis:2 creation returns403 before upstream; allowed
analysis:1 reaches the backend and returns503 Submission unavailable because
admission remains closed. No task or provider call was launched. This distinguishes
connected-read verification from an unexecuted positive create/cancel workflow.

Fresh full M1–M7 regression after the gateway correction: **828PASS,0FAIL,0ERRORS,
0SKIPPED in310.83s**. F19 recorded100 streams/120reads, readp95 .0349s, zero stream
errors, one physical request/effect. Final TypeScript/lint/format/build pass.
Raw connected-suite output: .secrets/frontend-origin-backend-regression.txt.
The earlier319.58s result and12-case review describe the preceding revision.

Earlier approval rejections were not bypassed; the subsequent explicit human
answers supplied the missing authorization. The draft PR and owner setup are now
authorized. The UI test-first chronology limitation remains unchanged; no merge
or public/paid deployment is authorized.
