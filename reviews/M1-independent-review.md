# M1 independent review

Date: 2026-10-15. Scope: `feature/m1-foundation`, M1 only. Reviewer: separate Codex subagent `m1_independent_review`; the implementation author did not act as the independent reviewer. Skills: adversarial-review, agent-security-review and execution-reliability-review. This report transcribes the reviewer's findings and evidence.

**Verdict: APPROVE for M1, subject to final integration/Compose and remote CI passing. No unresolved findings.**

Reviewed identity, owner isolation, transaction/lock boundaries, revocation/audit, SQL roles and migrations, epoch, pools, errors/logs, container/secret separation, compatibility manifest, README, smoke script and CI. No later-milestone execution feature was found.

## Confirmed finding and correction

**Low — existing local environment overwritten by secret generation.** `scripts/dev_secrets.py` originally checked only `.secrets/` before writing `.env`. With a pre-existing `.env` and no secret directory, `make dev-secrets` replaced operator configuration. The reviewer reproduced this in a temporary directory: command exited successfully, existing environment was not preserved and secrets were created.

Correction: refuse an existing `.env` or `.secrets/` before writing. Two regression tests verify unchanged environment contents, no secret directory creation on refusal, independent generated credentials and no overwrite on a repeated invocation. The reviewer reread the fix and reran unit/static verification.

## Independent evidence

| Command or scenario | Result |
|---|---|
| `.venv/bin/python -m pytest tests/unit -p no:cacheprovider` | PASS: 10 passed, 0 failed, 0 errors, 0 skipped |
| `.venv/bin/ruff check .` | PASS |
| `.venv/bin/ruff format --check .` | PASS: 29 files |
| `.venv/bin/mypy` | PASS: 22 sources |
| Four isolated CLI failure scenarios: admin/worker DB unavailable, invalid UUID, invalid Redis configuration | PASS: exit 1, synthetic canary absent from stdout/stderr |

Inspected protections: 256-bit random API secrets stored as SHA-256 digests; current PostgreSQL authentication; owner predicates; principal-before-key locks; transactional permission versions and audit; no runtime DDL/administrative inheritance; allowlisted JSON logs. PostgreSQL and Redis use only the internal network. API additionally uses an ingress network with host loopback publication. No provider credential is supplied.

The reviewer did not modify the repository, run Git operations or disturb the author's database tests. The author's 48 passing real integration tests and passing Compose smoke were reported to the reviewer, not independently re-executed by the reviewer. Remote CI was pending at review time; its final status belongs in the verification report and PR checks.

F01–F22, real-provider behavior, SSE, restoration and load testing are deferred to their milestones. No paid calls were made. This is M1 approval, not a production-readiness or universal security claim.
