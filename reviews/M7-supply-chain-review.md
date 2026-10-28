# M7 supply-chain verification

Date: 2026-10-28. Scanner: official Trivy0.76.1, downloaded from its tagged
release and SHA-256 verified before execution. The raw JSON reports are retained;
no ignore list, severity suppression or fabricated clean-image result is used.
This is dependency/artifact vulnerability inventory, not an exhaustive exploitability
analysis or a production security certification.

## Confirmed remediations

- Locked certifi2026.7.22 was reported for GHSA-6q48-6c7w-9gw2. Its upstream
  advisory requires2026.10.18 or newer. A maintained lockfile regression first
  failed, then passed after the isolated upgrade to2026.10.23 with hashes.
- Runtime Python included installation wheels/tooling that the application does
  not use. Actual-image assertions first failed for ensurepip, then for the base
  interpreter's pip after the reviewer showed the virtualenv check was insufficient.
  Both the base and application interpreters now pass the absence check; no
  runtime package-install capability is required or added.
- Debian's official security repository provided libssl3/openssl
  `3.0.22-1~deb12u2`. The actual artifact assertion first failed on the older
  package, then passed with that exact patched version pinned in the Dockerfile.

Sources: [certifi advisory](https://github.com/certifi/python-certifi/security/advisories/GHSA-6q48-6c7w-9gw2),
[Trivy image scanning](https://trivy.dev/docs/latest/target/container_image/).
Version availability evidence is `m7-os-patch-availability.txt`.

## Exact scan results

| Inventory | Initial | Final |
|---|---|---|
| Runtime lockfile libraries |1CRITICAL |0findings |
| Runtime image Python libraries | Reported vulnerable dependencies/tooling |0findings |
| Entire runtime image, including Debian |266:4CRITICAL,66HIGH,101MEDIUM,92LOW,3UNKNOWN |244:2CRITICAL,59HIGH,91MEDIUM,89LOW,3UNKNOWN |

The final scanner run completed successfully. **The image is not vulnerability
free.** No HIGH finding in that final report had a fixed version supplied by the
scanner. The two remaining CRITICAL records are:

- CVE-2025-7458, Debian libsqlite3-0 `3.40.1-2+deb12u2`, status affected, no
  fixed version supplied. The approved application uses PostgreSQL and provides
  no arbitrary SQL/code execution interface, but this fact is not a general proof
  of exploitability absence. Retain this unresolved base-package inventory risk.
- CVE-2023-45853, zlib1g `1:1.2.13.dfsg-1`, scanner status will_not_fix. Debian
  explicitly explains that the vulnerable contrib/minizip is not built into the
  affected source's binary packages in bookworm. This vendor scope note is retained
  as triage evidence; the raw critical scanner record is not hidden.

Vendor records: [SQLite issue](https://security-tracker.debian.org/tracker/CVE-2025-7458),
[zlib scope](https://security-tracker.debian.org/tracker/CVE-2023-45853).

Ordinary CI scans both inventories, gates HIGH/CRITICAL Python-library findings
and uploads raw OS results for operator triage. It does not silently assert that
unfixed OS findings are resolved. Production release still requires an identified
operator's review of this inventory and deployment prerequisites. No M7 merge or
production deployment is authorized here.

## Artifact identity

Verified runtime image:
`sha256:d8693ab05363959e257f3a0f7ad73f8a622f2c8a3fb4b45a4ffea6324b8a8007`.
Its saved archive SHA-256 is
`a9c32af73c9a0ed402d0f87aad9809d650c1a29d1296fc3b9eaa1a6b3341bbfb`.
The archive remains outside Git. The immutable Compose overlay was rendered with
that exact identity and no application build/pull operation. These are local
artifact identities, not a claimed registry publication or signed release.

The final rebuilt-artifact scan is `m7-release-image-scan.json`; the locked
runtime scan is `m7-release-dependency-scan.json`. Docker29 reports the OCI
manifest identity above; Trivy reports configuration digest `sha256:12b70c6f18c88b666cff47c3d7917e9bf0b71ff865d443af4bf04d6b36f8feb0`.
The archive index links that manifest to this configuration, and both blob
hashes were verified. `m7-release-manifest.json` preserves this relationship.
The prior candidate scans remain as historical evidence.
