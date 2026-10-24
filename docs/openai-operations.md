# M5 Responses and accounting operations

M5 introduces the explicit `analysis` workflow version `2`. Version `1` keeps its
M4 simulation contract. A worker whose pinned profile does not match a task
suspends that task instead of substituting a handler. Do not run incompatible
worker modes against the same ready queue.

## Provider contract and configuration

The shipped profile supports `gpt-5.4-mini-2026-03-17`, standard service tier,
`o200k_base` with tiktoken 0.14.0, and tariff `gpt54mini-2026-10-23`.
The [official model page](https://developers.openai.com/api/docs/models/gpt-5.4-mini)
was checked on 2026-10-23: standard prices per million tokens are USD 0.75 input,
0.075 cached input and 4.50 output. Rates in configuration are integer micro-USD
per million tokens, with ceiling rounding after summing the components. Actual
cached tokens can reduce settlement, never the initial reservation.

Copy `config/openai.synthetic.json` to an operator-managed profile and replace
its synthetic project identifier and illustrative RPM/TPM values with verified
project limits. No account tier is inferred. The effective local limits use
80% of those values. The profile is immutable once workflow version 2 is registered;
changing model, tariff, tokenizer or project requires a new reviewed handler/version.
Registration never clears durable authorization windows or Retry-After cooldowns.
The singleton admission controller conservatively shares capacity across all modes.

Requests use the [Responses API](https://developers.openai.com/api/reference/resources/responses/methods/create),
`store=false`, explicit `service_tier=default`, one strict read-only function call
at a time and a strict final report schema. A response served at another or missing
tier does not release its provision at the standard price. Full protocol output
items and call IDs are checkpointed privately; encrypted reasoning is replayed
without decoding. There is no `previous_response_id` dependency.

The complete serialized request is estimated with pinned BPE tokens plus 25% and
256 framing tokens. The input estimate is bounded by the configured value (at most
12,000), output by 2,000 tokens, and the existing cumulative task limits remain in
force. This is a conservative local estimate, not exact provider accounting.
Actual overruns remain fully recorded. The image prewarms and hash-checks the
public tokenizer asset. Runtime refuses missing/corrupt assets without downloading.
For local verification, run `make prepare-tokenizer` once after dependency sync;
this downloads public static tokenizer data, not a paid model response.

## Explicit activation

Use the administrative database role with:

```sh
atp admin provider-configure --file /path/to/reviewed-profile.json
```

The API never loads an OpenAI key. The optional `compose.openai.yaml` mounts it
only into the worker and gives that worker an explicit egress network. Set
`OPENAI_PROFILE_PATH` and `OPENAI_KEY_PATH` to reviewed local files, then include
that override along with the base and demo Compose files. It does not enable
PostgreSQL dispatch or application dispatch automatically. The worker command is
`atp worker --openai`; `--once` executes one durable unit. Existing administrative
admission/dispatch controls and the deployment epoch still apply.

HTTPX uses a fixed endpoint, disabled proxy/environment inference, zero transport
retries, no redirects, bounded response bytes, transport timeouts and a 60-second
absolute deadline further bounded by the task deadline. Unknown results retain
exposure and suspend without regeneration. Only an authorized owner can explicitly
accept overlap/cost risk and request a replacement. Local fencing cannot cancel
already-authorized remote work.

## Evidence-based accounting

`atp admin accounting --call UUID --file decision.json` uses the admin role.
A settlement file contains:

```json
{
  "kind": "settlement",
  "evidence_id": "operator-evidence-1",
  "revision": 0,
  "source": "provider export reference",
  "proof_sha256": "<64 lowercase hexadecimal characters>",
  "reason": "Explain the evidence and reconciliation decision",
  "cost": 138,
  "tokens": 120,
  "final": true,
  "estimated": false
}
```

`revision` is the expected current call-cost revision. Exact retries return the
same decision; changing its contents conflicts. After a lost acknowledgement,
retry the exact same call/evidence/revision, never invent a new identity.
`final=false` records partial evidence and keeps the entire provision.
A final estimate must use `estimated=true`; it is never labelled a proven invoice.
The proof hash/reference is an operator attestation, not automatic proof of invoice
authenticity. Only trusted administrators possess reconciliation credentials.

Provider observations retain bounded response identity, actual service tier and
partial numeric usage independently of task ownership. They contain no response
text or opaque reasoning. Definitive settlement replaces exposure, attributes
usage to authorization B's UTC date and does not restart terminal tasks.

An overrun blocks new authorizations until an explicit administrative decision:

```json
{
  "kind": "acknowledgement",
  "evidence_id": "overrun-review-1",
  "revision": 1,
  "source": "operator review reference",
  "proof_sha256": "<64 lowercase hexadecimal characters>",
  "reason": "Explain why authorizations may resume",
  "tariff_review": "Document the tariff review",
  "estimator_review": "Document the estimator and margin review"
}
```

The original overrun flag and actual amounts remain unchanged. The acknowledgement
applies only to that revision; a later changed settlement requires another review.
Admission still checks remaining task, owner and global budgets. A previously
suspended task is not automatically restarted by administrative acknowledgement.

Migration round-trips are tested on disposable data. An older schema cannot
represent OpenAI call rows or their tariff; a downgrade with such rows fails its
constraints. Do not treat schema downgrade as accounting retention or cleanup.

## Verification and separate live smoke

`make test-accounting` and `make test-provider-contracts` run synthetic fixtures
against the disposable real PostgreSQL/Redis stack. Ordinary CI has no live key.
The test runner has a bounded 1 GiB memory allocation because tokenizer tables and
independent Python processes coexist; the optional OpenAI worker is bounded at
512 MiB. Other service limits are unchanged.

`make smoke-openai` prints SKIPPED unless all of the following are explicitly
provided for a separately authorized run: `OPENAI_SMOKE_AUTHORIZED=yes`, a positive
`OPENAI_SMOKE_CAP_MICRO_USD` (at most USD 1), `OPENAI_PROFILE_FILE`, and a worker-only
`OPENAI_API_KEY_FILE` (or exclusive `OPENAI_API_KEY`). Never provide live keys to
untrusted PR code. No live run was authorized for this milestone implementation.

The smoke reserves two maximum bounded contexts before sending, uses only fixed
synthetic colors, forces a read-tool call, replays the returned protocol items and
a deterministic empty tool result, then requires a final structured report. It
stops on ambiguous/missing usage or insufficient remaining budget, checks confirmed
cost plus the next request's reservation before every send, has no retry and caps
the entire exchange at 60 seconds. The cap uses conservative reservations; it is
not a guarantee that a provider cannot report an unexpected billing overrun.
A successful fixture run is not live provider validation or evidence of semantic
report quality. M6 streaming and M7 restoration/retention remain out of scope.
