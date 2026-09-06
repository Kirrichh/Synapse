# Stage 14 — execution lineage and reconstruction

Implementation contract, based on Specification v2.2 §§13, 21, 28–31, 35–39
and Implementation Patch Plan v1.3 Stage 14. Baseline: merged PR #105,
`c4c90bd435fb801673bef8c8fa760a2eb6e96382`.

## Ownership and authority

LIN-01. New owners live in `gold/stage14/`. Module boundaries follow cohesive
responsibility, state, failure semantics and dependency direction. There is no
LOC threshold. Existing record, replay, library, publication and recovery
owners keep their bytes and authority. A lineage record references their exact
content; it does not grant admission, declare task success or dispatch work.
The only production entry remains `python -m synapse -> synapse.cli.main`.

LIN-02. Nodes carry a closed class, a complete hash-bound record reference and
an occurrence scope. Artifact content identities are unchanged. Distinct runs
and attempts remain distinct occurrences even when they use identical bytes.
Edges use the existing common relation vocabulary, extended in its original
owner. The edge matrix fixes source/target classes. In the graph, edges point
from evidence/prerequisite to the dependent occurrence. The semantic relation
is given by the matrix, never inferred from a timestamp or file order.

LIN-03. Graph identity binds schema/profile, scope, node references, roles and
typed edges in canonical order. Reordering input does not change identity.
Unknown types, conflicting identities, missing endpoints, cycles and resource
limit violations fail closed. Resource limits bound data processing, not source
code size. Graph construction alone establishes structural consistency, not
physical evidence completeness or permission to reuse.

## Physical reconstruction and completeness

LIN-04. Reconstruction reads the actual durable owners and verifies exact
references, run/attempt/context identity and committed boundaries. A consumer
compares the stored graph with reconstruction from those sources. A narrative,
caller-authored relation or observation timestamp cannot supply missing proof.
Each graph identifies its scope and the policy defining required evidence.

LIN-05. Attempt requirements follow actual durable phases and independently
verified outcome. Successful C1, no candidate, refusal, interruption, invalid
proof and observed negative-guard reuse have different obligations. Every
reached phase is represented; unexecuted phases are not fabricated. Negative
reuse requires source publication, real replay/delivery, the observed avoided
dispatch and independent promotion; it must not claim a new consumer oracle.
Incomplete proof cannot support a completion/reuse claim, even when the graph
itself has a valid content hash. Failed attempts remain in the run history.
The prepared intent/proposal/decision/accepted-plan bundle is retained even
when delivery is refused or interrupted before worker completion. An existing
pre-dispatch context resolves to its original audit and delivery records.
The audit's selection and exact replay observations supply mandatory typed
dependencies into the delivered context. A completed worker is not required
to prove that preparation occurred, and preparation never proves dispatch.

Execution feedback follows its explicit source-result reference to an earlier
attempt in the same run. Reconstruction compares the source result and its
physical execution proof before attaching its graph to the new intent. The
run's ordering or mere coexistence of two attempt graphs does not supply this
dependency. Historical source records remain immutable.
`execution-incomplete/v1` and `attempt-incomplete/v1` require an explicit
`EVIDENCE_GAP` node bound to the independent verifier's failure codes and expected
phase references. They retain `INVALID_CONTRACT` rather than hiding the failure
behind a read exception. Publication accepts only `execution/v1`.

LIN-06. Publication's lineage fragment belongs to its existing atomic commit.
It binds independent pre-publication verification, request, authority decision
and exact created objects. Its upstream graph uses the same `execution.py`
reader as final attempt completion and reaches the producer's snapshot,
retrieval, replay, accepted plan, delivery, C1 and oracle. The source catalog is
retained as an immutable prepared member bound by the verification's attempt
context digest. Final publication result references this fragment;
the later attempt graph links publication to final StructuredOutcome. There is
no outcome/publication hash cycle. A missing or inconsistent fragment invalidates
committed publication evidence. Recovery uses the existing transaction, not a
second writer or publication operation.

LIN-07. Attempt and run graph records use the existing RunRecordStore and its
mutation/recovery session. Terminal result visibility requires its lineage
record. A crash in a local completion suffix is repaired idempotently from
durable phase records without repeating the worker or C1. A completed record
is not rewritten during reconstruction. Historical snapshots remain immutable;
later use adds its own occurrence and does not revise the producer's outcome.

LIN-08. Reachability starts at an explicit claim/root. Library references
reachable from retained lineage feed the existing LINEAGE retention category.
Payloads stay with their owners. Content deduplication never merges execution
occurrences or removes required provenance. Historical interpretation uses
the recorded schema and policy, with no silent upgrade of old evidence.

## Boundary with Stage 15

LIN-09. Execution lineage and telemetry accounting have separate completeness.
Stage 14 supplies typed links to actual observations and reports missing
telemetry explicitly. Canonical telemetry, provider-call reconciliation and
read-only EventStream are Stage 15's owners. A Stage 14 execution graph cannot
claim telemetry completeness, token savings or whole-Stage-4 completion merely
because it reconstructs. The full §§29–31 acceptance chain is checked again
when Stage 15 supplies its actual canonical records; this dependency is not a
waiver of the final completion predicate.

## Acceptance

LIN-10. Acceptance is external. Separate suites cover structural contracts,
physical attempt/run reconstruction, atomic publication, observed reuse,
restart/recovery, retention and controlled mutations. Mandatory mutation
acceptance verifies endpoint integrity, explicit dependency evidence,
publication verification binding and mandatory-edge completeness. Reports
identify killed/survived checks and restoration; product code never imports
fixtures or mutation harnesses. Heavy suites have independent CI shards.

## Design sources and adopted limits

- [W3C PROV-DM](https://www.w3.org/TR/prov-dm/) and
  [PROV Constraints](https://www.w3.org/TR/prov-constraints/): typed entities,
  activities, agents and consistency; timestamps do not prove dependencies.
- [IPFS Merkle DAG](https://docs.ipfs.tech/concepts/merkle-dag/): immutable
  content and dependency identities; no IPFS service or dependency is introduced.
- [SLSA 1.2 verification](https://slsa.dev/spec/v1.2/verifying-artifacts): bind
  evidence to the actual subject and expected producer; no SLSA level claim.
- [OpenLineage object model](https://openlineage.io/docs/spec/object-model/):
  separate work definition, execution occurrence and artifact.
- [Bazel remote caching](https://bazel.build/remote/caching): distinguish CAS
  content from execution metadata and expected input/environment identity.
- [Execution Lineage, arXiv:2605.06365v1](https://arxiv.org/html/2605.06365v1):
  explicit resolved dependencies and stable boundaries. The two-task preprint
  provides design guidance, not economic evidence for Synapse.

Current storage versions are attempt-context/v5, publication-result/v3 and
lineage/v1. Older records do not acquire lineage claims by silent reinterpretation.
The execution catalog binds original record directories and coordinator IDs;
relocating or deleting those retained stores requires an explicit future
migration/retention contract. Reading a publication grants no new authority.

The uploaded draft contributes the typed-graph structure, iterative cycle
checking and permutation/restart acceptance ideas. Its universal successful
chain, isolated runtime model and numerical eLOC justification are replaced
by the governing contracts above.

The document/architecture re-audit and corrective acceptance are recorded in
[Stage 14 audit](STAGE4_STAGE14_ARCHITECTURE_AUDIT.md).
