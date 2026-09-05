# Stage 4 / Stage 12: verified outcomes (OD-13)

This amendment completes §27 of the Gold Execution Specification v2.2 and
Stage 12 of Implementation Patch Plan v1.3. The supplied Python drafts are
design input, not additional execution paths. Python ownership follows
responsibility, never a numerical LOC threshold (NR-04).

## Authority and evidence

The new product owners live together under `synapse/experiments/gold/stage12/`
at the user's request. This updates the draft plan's root-level file locations;
there are no compatibility modules at the old paths.

`stage12/verification.py` owns the immutable verification record. It checks the
accepted, durably delivered Stage 10 plan against the governing task and the
actual C1 report, committed task, patch, repository bindings and oracle result.
`stage12/outcome.py` is the only owner of the final status matrix. Neither accepts
worker-supplied completeness flags, discharged-operation lists, admission IDs,
or an editable success label as evidence. Content hashes identify records;
they do not replace checking the referenced records and their relationships.

The existing `runner/c1_boundary.py` remains the only Gold-to-C1/C2 boundary.
Verification reads existing evidence through it; it never invokes a second
worker, controlled change or oracle. C1/C2 production modules are unchanged.
Historical plan inspection does not grant new execution permission. Both the
policy-accepted and the governing-human-accepted route remain valid; historical
approval is checked against the execution it authorized, without requiring a
new approval merely to read a completed run.

## Frozen status precedence

| Status | Required observation |
| --- | --- |
| `INVALID_CONTRACT` | Contradictory, substituted, corrupt or unsupported authority/evidence; outranks all success claims and infrastructure labels. |
| `INFRA_ERROR` | A recorded infrastructure failure or an interrupted execution whose external result is unknown. |
| `NO_CANDIDATE` | C1 durably reports that no candidate was materialized. |
| `FULL` | Applied validated C1 evidence, resolving compatible oracle, every accepted-plan verification obligation discharged, and every required binding resolved. |
| `VERIFIED_REUSABLE_PARTIAL` | Task is not FULL and a newly produced independently verified reusable object has a committed admission for its declared future-use domain. |
| `FAIL` | Recorded controlled-change or delivery refusal; it does not establish reusable value. |
| `UNRESOLVED` | A coherent evaluated attempt has not established FULL or verified reusable partial. |

The order is normative. Missing verification coverage is never inferred from
an oracle success. An oracle result for a different commit pair is invalid,
not a failed test of this candidate. An unimplemented verification kind remains
undischarged. Rejected/corrupt inputs do not become ordinary negative oracle
results. Telemetry completeness is orthogonal to correctness: unknown is not
zero, and Mini's token observations remain visible without certifying total
platform accounting.

## Publication dependency correction

The literal combination of §§27–28 creates a hash cycle: final outcome includes
publication, while publication authority requires that final outcome. Replace
the required final `outcome_ref` input to publication authority with the sealed
`verification_ref` and exact proposed write set. The order is:

1. Verify the attempt and seal the verification record.
2. Stage 13 evaluates publication authority and atomically commits the complete
   publication set, or records its refusal/failure.
3. Seal the final outcome over verification and the publication result.

Stage 12 reports publication as `NOT_ATTEMPTED` and created behaviors as empty.
It cannot emit `VERIFIED_REUSABLE_PARTIAL` from a candidate, a source memory,
an operator seed, or a string naming an admission. The vocabulary and predicate
are fixed here; production of newly admitted behaviors belongs to Stage 13.
Later useful reuse is a separate consumer observation, never an initial status.

## Durability and consumers

The existing controller checkpoints C1 completion before final verification.
An attempt result contains its verification and structured outcome in one
immutable record, so there is no orphan-outcome transaction protocol. A restart
after C1 completion materializes that suffix without repeating external work.
The outcome identity binds the manifest/version/policy, context and all phase
references, worker delivery, C1/evidence/oracle, discharged obligations and
resolved bindings, plus publication and telemetry declarations.

Stored JSON is a transport representation. Consumers must use the platform
validation path before trusting its status; rehashing edited JSON is not
approval. Completed-run reads recheck retained authority/evidence. Missing or
corrupt retained proof blocks outcome claims while preserving C1/C2 history.
Only a verified `FULL` permits the controller's success stop. A run outcome
binds all attempt result identities and its terminal decision, including runs
that stop before their first attempt. Baseline fallback remains explicitly
separate from Gold correctness.

## Acceptance boundary

Acceptance code stays under `acceptance/` or `tests/` and is never imported by
product code. Cheap record/matrix checks and repository/process/recovery
scenarios use separate files; heavy files run as independent GitHub matrix
jobs. Required cases include real successful and unresolved attempts, forged
labels and refs, missing plan/report/bindings, evidence–oracle disagreement,
restart after C1, completed-result corruption, and unchanged Mini usage.

## Sources and design limits

The design uses the subject/verifier/policy/input-evidence separation in
[SLSA v1.2 Verification Summary Attestation](https://slsa.dev/spec/v1.2/verification_summary).
This is a design influence, not a claim of SLSA compliance. Content-addressed
records must form an acyclic graph; the publication correction follows that
constraint ([Merkle DAGs](https://docs.ipfs.tech/concepts/merkle-dag/)).

Durable completion must precede replay decisions; external execution without
a recorded completion is not automatically exactly once
([Temporal activities](https://docs.temporal.io/activity-definition)). Existing
Synapse checkpoints and fences implement this boundary; no Temporal, IPFS,
database or telemetry dependency is introduced.
