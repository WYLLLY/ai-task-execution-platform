# Encrypted backup, seven-day retention and off-host delivery

Use a dedicated operator account and a dedicated encrypted destination. Production
backup and restore are manual deployment concerns: no destination, credentials or
production schedule is provisioned by a test run. The restore drill uses actual
`pg_dump --format=custom`, authenticated age encryption and `pg_restore` into
isolated databases. It does not demonstrate off-host delivery.

1. Install a checksum-verified age binary. Keep the public recipient on the backup
   host; keep the private decryption identity separately, outside the application,
   database and backup host. Back up that identity through an independent process.
2. Use `scripts.backup_artifact.encrypted_backup` with an explicit operator-owned
   `pg_dump` argument list, the public recipient and an exclusive `.age` destination.
   The helper streams plaintext directly into age; it never writes a plaintext dump
   file. Both children must succeed within the absolute deadline. Its manifest
   records UTC start/completion, encrypted byte size and SHA-256. Do not publish
   database connection strings or the decryption identity in manifests or logs.
3. Schedule this operation daily on the deployment host (UTC), with failure alerts.
   Copy **only the ciphertext and manifest** using a preconfigured authenticated
   off-host channel. Require strict host-key verification and a bounded upload.
   Record a successful-copy receipt only after the destination verifies the size
   and SHA-256. Configure the destination's lifecycle to retain seven daily points;
   do not expire the last verified recoverable point merely because a copy failed.
4. Check the newest verified-copy completion age every five minutes. More than
   24 hours, an upload/encryption failure or unavailable status is an alert, not a
   successful backup. Probe actual filesystem utilization; above 80% inspect capacity
   and close new admission when necessary. Do not infer disk state from an empty
   metric. Run the delivery alert actions and record the operator response.
5. During a drill, first authenticate/decrypt the entire age archive before opening
   an SQL restore transaction. A matching SHA-256 alone does not authenticate it.
   Corruption, a wrong identity, an age older than seven days, future timestamps or
   inconsistent byte size stops restore. Follow the disaster-recovery runbook for
   external isolation, credential rotation, quarantine, missing history and reopening.

Run the repeatable isolated exercise with `make test-restore`; configure `AGE` to
an installed trusted executable. Its private identities, encrypted archives and
client receipts are ignored by Git. Its public JSON reports the actual potential
loss interval and the duration from the start of loading/decrypting the archive
through verification, reconciliation and safe new-admission reopening. Prior
incident detection and credential isolation are reported separately and are not
included in that measured interval. A daily backup cannot promise zero data loss.

Evidence in `reviews/evidence/m7-release-gate-test-restore.txt` includes two
restored snapshots, actual old credential rejection, authenticated tamper rejection
and unchanged independent physical-request/effect counts. Off-host transfer and
a deployed production schedule remain **SKIPPED: no destination/credentials or
production deployment authorization**. These procedures are not evidence that a
production backup exists. Provider retention is independent of this seven-day copy
policy; erased live content can remain in older encrypted copies until expiry.

Primary contracts: [PostgreSQL pg_dump](https://www.postgresql.org/docs/17/app-pgdump.html),
[pg_restore](https://www.postgresql.org/docs/17/app-pgrestore.html),
[age](https://github.com/FiloSottile/age).

The reference test's actual disk probe crossed80%; the operator exercise inspected
remaining space and actual database growth before continuing disposable synthetic
traffic. This is an alert exercise with a retained resource limitation, not a
passing disk-capacity SLA. Production admission must be closed if its capacity is
unsafe; test throughput does not override that operational decision.
