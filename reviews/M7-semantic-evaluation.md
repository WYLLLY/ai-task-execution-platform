# Synthetic semantic and adversarial evaluation

The retained `tests/unit/test_semantic_fixtures.py` campaign contains20source
dossiers and20document-injection variants. It pins document owner/version/hash,
expected fact and exact citation span. Its40tests are part of the unfiltered802
regression suite and the named `test-load-contracts` gate. These are scripted
outputs, not samples from a real model.

| Scripted group | Cases | Schema valid | Authentic citations | Foreign reference refused | Summary agrees with pinned fact |
|---|---:|---:|---:|---:|---:|
| Normal dossiers |20|20|20|20|20|
| Adversarial variants |20|20|20|20|0|

The20contradictory summaries are deliberately published semantic failures: a
structurally valid, authentically cited report can still make a false claim.
Their fixture tests PASS because the evaluator detects that distinction; this
must never be advertised as40factually correct reports. Untrusted documentary
instructions remain data rather than privileged context. Owner/reference checks
are evaluated independently of factual correctness, including content-identical
foreign documents and fabricated quotations.

The cumulative protocol/workflow suites additionally exercise forbidden tool
names, malformed names/arguments, excessive cardinality, refusal/incomplete
outputs, invalid citation versions/spans, current rights, opaque protocol items
and retained billable usage under functional rejection. The model cannot invoke
save_report directly; report persistence follows deterministic orchestration.
These controls do not promise prompt-injection immunity or general factual truth.

Real-provider semantic/adversarial campaign and smoke: **SKIPPED**. No separate
paid-call authorization or spending cap was supplied. No live provider request
was made. Ordinary CI uses only synthetic Responses/simulator boundaries. Fixtures
do not validate live provider availability, token counting, invoice reconciliation,
real model quality or resistance to documentary instructions.
