# AS2 Persistent Idempotency Store RFC — P0.6.32

Status: **proposed / accepted for doc-only planning**  
Patch type: **doc-only RFC**  
Runtime implementation: **LOCKED**

P0.6.32 defines the production contract for persistent idempotency in the AS2
projection handoff path. It builds on the in-memory deduplication semantics
proved by the P0.6.24 harness, materialized in the P0.6.26 Projection Handoff
skeleton, hardened in P0.6.27 with lock-protected two-phase reservation, and
linked to the P0.6.31 Audit Persistence / Transactional Outbox design.

This RFC does **not** implement a persistent store, does **not** add database,
file, Redis, CAS, queue, or KV writes, does **not** change
`AS2ProjectionHandoffSkeleton`, and does **not** change runtime wiring behavior.
It defines the durable idempotency contract required before production AS2
projection can be considered for `ENABLED` readiness.

---

## 1. Purpose

The current AS2 projection handoff uses an in-memory deduplication index. That
is sufficient for harness and skeleton phases, but it does not survive process
restart or host failure. Production projection handoff requires persistent
idempotency so retries, crash recovery, and operator review cannot accidentally
re-run projection or lose evidence of prior attempts.

The design target is:

```text
Projection handoff receives PreparedAS2Inputs
  -> derive prepared_inputs_hash
  -> reserve idempotency_key = correlation_id + prepared_inputs_hash
  -> append audit event atomically via Transactional Outbox
  -> execute projection only if reservation is valid
  -> persist terminal idempotency state atomically with audit event
```

Persistent idempotency prevents these failure modes:

```text
process restarts and loses in-memory dedup state
retry re-enters projection after a prior successful completion
same correlation_id is reused with changed inputs
projection failed but the failure evidence is removed or forgotten
IN_PROGRESS survives crash with unknown side-effect status
audit says projection completed but idempotency state has no completed record
idempotency says completed but audit chain lacks projection_completed
```

---

## 2. Scope

P0.6.32 covers persistent idempotency for **projection handoff only**.

In scope:

```text
idempotency key model
semantic relationship between correlation_id, prepared_inputs_hash, event_id, snapshot_hash
persistent idempotency record lifecycle
state model and terminal states
Poison Pill detection
restart recovery for stale IN_PROGRESS records
conditional update capability requirements
atomic linkage with Audit Transactional Outbox
store-unavailable policy
TTL and retention concepts
replay / forensic requirements
schema versioning and upcasting requirements
storage backend deferral
explicit non-goals
```

Out of scope:

```text
gate transition deduplication
provider call deduplication
general runtime wiring deduplication
diagnostic / OBSERVE event deduplication
storage implementation
SQL / NoSQL schema
Redis / PostgreSQL / SQLite / CAS / queue selection
production provider aggregator
production ENABLED
```

---

## 3. Verified project facts

P0.6.32 is grounded in the current code and accepted patch sequence.

### 3.1 P0.6.24 executable idempotency contract

P0.6.24 introduced the executable harness contract for projection idempotency:

```text
same inputs + same correlation_id -> same snapshot_hash
same correlation_id + changed inputs -> Poison Pill / contract violation
same inputs + different correlation_id -> independent audit trail
```

The harness established the distinction between:

```text
Functional idempotency
  same deterministic projection input produces same projection artifact hash

Operational idempotency
  repeated logical operation must not duplicate side effects
```

### 3.2 P0.6.26 in-memory handoff dedup

P0.6.26 introduced `AS2ProjectionHandoffSkeleton` as the only approved
production-namespace caller of `project_validated_as2_inputs(...)` and added an
in-memory dedup index for projection handoff. That index is intentionally not a
persistent store.

### 3.3 P0.6.27 dedup hardening

P0.6.27 hardened the in-memory dedup behavior:

```text
_dedup_lock protects the in-memory dedup index
two-phase IN_PROGRESS reservation prevents concurrent double projection
write-after-success ensures completed dedup entries are written only after projection success
projection failures roll back transient reservation state
DUPLICATE results are hash-only and do not retain AgentSnapshot objects
```

### 3.4 P0.6.31 audit outbox design

P0.6.31 defined Audit Persistence / Transactional Outbox as the preferred
production model:

```text
state transition + audit event append must be atomic
OutboxAuditSink writes a local outbox record
AuditRelay exports events asynchronously
record_hash uses deterministic payload fields only
Event Envelope metadata does not participate in hash computation
CHAIN_START defines first-record hash-chain semantics
```

Persistent idempotency must use that audit outbox discipline rather than invent
a separate persistence timeline.

---

## 4. Deduplication scope

Persistent idempotency applies only to projection handoff, because projection is
the AS2 boundary that creates canonical projected artifacts and can have
side-effect significance for downstream consumers.

Persistent idempotency does **not** cover:

```text
Provider calls
  Provider failures are value outcomes routed to Control Plane.

Gate transitions
  Gate state is controlled by AS2GateController and audit chain semantics.

Diagnostic events
  OBSERVE / telemetry events follow audit failure policy, not projection dedup.

General runtime wiring
  Runtime wiring delegates to handoff under explicit flag and does not own projection idempotency.
```

---

## 5. Semantic identifiers

The following identifiers must remain distinct.

| Identifier | Meaning |
| --- | --- |
| `correlation_id` | Groups a logical request, trace, or operation. |
| `prepared_inputs_hash` | Stable hash of exact `PreparedAS2Inputs` used for projection. |
| `idempotency_key` | Composite key for one logical projection attempt: `correlation_id + prepared_inputs_hash`. |
| `event_id` | Unique identity of one audit event. Used for audit relay idempotency. |
| `snapshot_hash` | Identity of the produced `AgentSnapshot` artifact. |
| `derivation_record_hash` | Identity of the produced derivation record artifact. |

The canonical idempotency key is:

```text
idempotency_key = correlation_id + prepared_inputs_hash
```

The RFC intentionally does not prescribe the serialized key format. Future
implementations may encode it as a tuple, canonical JSON object, delimited
string, or stable hash of a typed key payload. The semantics are fixed; the
physical representation is deferred.

---

## 6. Prepared inputs hash

`prepared_inputs_hash` must represent the exact projection payload identity.

Required properties:

```text
deterministic
stable across process restart
independent of wall-clock time
independent of object identity / memory address
computed before projection core is called
used for Poison Pill detection
recorded in idempotency record or derivable from it
```

Open implementation detail:

```text
The exact canonicalization profile for PreparedAS2Inputs hashing is deferred.
```

Constraint:

```text
Any future implementation must ensure the hash is based on deterministic domain payload fields only.
```

---

## 7. Persistent idempotency record model

The RFC-level record shape is conceptual only. No database schema is defined.

Minimum logical fields:

```text
schema_version
idempotency_key
correlation_id
prepared_inputs_hash
state
reason_code
snapshot_hash
derivation_record_hash
created_at_envelope_time
updated_at_envelope_time
expires_at / ttl_metadata
operator_resolution_ref
last_audit_event_id
last_audit_record_hash
retry_policy_marker
```

Hash-sensitive payload fields must not depend on mutable transport metadata.
Envelope metadata such as ingestion time, storage offset, relay attempt count,
or backend-specific version fields must not change the domain meaning of the
record.

---

## 8. State model

Recommended state set:

```text
IN_PROGRESS
COMPLETED
FAILED
CANCELLED
STALE_IN_PROGRESS
```

### 8.1 IN_PROGRESS

```text
The idempotency key is reserved.
Projection has been approved to start or has started.
Terminal result is not yet known.
```

`IN_PROGRESS` must be created atomically with the corresponding
`projection_started` audit event.

### 8.2 COMPLETED

```text
Projection completed successfully.
snapshot_hash and derivation_record_hash are persisted.
Duplicate requests return hash-only duplicate semantics.
```

`COMPLETED` must be written atomically with `projection_completed`.

### 8.3 FAILED

```text
Projection failed or was rejected due to systemic/core/contract reason.
The failure is persisted and audit-visible.
The record is not deleted or rolled back.
```

`FAILED` records may be eligible for explicit operator-controlled retry policy,
depending on reason code. That retry policy is not implemented or selected in
P0.6.32.

### 8.4 CANCELLED

```text
The operation was cancelled by caller/deadline/host policy.
No successful projection artifact is recorded.
The cancellation is persisted for forensic visibility.
```

`CANCELLED` is not a systemic failure by itself.

### 8.5 STALE_IN_PROGRESS

```text
An IN_PROGRESS record exceeded its allowed age / TTL.
The system cannot determine whether the previous execution produced side effects.
Automatic retry is forbidden.
Operator review is required.
```

`STALE_IN_PROGRESS` must be paired with an operator-review-required audit event.

---

## 9. Poison Pill modeling

A Poison Pill occurs when the same `correlation_id` is reused with a different
`prepared_inputs_hash`.

Canonical rule:

```text
same correlation_id + different prepared_inputs_hash -> Poison Pill
```

Required behavior:

```text
fail-closed
no projection core call
persistent terminal failure evidence
audit event appended atomically
operator review / Dead Letter Channel candidate
all future retries with that correlation_id rejected until explicit resolution policy exists
```

### 9.1 Recommended representation

Recommended model:

```text
FAILED(reason_code=POISON_PILL)
```

Rationale:

```text
Poison Pill changes retry policy and routing, but it does not require a separate state.
Using FAILED + reason_code avoids unnecessary state-machine growth.
Operator tooling can still distinguish Poison Pill through reason_code.
Audit and Dead Letter routing can key off reason_code.
```

### 9.2 Alternative considered

Alternative model:

```text
FAILED_POISON_PILL
```

This was considered because Poison Pill is terminal and must never be retried.
However, it adds state-machine complexity without adding behavior that cannot be
expressed by `FAILED(reason_code=POISON_PILL)`.

Implementation choice is deferred, but this RFC recommends reason-code based
modeling.

---

## 10. Conditional update capability

Any future persistent backend must support atomic conditional state transitions.
The RFC specifies required capability semantics, not a concrete interface and
not a storage-specific mechanism.

Required capabilities:

```text
reserve_if_absent(key, input_hash)
complete_if_state(key, expected_state=IN_PROGRESS)
fail_if_state(key, expected_state=IN_PROGRESS)
cancel_if_state(key, expected_state=IN_PROGRESS)
mark_stale_if_expired(key, expected_state=IN_PROGRESS, age_gt=TTL)
reject_if_poison(correlation_id)
```

Equivalent conditional update semantics:

```text
SET state = COMPLETED
WHERE key = <idempotency_key>
  AND state = IN_PROGRESS
```

Reason:

```text
Without conditional update, concurrent runtime instances can both observe a valid state
and both proceed to projection, causing duplicate side effects.
```

Implementation mechanisms are explicitly deferred:

```text
PostgreSQL transactions / unique constraints / SELECT FOR UPDATE
Redis SETNX / Lua scripts
CAS compare-and-swap
SQLite transaction locks
Kafka compacted topic semantics
NATS KV conditional writes
```

---

## 11. Atomic linkage with Transactional Outbox

Persistent idempotency state transitions and audit outbox appends must be part
of one local atomic unit for state-changing idempotency transitions.

Required atomic pairs:

```text
IN_PROGRESS reservation + projection_started audit event
COMPLETED update + projection_completed audit event
FAILED update + projection_failed audit event
FAILED(reason_code=POISON_PILL) + poison_pill/systemic_failure audit event
CANCELLED update + projection_cancelled audit event
STALE_IN_PROGRESS update + operator_review_required audit event
```

Forbidden outcomes:

```text
idempotency says COMPLETED but audit lacks projection_completed
audit says projection_completed but idempotency has no COMPLETED record
Poison Pill detected but no durable audit evidence exists
IN_PROGRESS reservation exists without projection_started audit evidence
```

This extends the P0.6.31 Transactional Outbox discipline to the projection
idempotency lifecycle.

---

## 12. Restart recovery

Persistent restart recovery must handle `IN_PROGRESS` records explicitly.

Rule:

```text
IN_PROGRESS + age > configured TTL -> STALE_IN_PROGRESS
```

`STALE_IN_PROGRESS` behavior:

```text
no automatic retry
no projection core call
operator review required
audit event required
explicit operator resolution required before further action
```

Operator resolution options may include:

```text
mark FAILED
mark CANCELLED
authorize retry under a new correlation_id
authorize recovery after external artifact inspection
```

Those workflows are future operator/control-plane design topics. P0.6.32 only
requires that automatic retry is forbidden.

---

## 13. Store unavailable policy

If the persistent idempotency store is unavailable:

```text
projection must fail-closed
```

The system must not execute projection without durable idempotency. Otherwise
retries and restarts can violate projection idempotency and forensic guarantees.

Diagnostic or OBSERVE events may continue under best-effort audit policy, but
state-changing projection handoff must be blocked.

---

## 14. TTL and retention policy

P0.6.32 requires TTL / retention concepts but does not choose concrete values.

Required policy concepts:

```text
IN_PROGRESS TTL
  covers maximum expected projection duration before stale detection

COMPLETED retention
  covers dedup replay window and forensic replay window

FAILED retention
  preserves failure evidence and blocks blind retry

FAILED(reason_code=POISON_PILL) retention
  preserves toxic correlation evidence and prevents automatic retry

CANCELLED retention
  preserves operational termination evidence

STALE_IN_PROGRESS retention
  preserves unresolved crash-recovery evidence until operator resolution
```

Concrete durations are deferred.

---

## 15. Replay and forensic requirements

Persistent idempotency records must be reconcilable with the audit chain defined
in P0.6.31.

RFC-level future API concepts:

```text
verify_idempotency_record(correlation_id)
detect_stale_records(threshold)
inspect_poison_pill(correlation_id)
reconcile_with_audit_chain(correlation_id)
list_records_by_state(state)
```

Verification expectations:

```text
COMPLETED record has matching projection_completed audit event
FAILED record has matching projection_failed / systemic failure audit event
CANCELLED record has matching projection_cancelled audit event
STALE_IN_PROGRESS record has matching operator_review_required audit event
Poison Pill record has matching poison/systemic audit event
snapshot_hash in idempotency record matches audit payload snapshot_hash
derivation_record_hash in idempotency record matches audit payload derivation_record_hash
```

No replay API implementation is added in P0.6.32.

---

## 16. Schema versioning and upcasting

Persistent idempotency records must carry `schema_version` or an equivalent
version marker.

Replay and forensic tooling must be schema-version aware.

Future replay verifier requirement:

```text
old records with schema_version=v1 are upcast to the current canonical structure
missing fields are filled with documented defaults
unsupported schema versions return typed verification failure
replay_transitions / reconciliation must not crash on archived records
```

This extends the P0.6.31 audit schema-versioning discipline to idempotency
records.

---

## 17. Failure categories

The RFC-level reason taxonomy must at least support:

```text
PROJECTION_COMPLETED
PROJECTION_FAILED
PROJECTION_CANCELLED
POISON_PILL
STALE_IN_PROGRESS
STORE_UNAVAILABLE
CONDITIONAL_UPDATE_CONFLICT
AUDIT_OUTBOX_COMMIT_FAILED
OPERATOR_RESOLUTION_REQUIRED
```

Exact enum names and storage representation are deferred to implementation.

---

## 18. Storage backend deferred

P0.6.32 does not choose a backend.

Explicitly deferred options include:

```text
PostgreSQL
SQLite
Redis
NATS KV
Kafka compacted topic
S3 / object store
CAS
filesystem
```

The RFC defines required semantics, not vendor selection.

---

## 19. Interaction with Production Provider Aggregator

P0.6.32 does not design the production provider aggregator.

Future P0.6.33 topics remain:

```text
provider output normalization
Failure Priority Matrix for concurrent aggregation
agent-scoped INVALID_INPUT refinement
production provider assembly contract
```

Persistent idempotency consumes `PreparedAS2Inputs` after provider aggregation
and AS2 preparation have already succeeded. It does not normalize provider
outputs.

---

## 20. Production ENABLED impact

Production `ENABLED` remains locked.

Persistent idempotency is a prerequisite for readiness, but this RFC alone does
not authorize production activation.

Current roadmap:

```text
P0.6.31 — Audit Persistence / Transactional Outbox RFC ✅
P0.6.32 — Persistent Idempotency Store RFC
P0.6.33 — Production Provider Aggregator RFC
P0.6.34 — Production ENABLED readiness vote
```

If implementation skeletons for audit/idempotency are required before readiness,
the roadmap may expand before a production vote.

---

## 21. Explicit non-goals

P0.6.32 does not include:

```text
production code changes
synapse/ implementation changes
tests/ implementation changes
real idempotency storage
SQL schema
Redis/PostgreSQL/SQLite/CAS implementation
file / DB / CAS writes
queue / KV implementation
AS2ProjectionHandoff behavior changes
as2_runtime_wiring.py changes
production provider aggregator
provider output normalization implementation
concrete provider adapters
production ENABLED
```

---

## 22. Locked items

The following remain locked after P0.6.32:

```text
persistent idempotency implementation
audit storage implementation
OutboxAuditSink implementation
AuditRelay implementation
runtime behavior changes
as2_projection_handoff.py changes
as2_runtime_wiring.py changes
production provider aggregator
provider shape normalization implementation
concrete provider adapters
real storage backend
production ENABLED
```

---

## 23. Acceptance criteria

P0.6.32 is complete when:

```text
1. docs/AS2-PERSISTENT-IDEMPOTENCY-STORE-RFC-P0632.md exists.
2. RFC states projection handoff is the only idempotency scope.
3. RFC defines idempotency_key = correlation_id + prepared_inputs_hash.
4. RFC distinguishes correlation_id, event_id, prepared_inputs_hash, snapshot_hash, derivation_record_hash.
5. RFC defines IN_PROGRESS / COMPLETED / FAILED / CANCELLED / STALE_IN_PROGRESS states.
6. RFC recommends FAILED(reason_code=POISON_PILL) over separate FAILED_POISON_PILL.
7. RFC requires Poison Pill fail-closed behavior.
8. RFC requires conditional update capability for future backends.
9. RFC requires atomic linkage with Transactional Outbox.
10. RFC defines restart recovery via STALE_IN_PROGRESS + operator review.
11. RFC defines store unavailable -> fail-closed for projection.
12. RFC includes TTL / retention concepts without concrete values.
13. RFC includes replay / forensic reconciliation requirements.
14. RFC includes schema versioning and upcasting requirements.
15. RFC defers storage backend selection.
16. RFC explicitly locks implementation, storage, runtime behavior changes, production aggregator, adapters, and production ENABLED.
17. docs/CHANGELOG.md records P0.6.32 as doc-only.
```

