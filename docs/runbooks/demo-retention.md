# Evidence-based retention in the independent report service

Platform redaction and demo redaction are separate administrative operations.
The demo does not know when a platform task terminated. Never substitute first
receipt time, a missing lookup or a restored database's absence for that clock.
Use the existing verified task termination timestamp and retain its evidence.

## Apply the compatible forward addition

Fresh demo volumes execute `infra/demo-retention.sql` after the existing schema.
An existing volume does not rerun initialization files. Close/drain report sends,
back up the independent database, and explicitly apply that SQL under its schema
owner, stopping on any error. For the disposable Compose project the command is:

```sh
docker compose -p atp-m1-test -f compose.yaml -f compose.demo.yaml \
  exec -T demo-db psql -U postgres -d demo -v ON_ERROR_STOP=1 < infra/demo-retention.sql
```

The addition is transactional and repeatable. It preserves all effect rows and
adds a private immutable decision journal, a guarded erasure function and a
NOLOGIN privilege group. It never grants UPDATE, DELETE or administrative access
to `atp_demo`, the worker or the API. No destructive downgrade is automatic.

Provision an identified operator's separate login through a trusted SQL console:

```sql
CREATE ROLE atp_demo_gc_operator LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION;
GRANT CONNECT ON DATABASE demo TO atp_demo_gc_operator;
GRANT atp_demo_retention TO atp_demo_gc_operator;
```

Use psql's interactive `\password atp_demo_gc_operator` to set its credential;
do not put a password in command history or a public report. The group provides
schema usage, execution of the fixed erasure function and read access to its
minimal audit. It grants no direct effect rewrite/deletion or audit mutation.
Keep this credential outside the API, worker, demo HTTP service and PR CI.
Production login provisioning and scheduling are operator deployment actions,
not performed by the local verification campaign.

## Prepare terminal evidence

Under the platform administrator credential, extract only minimal metadata from
terminal tasks, their retained `steps.operation_key` and matching `demo-v1` call
hashes. For example, with the SQL session timezone explicitly UTC:

```sql
SET TIME ZONE 'UTC';
SELECT DISTINCT t.id AS task, t.principal_id AS owner, s.operation_key AS operation,
       t.terminal_at, x.payload_hash
FROM tasks t JOIN steps s ON s.task_id=t.id
JOIN external_calls x ON x.step_id=s.id
WHERE t.terminal_at <= clock_timestamp()-interval '30 days'
  AND s.path='analysis/save-report' AND s.operation_key IS NOT NULL
  AND x.provider='demo-v1'
ORDER BY t.id, s.operation_key, x.payload_hash;
```

Save the extraction privately with restrictive permissions, retain its provenance
and SHA-256, and verify that the operation has one exact payload hash. A hash is
an integrity reference, not independent authentication or proof of termination.
The identified administrator attests the source. If restored/lost history cannot
establish termination, investigate independent evidence; do not invent a date.

Prepare one JSON file per operation, at most 16 KiB, with exactly these fields:

```json
{
  "evidence_id": "terminal:stable-task-identifier",
  "owner": "00000000-0000-4000-8000-000000000001",
  "operation": "00000000-0000-4000-8000-000000000002",
  "payload_sha256": "<64 lowercase hexadecimal characters>",
  "terminal_at": "<verified UTC timestamp ending Z>",
  "proof_sha256": "<SHA-256 of the retained source evidence>",
  "reason": "Verified terminal task and exact operation hash"
}
```

Placeholders are illustrative and must be replaced. Include no document body,
report, raw key or opaque provider item. Keep evidence IDs stable across retries.

## Execute, inspect and repeat safely

Use the standard trusted administrator configuration plus a separate protected
`DEMO_RETENTION_DATABASE_URL_FILE` (or explicit secret value, never both). The
ordinary Compose admin service deliberately does not mount that credential;
run in the operator environment or supply a dedicated private secret mount.

```sh
atp admin demo-retention --file /private/operator/terminal-evidence.json
```

The command makes no HTTP request. It validates strict identity/hash/UTC fields,
locks the actual demo operation and checks fresh database UTC time after waiting.
At least 30 elapsed UTC days must have passed since proven termination. One commit
records the minimal decision and replaces the sensitive payload with empty bytes.
Owner, operation, effect ID, exact hash, original response and first receipt time
remain unchanged. Exact HTTP replay still returns the original receipt; a changed
body still conflicts. No accounting provision is released by this action.

The same evidence/body returns the retained decision after an acknowledgement is
lost. A changed body under the same evidence ID conflicts. After an uncertain CLI
result, reread/retry this same stable evidence; never reconstruct or resend the
business report. Schedule bounded per-operation maintenance alongside platform
retention and alert on failures. Inspect the private decision journal under the
operator role. No source content belongs in operational logs.

Minimal deduplication/proof metadata is conservatively retained for at least the
90-day remote guarantee; automatic day-90 deletion is not implemented or required
for correctness. The platform still enforces its earlier task deadline and
24-hour authorization horizon. Controlled F20 tests expire a remote record and
prove zero old HTTP sends with nonzero local exposure retained. This does not
claim that a missing remote record proves non-execution.
