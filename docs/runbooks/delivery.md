# Manual delivery and compatible rollback

Use an immutable application image digest built from a reviewed commit and the
locked dependencies. Retain its commit, platform, image ID/digest, schema revision,
workflow/handler versions and verification/scan reports in the release manifest.
Production endpoints require operator-provisioned TLS. Database and Redis stay
private. API credentials must not include the OpenAI key or administrator role.

1. Close new admissions and B authorizations administratively. Signal workers to
   stop accepting work and drain. Observe active local calls and attempts; closing
   dispatch is not cancellation of an already authorized remote operation.
2. Back up and verify a recoverable point. Record the release manifest and the
   compatible rollback image. Apply reviewed forward migrations with the dedicated
   migration role, with dispatch closed. A migration failure stops deployment.
3. Run schema, privilege, epoch and pinned-handler preflight. An absent compatible
   handler suspends affected tasks; never substitute a newer handler. Review queue,
   unknown exposure, provider windows, pool pressure and operational alerts.
4. Reopen only through a separate explicit operator action after successful
   preflight. Retain the external `DISPATCH_ENABLED` deployment fence. There is
   no automatic destructive database downgrade. An application rollback is allowed
   only when its actual schema/handler compatibility is established; otherwise
   keep dispatch closed and repair forward.

The proxy resolves the fixed API service through the trusted Docker DNS resolver
with a bounded cache and retries disabled. This avoids continuing to route to a
replaced container address. It keeps SSE buffering disabled and bounds writes.
Contracts: [Nginx proxy_pass](https://nginx.org/en/docs/http/ngx_http_proxy_module.html#proxy_pass)
and [resolver](https://nginx.org/en/docs/http/ngx_http_core_module.html#resolver).

## Alert actions

* Eligible queue age >30 seconds for two minutes: inspect workers, provider
  capacity and lock waits; distinguish intentional cooldown.
* Expired lease untreated >15 seconds or three failed renewals: inspect the
  control pool/DB/event loop and stop the unhealthy worker.
* Pool >80% for one minute or acquisitions >1 second: reduce read pressure,
  inspect transaction duration, preserve heartbeat/control capacity.
* Unknown exposure >15 minutes or overrun: suspend affected admission and obtain
  evidence-based accounting review; never discard uncertain provision.
* Missing handler or event invariant: suspend the scope, restore compatible code
  or diagnose the invariant. Do not silently repair by regeneration.
* Disk >80%, backup failure or last verified copy >24 hours: correct storage or
  backup delivery, close admission as needed and publish the actual RPO.

Fault drills use isolated disposable services and retain their failure logs.
Operational probes must report unknown disk/backup status when no measurement is
available. A test fixture is not a successful off-host backup or a live provider
validation.

## Concrete artifact and command sequence

Build only from the reviewed commit using the locked dependencies. Resolve the
runtime's actual `docker image inspect --format '{{.Id}}'` value and use that
`sha256:` identity as `ATP_RUNTIME_IMAGE`; a mutable tag is not a release identity.
Save an archive outside Git, hash it and retain the release manifest alongside it.
After transferring it to the operator host, verify the archive SHA-256 and `docker
load` it; verify the loaded image ID before use. Registry distribution is an
external deployment option and has not been performed by this repository task.

Apply `compose.release.yaml` after the base/demo files. Its runtime services use
only the supplied immutable identity, with builds disabled and pulling disabled.
Inspect `docker compose ... config` before starting services. A compatible rollback
uses the previous verified immutable identity and the same explicit preflight;
never run an old migration downgrade automatically to make a binary start.

With the dedicated administrator credential, execute `atp admin delivery --phase
close`, then `--phase drain` until it reports ready. Stop drained worker processes.
Keep the external dispatch fence closed. Run the migration command under the
migration credential and **stop immediately if it exits nonzero**. Execute
`atp admin delivery --phase preflight` with the new configured epoch and handlers;
its exact read/write grants must pass. Reopen only via the separate `--phase reopen`
command after reviewing all remaining delivery checks. The actual failed migration
exercise retains its old revision, rolls back partial DDL and keeps both flags
closed. It does not automatically invoke reopening or destructive downgrade.

No production TLS certificate, DNS, off-host backup destination, registry upload
or production deployment is claimed. The private demo/test HTTP boundary is not
a production TLS terminator; the operator must supply TLS at the ingress before
public service. Keep the provider credential exclusively in the worker's protected
manual configuration; ordinary PR CI receives no live provider credential.
