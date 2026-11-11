# November 2026 maintenance report

A JSON number with an exponent outside the Decimal constructor's representable range raised an unhandled exception during command parsing. The parser now translates that exception to HTTP 422. Representable numbers retain the same bounds, canonicalization and idempotency fingerprints.

Two parser regressions initially failed with Decimal InvalidOperation, then passed after the exception was handled. The authenticated HTTP regression also checks that a valid request can reuse the rejected request's idempotency key. The README now reflects the merged backend milestones and links to the separate Taskroom pull request.

The pull request records the regression, simulation and independent review results for this change.
