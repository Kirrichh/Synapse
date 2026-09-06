# Stage 13 atomic publication and observed useful reuse

Status: **implementation and local acceptance complete**. Required GitHub checks
remain the merge gate for the pushed head. Branch:
`codex/stage4-stage13-atomic-publication`, based on merged Stage 12, PR #104.
The governing sources are Stage 4 Gold Execution Specification v2.2 §28 and
Implementation Patch Plan v1.3 Stage 13, including NR-04, NR-06, NR-08 and NR-11.

## Responsibility boundaries

New product files are grouped in `gold/stage13/`. `publication.py` owns candidate
extraction and independent exact authority; `publication_store.py` owns the
cross-store transaction and recovery; `run_publication.py` owns attachment to
the canonical C1 completion suffix. `reuse.py` owns actual guard consumption and
its retained observation; `promotion.py` owns the independent downstream promotion
policy. `rejected_patch_profile.py` owns the pure fingerprint program identity
shared by C1 derivation and compatibility. Existing persistence, library, provenance,
lifecycle, taint and admission owners retain their semantics and physical files.
There is no numerical LOC rule. Acceptance fixtures and mutation execution live
outside the product package. Heavy acceptance files have separate CI shards.

The Stage 12 verification transport was moved to `verification_contract.py` so
the physical verification reader can consume publication results without a
circular import. It remains one contract and one disk-reading producer, with no
second authority factory or compatibility shim.

## Implemented transaction contract

The initial extraction profile is Stage 12's exact negative-fact profile, now `rejected-patch-guard/v2`: a pure
program carrying the fingerprint of an independently rejected patch in its exact
task/base/oracle/policy/environment domain. It grants no execution capabilities
or oracle access. Its negative fact is platform-derived; arbitrary worker code
or prose is not copied into executable knowledge. The v2 domain includes the
frozen replay gas budget. The existing CVM adapter calibrates the prospective
transition contract for an exact lossless five-integer SHA-256 return: no calls, branches,
host activities or caller-supplied behavior are accepted. This compile-time
certificate grants no admission or replay observation; subsequent governed
capture and replay must independently conform to the declared trace.

Independent verification precedes publication; final StructuredOutcome follows
publication. This preserves Stage 12's solution to the outcome/publication hash
cycle. An unresolved task can have a committed verified reusable partial; it
cannot become FULL merely because publication succeeded. Outcome and verification
transports advance to v5; reusable registration is v2 with the actual publication
transaction reference. Publication request, authority decision, result and undo
transports are v2. Missing publication is `NO_COMMITTED_OUTPUT`; legacy verified
admission remains `ADMISSION_CONFIRMED`.

The authority derives explicit refusal reasons from independently verified
execution. A resolved task without a supported extractor is `REJECTED` for
publication and retains its independently computed task status. Invalid proof
produces `QUARANTINED`; incomplete/refused execution and a missing candidate have
distinct refusal reasons. These evaluator-produced decisions are retained in
the existing run store and have an empty authorized write set. They create no
library object. `REVIEW_REQUIRED` is part of the result vocabulary; the frozen
pure-guard profile requires no human review, and unsupported profiles are
rejected rather than made eligible through a generic approval flag.

Recovered transactions attach actual quarantine evidence to the attempt and
final outcome. Its identity is checked against the original coordinator opening
frame, including the request identity, undo hash and completed interval. The
canonical verifier reopens that evidence and compares the pre-publication facts
with current execution verification. A retained run label alone is insufficient.

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

The decision freezes the complete subject set, future-use domain, exact index
visibility, evidence retention roots, pre-publication outcome and coordinator
sequence. These fields participate in the transaction-contract hash. The final
outcome follows publication, so the pre-publication outcome is an independent
input rather than a circular reference to the final outcome.

The prepared snapshot retains the undo image alongside its C1 artifacts. The
committed snapshot retains the actual index image as audit evidence; retrieval
continues to use the existing library index. Reconciliation checks each physical
journal suffix: exact attestation, taint profile, lifecycle transitions, admission
decisions and library operation. It also compares the complete index with its
pre-write image and authorized entry. Additional participant changes prevent the
terminal commit. `created_refs` identifies actual blob, manifest, attestation,
lifecycle, admission and index records and is checked again on reads. Together
with the retained request, verification, outcome and decision, these are the
inputs for Stage 14's lineage DAG; no parallel lineage store was added.

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

Existing committed content is deduplicated by the library's existing owner. The
transaction proves that an unchanged blob/manifest and index entry predate it,
then adds distinct attestation/lifecycle/admission records. Recovery also repairs
an interrupted audit tail after a valid terminal marker and can resume after the
quarantine artifact itself was already written.

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

For the continuation after `4087e52`, observed local checks passed:

| Responsibility | Observed result |
| --- | --- |
| Outcome contract, status matrix and dependency direction | 312 passed |
| Exact publication authority | 13 passed |
| Existing content with distinct provenance | 1 passed |
| Phase recovery and interrupted rollback | 10 passed |
| Durable quarantine and interrupted committed audit | 2 passed; 10 existing cases deselected |
| Publication mutation acceptance | 5 passed, including its positive control |
| Exact write-set mutation acceptance | 2 passed, including its positive control |
| Quarantine through verification and outcome | 1 passed |
| Stage 12 FULL and reusable-outcome regression | 18 passed |
| Canonical FULL with explicit publication rejection and resume | 1 passed |
| Fresh canonical committed publication and resume, final v2 reference contract | 1 passed |

The two mutation files exercised five altered product guards; their unchanged
positive controls also passed. Tests remain external acceptance code and their
heavy files run as separate CI shards. GitHub Actions for `4087e52` passed all
three workflows; the continuation requires its own CI result after pushing.

GitHub Actions run `34019214007` for `5424c1e` completed all 74 other jobs
successfully, including the eight Stage 13 shards and the slow-suite aggregate.
`gold-fast` reported 1,572 passes and two architecture failures with one cause:
`stage12/verification_contract.py` was missing from the ownership manifest.
The manifest now declares its immutable evidence identity and transport
responsibility; the existing disk reader remains the only producer. The
architecture checks and NR-04 rules are unchanged.
After this correction, the architecture, ownership-DAG and dependency-direction
files passed locally: 438 tests. The complete Gold suite was not rerun locally.

## Observed use and independent promotion

The positive path consumes the existing pure rejected-patch guard between
completed worker delivery and C1. Its committed publication, point-of-use
admission, delivered context and real replay must match the candidate. The
comparison includes the patch bytes, task, repository base, command policy,
oracle identity and independently retained C2 configuration, environment kind
and project environment profile, replay gas budget and policy fingerprint. A mismatch continues
through the existing C1 path. It does not reuse a prior oracle verdict as a new
verdict and does not predict success or failure for a different candidate.

The compatibility owner recognizes only the exact inert fingerprint program.
A historical negative fact can be read in the same task at its original base;
its old verification revision and observations remain explicitly historical.
Compiler, host ABI, capabilities, bindings, lifecycle, taint, actual evidence
and ordinary admission checks still apply. The canonical pure-CVM corpus is
selected within its existing ceiling and replay consumes the exact admitted
subject set, including additional negative facts. Required task behaviors
remain mandatory. No alternate replay or index is introduced.

The observer reads the actual return value from the retained terminal VM
snapshot. It checks the live consumable lifecycle and unchanged clean base,
then emits `MechanismUseRecord/v1`. A durable `REUSE_GUARD_COMPLETED` checkpoint
precedes C1 dispatch. The independent verifier reopens publication, delivered
context, admitted basis, replay, VM output and historical lifecycle proof, and
checks that no C1 execution was recorded for the consumer.

The consumer's task status is UNRESOLVED, with no new oracle verdict or FULL.
The closed non-worsening policy compares task resolution for an identical
already-rejected candidate at an unchanged base. The observed benefit is one
avoided duplicate C1 dispatch. This is not a total order over outcome statuses
or a claim about token savings, economic benefit or hermetic caching.

The publication evaluator independently binds that verified consumer result to
the source publication and exact mechanism record. `reuse-promotion/v1` is an
immutable record in the existing consumer run store. Its actor set includes the
producer, consumer and observer, excludes the evaluator, and is checked again
on read. Final verification and outcome v5 expose `OBSERVED_USEFUL_REUSE` through
`reuse_promotions` and `observed_reuse`. The source outcome, admission and
publication stay immutable. Repeated completion returns the same promotion;
completed consumers cannot acquire a retrospective promotion.

Recovery from observation or promotion checkpoints performs proof reading and
suffix completion, with no repeated worker or C1 dispatch. Missing or changed
physical evidence prevents promotion and produces invalid verification.
Stage 15 will consume this narrow actual observation producer; generic
mechanism measurement and Stage 14 lineage remain their own later stages.

Acceptance for this completion passed locally on 2026-09-06:

| Responsibility | Observed result |
| --- | --- |
| Closed profiles, outcome matrix, architecture and dependency direction | 470 passed |
| Canonical two-run use, repeated resume and immutable producer outcome | 1 passed |
| Consumption/promotion mutations and retained physical replay evidence | 4 passed |
| Interruption after observation and after promotion | 2 passed |
| Retained publication history and existing publication/write-set mutations | 8 passed |

These 485 targeted checks are not a full-repository-suite claim. The use and
promotion mutations were rejected by the same assertions satisfied by their
unmodified positive controls. Heavy observed-use, recovery, mutation and
retained-history files have independent CI matrix entries. No acceptance code
is imported by the product. The earlier CI repair at `e79f944` also passed all
three GitHub workflows; this completion requires its own head's CI result.

## Primary-source design basis

SQLite's [atomic commit description](https://sqlite.org/atomiccommit.html)
informs durable undo before participant writes and a last commit point. The
implementation uses Synapse's existing framed stores; it adds no SQL backend.
SQLite's [transaction documentation](https://www.sqlite.org/lang_transaction.html)
informs exclusive writer ownership and explicit handling of interrupted writes.
[SLSA verification guidance](https://slsa.dev/spec/v1.2/verifying-artifacts)
informs matching actual subjects and trusted provenance to policy independently
of the artifact producer. These are design references, not claims of SQLite or
SLSA certification.

Bazel's [remote caching model](https://bazel.build/remote/caching) reinforces
matching action inputs and configuration before reuse. Here the narrower policy
records avoidance of an already rejected proposal; it does not cache a fresh
oracle result or claim Bazel-style hermetic execution.
