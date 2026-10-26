# One-time acceptance of incomplete M1/M2 TDD traceability

Decision date: 2026-10-18. Authority: the project owner explicitly authorized this exception in the project conversation after reviewing the independent report and its strict-process rejection.

## Exact scope

The owner accepts incomplete historical test-first traceability only for the already implemented M1/M2 portions present at commit `7e090d441e48ec1e53af8c7d6b76bc709bb4d429` (application source last changed at `51c7e3c4a31b6d7470941c6cef2dc9560b495e59`). This covers the unavailable historical M1 chronology and the M2 state-arc, transport, limiter and transaction-retry audit tests added after their mechanisms existed, as identified in the [test matrix](M2-test-matrix.md).

The owner's explicit instruction was to accept that incomplete traceability for existing M1/M2, retain the limitation in the report, keep strict TDD for every new modification, obtain independent reassessment under this accepted criterion, and mark the PR ready for review only if the remaining requirements are satisfied.

## What the decision changes

The missing historical chronology remains **unverified**. The original strict-process verdict was **REJECT**; it remains part of the review history. This decision changes acceptance of that known process limitation for the specified baseline only. It does not turn unverified chronology into TDD PASS, erase failed runs or invent retrospective Red evidence.

The accepted process risk no longer blocks M1/M2 acceptance by itself. Runtime correctness, security, scope, test coverage, migrations, independent review and CI remain mandatory; no application defect or failed technical check is waived. The existing evidence is recorded in [verification](M2-verification.md) and the [independent review](M2-review.md). The reviewer must reach a new verdict independently before readiness is changed.

## Rules for all subsequent work

Strict Red → Green → Refactor applies to every new behavior and correction, including changes to M1/M2 and any future M3 work:

1. Write the behavior/regression test before application code.
2. Run it and record a meaningful failure caused by the missing behavior. Import, setup or infrastructure errors do not count.
3. Implement the smallest change that passes the test; refactor while tests remain green.
4. Preserve all coverage and assertions; execute the affected checks and the complete regression suite required by AGENTS.md and the verification Skill.

This exception does not apply to future work, and cannot be reused as a general exemption. If a required meaningful Red cannot be demonstrated, stop before implementation and report the blocker. No global Skill or architectural requirement is weakened.

## Authorization boundaries

Only documentation and PR review-readiness are changed by this decision. No application code, tests, migrations or infrastructure changes are authorized by this exception. M3 has not been started or authorized for implementation by this decision. A ready-for-review PR is not a merge approval: no merge, auto-merge, deployment or self-approval on GitHub is authorized.
