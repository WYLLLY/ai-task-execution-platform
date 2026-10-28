# Disaster recovery and lost history

This procedure is for an identified administrator. A restored database is not
proof that an external request never happened. Do not send or regenerate work
merely because its task, command key or call is absent from the backup.

1. Close ingress for new commands and provider/demo egress **outside PostgreSQL**.
   Set `DISPATCH_ENABLED=false` in deployment configuration. Stop old workers,
   revoke their database/provider/demo credentials, terminate old DB and network
   sessions where possible, and document evidence of isolation. Already submitted
   provider work can still finish. A new epoch does not cancel that remote work.
2. Check the authenticated encrypted backup and its manifest. Refuse the normal
   procedure for a point older than seven days. Measure the actual backup age;
   a daily schedule is not evidence that a copy succeeded. Restore only into an
   isolated deployment with synthetic destinations for drills and no live egress.
3. Generate a new random execution epoch; provision it in configuration. Run the
   administrative recovery command with an evidence file containing the restored
   point, incident time, isolation and credential-revocation attestations and a
   SHA-256 reference to independently retained operator evidence. Never put raw
   credentials, document bodies or opaque provider items into that evidence.
4. Verify every restored nonterminal task is quarantined, every old active
   attempt is closed and its lease invalid, and authorized calls are unknown.
   Expired tasks must be timed out while retaining restoration uncertainty.
   Verify old API keys fail, then issue current owner keys after identity review.
5. Collect independent provider invoices/records and owner-scoped demo lookups.
   An absent lookup is not proof of non-execution. Bound missing exposure using
   deployment and owner disaster reserves before reopening admission. An
   unbounded scope stays closed. Reserve revisions and their evidence are
   explicit, atomic and idempotent; do not invent call/B rows lost by the restore.
   Account for existing local provisions separately to avoid double counting.
6. Publish the restored point, incident and possibly lost acceptance interval.
   `/v1/system` must disclose that window. Clients must not refresh the epoch and
   automatically replay an old submission. A new submission after lost history
   is a new human decision that can duplicate an external effect.
7. Check budgets, shared provider windows, handlers, schema, database privileges,
   current credentials and external isolation before reopening **new** admission.
   Keep existing quarantined tasks closed. Release a task only on sufficient
   evidence, an explicitly associated current authorization, current document
   rights, intact payload/checkpoint and a remaining deadline. Otherwise abandon
   it with its uncertain exposure retained. Never renew the 24-hour remote replay
   horizon or the original task deadline during recovery.

Record backup point, incident, restore start/end, evidence/reconciliation checks
and actual safe reopening. RPO is the measured potentially lost interval; RTO
includes safe reopening, not just `pg_restore`. IDs accepted after the restored
point may be irrecoverable. No zero-data-loss claim is made.

## Retention

Run bounded administrator batches. Sensitive task content expires 30 days after
termination; retained command bodies expire 30 days after acceptance and return
410 thereafter. Owner/ID/hash/timestamp/state tombstones remain for at least
90 days. Minimal financial records remain for at least 90 days after resolution
and indefinitely while money or token exposure/reconciliation remains open.
The current conservative policy may retain minimal non-content metadata longer;
content erasure never cascades into financial evidence. A purged exact request
is marked unavailable for replay and must never be reconstructed for an old send.

Explicit corpus deletion waits for all active task references to end. Preserve
the immutable document version identity so that deletion cannot make an old
version number refer to new text. Encrypted backup copies may retain deleted
live data until their seven-day expiry. Provider retention is a separate policy.
