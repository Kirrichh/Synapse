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

`stage12/reusable.py` owns independent verification of reusable outputs and
their existing scoped admission. It neither executes C1 nor publishes a second
copy of Library data. All three modules have distinct contracts and reasons
to change; their boundaries are not derived from file lengths.

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

Stage 12 implements the positive reusable predicate now. Its first closed
verification profile is `rejected-patch-guard/v1`, using the already normative
`rejected_hypothesis_guard` kind. A complete C1 report and a coherent negative
oracle establish that one exact patch did not resolve one exact task. The
platform derives the guard's entire program and contracts from this evidence
and compares the admitted executable against it. Replay or compilation alone
cannot establish that negative fact.

The future-use domain binds the original repository revision, governing task,
command policy, patch digest, oracle identity, environment and policy digest.
The pure CVM program returns the 32 fingerprint bytes of this domain for
duplicate-hypothesis detection. It makes no claim about a different patch and
does not authorize execution. The compiler, full Unit/Blob/Manifest, actual
Library bytes, current-attempt attestation and domain-specific lifecycle must
all agree. Both independent ingestion/publication ADMIT decisions must belong
to the connected project's configuration, run, attempt, verified revision and
environment and exist in their exact committed journal prefix.
The publication's retained grant evidence must match this exact domain, with
no capabilities or oracles granted to the pure guard. An ADMIT over another
grant is insufficient. The existing admission owner checks that evidence.

`register_reusable_candidate` attaches an already admitted output to the
existing run-record store after durable C1 completion and before the attempt
result. Registration requires the sealed evidence of the existing
`admit_library_write` operation. Verification and completed-result reads reopen
the underlying owners; a registration record alone grants no status. Seeds,
worker narratives, arbitrary admission strings and retrospective attachment
to a completed attempt are refused. The canonical composition binds the
connected project's stores to the same verifier used during recovery.

When that predicate holds, the outcome lists the actual behavior and records
`ADMISSION_CONFIRMED`. This means an existing scoped admission was verified;
it does not claim that Stage 13's complete atomic publication transaction was
executed. With no such proof, the projection remains `NOT_ATTEMPTED` and empty.
Stage 13 owns general candidate extraction and the complete cross-store write
set; Stage 12 does not automatically turn every unsuccessful attempt into
published knowledge. Later useful reuse remains a separate consumer event.

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

The v2 verification/outcome transports and v2 outcome policy embed the verified attempt transports in RUN, instead
of trusting copied child status strings. The same aggregate function is used
for construction and inspection. It distinguishes a preparation failure from
an ordinary terminal attempt, preserves INVALID precedence and already earned
reusable value across later unresolved attempts, and forbids continuation after
FULL. Rehashing RUN, a child label or an admission projection does not replace
the missing predicate. JSON inspection checks consistency; only owner-backed
consumer revalidation restores trust in the underlying evidence.

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
