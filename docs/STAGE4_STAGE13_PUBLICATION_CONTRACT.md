# Stage 13 atomic publication — implementation checkpoint

Status: **in progress; this checkpoint is not merge approval**. Branch:
`codex/stage4-stage13-atomic-publication`, based on merged Stage 12, PR #104.
The governing sources are Stage 4 Gold Execution Specification v2.2 §28 and
Implementation Patch Plan v1.3 Stage 13, including NR-04, NR-06, NR-08 and NR-11.

## Responsibility boundaries

New product files are grouped in `gold/stage13/`. `publication.py` owns candidate
extraction and independent exact authority; `publication_store.py` owns the
cross-store transaction and recovery; `run_publication.py` owns attachment to
the canonical C1 completion suffix. Existing persistence, library, provenance,
lifecycle, taint and admission owners retain their semantics and physical files.
There is no numerical LOC rule. Acceptance fixtures and mutation execution live
outside the product package. Heavy acceptance files have separate CI shards.

## Implemented transaction contract

The initial extraction profile is Stage 12's `rejected-patch-guard/v1`: a pure
program carrying the fingerprint of an independently rejected patch in its exact
task/base/oracle/policy/environment domain. It grants no execution capabilities
or oracle access. Its negative fact is platform-derived; arbitrary worker code
or prose is not copied into executable knowledge.

Independent verification precedes publication; final StructuredOutcome follows
publication. This preserves Stage 12's solution to the outcome/publication hash
cycle. An unresolved task can have a committed verified reusable partial; it
cannot become FULL merely because publication succeeded. Outcome and verification
transports advance to v3; reusable registration advances to v2 with the actual
publication transaction reference. No committed output is reported as
`NO_COMMITTED_OUTPUT`; legacy verified admission remains `ADMISSION_CONFIRMED`.

One existing project coordinator guard and one mutation ticket cover the entire
write set. The opening coordinator frame durably retains the exact undo contract
before any participant obtains a ticket. The existing frame size bound applies;
an oversized recovery record is refused before participant writes.

The prepared immutable snapshot retains the verified request and actual C1
artifact bytes. Its marker establishes readiness only. Actual attestation, taint
and lifecycle records precede independent ingestion/publication evaluation.
The evaluator's proof includes configured planner/worker/source actors, builder,
attester, oracle, extractor and publisher. Write-only gate declarations have no
unused retrieval probes or fictitious reader identities.

The existing library admission owner commits both gates and performs its normal
capability-bound write using the outer ticket. The transaction adds ADMITTED and
INDEXED lifecycle records and checks the exact authorized subject, grant, context,
physical member set, journal receipts and read-back bytes. The terminal committed
snapshot marker is last. Per-object commit requirements keep provisional objects
out of ordinary index and consumability reads. Committed readers verify all
required immutable files and retained append-only journal prefixes.

Commit visibility is checked against the object's publication interval. A later
point-of-use transaction can read an already committed object while retaining
the existing read-gate checks. Each gate requires its declared role before
invoking probes. Stage 12 owns the single durable reusable-output registration
boundary, shared by explicit admission and canonical publication.

A repeated committed request returns its original transaction without writes.
An interrupted transaction restores only the captured journal suffixes and
metadata images, then records quarantine. Recovery neither evaluates authority
nor publishes again. A second interruption during recovery repeats the same
verified undo, including the case where quarantine already exists. Unreachable
immutable CAS leftovers are not searchable content. Evidence corruption blocks
recovery rather than inventing missing proof.

## Observed verification, 2026-09-06

On checkpoint `da784a4`, local Python 3.12 runs passed the authority acceptance
file (7 tests), mutation acceptance file (5 tests, including its unchanged
positive control), recovery acceptance file (10 tests) and canonical publication
file (1 test).

After the integration corrections described above, targeted read-gate regression
checks passed (11 tests; 202 unrelated cases deselected), as did the write-gate
contract and dependency-direction checks (273 tests), existing Stage 12 reusable
registration (1 test), and canonical publication with resume (1 test). These are
targeted results, not a full-suite result or final Stage 13 acceptance.

## Remaining work at this checkpoint

- Finish promotion from verified reusable candidate to observed useful reuse
  against the real MechanismUseRecord and later independently verified outcome;
  the current code does not implement that stronger claim.
- Complete explicit refusal/quarantine/review decision reporting and reconcile
  all normative fields and retention/lineage requirements with the final contract.
- Finish mutation and acceptance execution after the final changes, including
  duplicate content with distinct provenance and the remaining recovery edges.
- Complete the ownership/integration review and update acceptance evidence before
  declaring Stage 13 ready for merge.

## Primary-source design basis

SQLite's [atomic commit description](https://sqlite.org/atomiccommit.html)
informs durable undo before participant writes and a last commit point. The
implementation uses Synapse's existing framed stores; it adds no SQL backend.
SQLite's [transaction documentation](https://www.sqlite.org/lang_transaction.html)
informs exclusive writer ownership and explicit handling of interrupted writes.
[SLSA verification guidance](https://slsa.dev/spec/v1.2/verification_summary)
informs matching actual subjects and trusted provenance to policy independently
of the artifact producer. These are design references, not claims of SQLite or
SLSA certification.
