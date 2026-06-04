# AS2 Audit Persistence / Transactional Outbox RFC — P0.6.31

Status: **proposed / accepted for doc-only planning**  
Patch type: **doc-only RFC**  
Runtime implementation: **LOCKED**

P0.6.31 defines the persistence and delivery design for AS2 audit events. It
builds on the production-facing `AS2AuditSink` and `AS2AuditEvent` skeletons
introduced in P0.6.25, the projection handoff audit lifecycle introduced in
P0.6.26, the runtime wiring expansion in P0.6.27, and the completed six-port
Host Provider test boundary in P0.6.28-P0.6.30.

This RFC does **not** implement audit storage, does **not** add an
`OutboxAuditSink`, does **not** add an `AuditRelay`, and does **not** select a
storage backend. It defines the production persistence contract that must be
accepted before any future audit persistence implementation.

---

## 1. Purpose

AS2 now emits structured audit events from the Control Plane and Projection
Handoff skeletons, but those events are still handled through in-memory or no-op
sinks. Production AS2 cannot rely on no-op audit handling because gate
transitions, systemic disables, quarantine requests, operator overrides, and
successful projections must be forensically reconstructable after process
restart, host failure, or operator review.

P0.6.31 establishes the audit persistence target model:

```text
AS2AuditEvent generation
  -> AS2AuditSink port
  -> local transactional outbox append
  -> asynchronous AuditRelay export
  -> external durable audit store
  -> replay / forensic verification
```

The design goal is to prevent both forms of dual-write inconsistency:

```text
state changed, audit event lost
audit event emitted, state change failed
```

P0.6.31 also defines deterministic hash-chain rules so persisted audit records
can be verified without depending on wall-clock time or backend-specific storage
metadata.

---

## 2. Verified project facts

P0.6.31 is grounded in the current project code and accepted RFC sequence.

### 2.1 Verified from executable scope

The current executable AS2 scope establishes:

```text
AS2AuditSink exists as a production-facing Protocol
AS2AuditEvent exists as the structured event object
NoOpAuditSink is the current default implementation
AS2AuditEvent.record_hash() uses deterministic fields only
timestamp is explicit metadata and is excluded from record_hash()
previous_state_hash is present on audit events
snapshot_hash and derivation_record_hash are present for projection audit events
Control Plane and Projection Handoff emit through AS2AuditSink
```

### 2.2 Verified from recent patch sequence

The accepted P0.6.25-P0.6.30 sequence establishes:

```text
P0.6.25 — production AS2GateController skeleton + AS2AuditSink
P0.6.26 — approved Projection Handoff skeleton + projection audit events
P0.6.27 — runtime wiring delegates to handoff under explicit flag
P0.6.28 — all six Host Provider Port interfaces materialized
P0.6.29 — Stage 2 provider fakes + safe ProviderReason routing
P0.6.30 — Stage 3 provider fakes + full six-port test-only aggregation
```

P0.6.30 also surfaced a future provider-shape normalization concern: some
provider fake output shapes may include metadata that is not accepted by the
current bridge-valid Host Pre-Stage payload shape. That is not resolved in
P0.6.31; it is recorded as future Production Provider Aggregator work.

### 2.3 Consequence for P0.6.31

P0.6.31 must specify persistence and replay contracts, not implementation. The
RFC must preserve the existing port boundary while defining how future adapters
will durably record and relay audit events.

---

## 3. Scope

P0.6.31 defines:

```text
AS2AuditSink role as production audit port
Audit pipeline components
Event Payload vs Event Envelope separation
Transactional Outbox as preferred production model
Relay idempotency by event_id
previous_state_hash chain requirements
first-record / CHAIN_START semantics
deterministic record_hash discipline
timestamp metadata rules
AS2AuditEvent schema versioning
policy-based audit failure behavior
replay and forensic API contract
storage backend deferral
explicit non-goals and locked implementation scope
future refinements for provider aggregation and INVALID_INPUT semantics
```

The RFC is documentation-only.

---

## 4. Non-goals and locked scope

P0.6.31 does **not** introduce:

```text
production code changes
synapse/ implementation changes
new AS2AuditSink implementations
OutboxAuditSink implementation
AuditRelay implementation
external audit store implementation
database schema
file/database/CAS writes
Kafka / NATS / queue / broker design
persistent idempotency store
production provider aggregator
provider output normalization implementation
concrete provider adapters
as2_runtime_wiring.py changes
runtime behavior changes
production ENABLED state
```

All storage, relay, and backend choices remain locked until a later implementation
or infrastructure RFC explicitly opens them.

---

## 5. AS2AuditSink as production port

`AS2AuditSink` remains the production-facing outbound audit port. AS2 runtime
components emit structured audit events through the port and must not know the
storage backend.

Current and future implementation hierarchy:

```text
AS2AuditSink
  NoOpAuditSink
    current skeleton default; accepts events and performs no I/O

  InMemoryAuditSink
    tests / harness only; captures events in memory

  OutboxAuditSink
    future production adapter; appends events to local transactional outbox

  RelayAuditSink / AuditRelay integration
    future export mechanism; relays already-persisted outbox entries
```

Only the first two categories are currently allowed by existing skeleton/test
scope. `OutboxAuditSink` and relay/export components are future work and are not
implemented by this RFC.

---

## 6. Audit pipeline components

The audit persistence design separates three components with distinct
responsibilities and failure semantics.

### 6.1 OutboxAuditSink

Future `OutboxAuditSink` is an adapter behind the `AS2AuditSink` port.
Its responsibility is local durable append of audit events to a transactional
outbox.

Required semantics:

```text
receives AS2AuditEvent payload from AS2 runtime
writes payload into local outbox
commits audit event atomically with the state-changing operation that caused it
performs no external relay work itself
```

`OutboxAuditSink` is the local atomic write boundary. It is not an external
message broker, not a daemon, and not an external audit store.

### 6.2 AuditRelay

Future `AuditRelay` is a separate asynchronous component that reads committed
outbox entries and exports them to an external durable audit store.

Required semantics:

```text
reads local outbox entries
sends entries to external store / export target
tracks delivery attempts
supports retry
provides at-least-once delivery
uses event_id for idempotent delivery
```

`AuditRelay` is not responsible for deciding whether a gate transition is
allowed. It only relays already-committed audit records.

### 6.3 External Store

The external durable audit store is deferred. P0.6.31 does not select a backend
or define a physical schema.

Possible future backends include, but are not limited to:

```text
PostgreSQL
SQLite
Kafka
NATS
S3 / object storage
Redis
CAS
filesystem
```

No backend is approved by this RFC.

---

## 7. Event Payload vs Event Envelope

Persisted audit records must distinguish deterministic AS2 event payload from
transport/storage envelope metadata.

### 7.1 Event Payload

Event Payload is the deterministic domain event. It participates in
`record_hash()` and is replay / forensic visible.

Payload fields include:

```text
schema_version
event_id
event_type
correlation_id
request_id
agent_id
reason_code
from_state
to_state
previous_state_hash
snapshot_hash
derivation_record_hash
detail
```

Payload must be serialized with stable canonical ordering before hashing.

### 7.2 Event Envelope

Event Envelope is transport or storage metadata. It does **not** participate in
`record_hash()`.

Envelope fields may include:

```text
wall_clock_timestamp
sequence_number
partition_key
storage_offset
relay_attempt_count
ingestion_node_id
backend_metadata
```

Envelope metadata may be backend-specific and may vary across deployments. It is
not part of deterministic replay verification.

### 7.3 Hash boundary

The hash boundary is strict:

```text
record_hash = stable_canonical_hash(Event Payload only)
```

No envelope field may influence `record_hash()`.

---

## 8. Transactional Outbox model

Transactional Outbox is the preferred production architecture for AS2 audit
persistence.

Required production invariant:

```text
state transition AND audit event persistence must be committed atomically
```

The same rule applies to projection completion when downstream consumption of
the projected result depends on an audit record.

### 8.1 Atomicity requirement

The following states are invalid:

```text
gate state changed but no audit event was durably recorded
projection result consumed downstream but projection_completed was not durably recorded
audit event was externally emitted for a state change that failed to commit
```

### 8.2 Relay semantics

The relay/export side may be asynchronous:

```text
local outbox append is part of the state-changing transaction
external export may occur later
external export may be retried
external export is at-least-once
external consumers must be idempotent by event_id
```

Exactly-once external delivery is not required by this RFC.

---

## 9. Relay idempotency by event_id

Persisted audit events must have a stable `event_id` that identifies one logical
audit event.

Required semantics:

```text
event_id is stable for one logical audit event
AuditRelay retries use the same event_id
external stores must tolerate repeated delivery of the same event_id
semantic duplicates must be rejected or collapsed by event_id
```

`event_id` is distinct from `correlation_id`:

```text
correlation_id groups a request / trace / operation
event_id identifies a single audit event within that group
```

A single correlation chain may contain many event IDs.

---

## 10. Hash chain discipline

Persisted audit records form an append-only hash chain.

### 10.1 previous_state_hash rule

For every non-first persisted record in a chain:

```text
current.previous_state_hash == previous.record_hash()
```

A mismatch indicates a broken chain and must be reported as a forensic alert.

### 10.2 First-record semantics

The first record in a persisted chain has no previous record. P0.6.31 requires an
explicit sentinel so a verifier can distinguish a valid chain start from a
missing previous record.

Required rule:

```text
For the first persisted record in a chain:
  previous_state_hash = "CHAIN_START"
```

A null or missing `previous_state_hash` in persisted records is invalid unless a
future migration policy explicitly defines a legacy exception.

Replay verifiers must distinguish:

```text
CHAIN_START            valid first record
BROKEN_PREVIOUS_HASH   previous_state_hash does not match prior record
MISSING_PREVIOUS       non-first record lacks previous_state_hash
```

### 10.3 record_hash discipline

`record_hash()` must be computed from deterministic payload fields only.

Required rule:

```text
record_hash = stable_canonical_hash(payload_only)
```

Excluded from hash payload:

```text
wall_clock_timestamp
sequence_number
partition_key
storage_offset
relay_attempt_count
ingestion_node_id
backend_metadata
```

### 10.4 Timestamp rule

Timestamp is allowed only as explicit metadata. It must not be generated inside
`record_hash()` construction and must not affect deterministic replay.

---

## 11. AS2AuditEvent schema versioning

Persisted audit records must support backward-compatible schema evolution.

Required semantics:

```text
persisted payload carries schema_version or equivalent version marker
replay verifier is schema-version aware
new fields should be additive where possible
field removal or rename requires migration policy
old records remain readable after schema evolution
unsupported schema version returns a typed verification failure
```

Schema version participates in payload semantics and may be included in the
hashable payload once introduced. Envelope versioning, if needed, is separate
from event payload versioning.

---

## 12. Audit failure policy

Audit persistence failures must be handled according to event criticality.

### 12.1 Critical state-changing events

Critical events include:

```text
gate transition
systemic disable
agent quarantine
operator override / reset
projection_completed
provider failure that triggers systemic disable
```

Required policy:

```text
Fail-Closed
```

A state-changing operation must not complete if its required audit record cannot
be durably recorded.

Canonical rule:

```text
No durable audit for state-changing event -> no state-changing operation.
```

### 12.2 Diagnostic / observational events

Diagnostic events include:

```text
OBSERVE
retry observed
timeout observed without state transition
WiringBridgeDisabled diagnostic/config event
telemetry-like event
```

Required policy:

```text
Best-Effort / Fail-Open
```

These events may be logged locally, counted as dropped diagnostics, or retried
later, but they must not necessarily block runtime execution.

### 12.3 Policy table

```text
Event category                         Audit failure policy
-----------------------------------------------------------
Gate transition                         Fail-Closed
Systemic disable                        Fail-Closed
Agent quarantine                        Fail-Closed
Operator override / reset               Fail-Closed
Projection completed                    Fail-Closed before downstream use
Provider failure -> systemic disable    Fail-Closed
OBSERVE                                 Best-Effort
WiringBridgeDisabled diagnostic         Best-Effort
Telemetry / diagnostic counters         Best-Effort
```

---

## 13. Replay and forensic API contract

P0.6.31 defines replay semantics at RFC level only. No implementation is added.

Future replay interface may expose:

```python
read_chain(correlation_id) -> list[AS2AuditEvent]
verify_chain(events) -> ChainVerificationResult
detect_gaps(events) -> list[GapDescriptor]
replay_transitions(events) -> list[AS2WiringGateState]
verify_event_id_uniqueness(events) -> bool
```

### 13.1 Verification responsibilities

Replay verification must detect:

```text
BROKEN_PREVIOUS_HASH
MISSING_PREVIOUS
MISSING_RECORD
REORDERED_RECORD
DUPLICATE_EVENT_ID
UNSUPPORTED_SCHEMA_VERSION
HASH_PAYLOAD_MISMATCH
```

### 13.2 Replay responsibilities

Replay must reconstruct:

```text
gate transition history
systemic disable events
agent quarantine events
operator reset / override events
projection lifecycle events
provider-failure escalations
```

Replay must not depend on envelope metadata for deterministic correctness.

---

## 14. Provider input-boundary future refinements

P0.6.30 completed the test-only six-provider input boundary. P0.6.31 does not
change provider behavior, but it records future work that must be addressed
before production provider activation.

### 14.1 Provider shape normalization layer

Known design debt:

```text
Provider output shape may differ from bridge-valid Host Pre-Stage payload shape.
```

Observed example from P0.6.30 integration hardening:

```text
FakeModelSelectionProvider emits:
  {"model": ..., "selection_source": ...}

Bridge-valid payload accepts:
  {"model": ...}
```

Future production provider aggregator must normalize and validate provider
outputs before passing payload to `prepare_as2_inputs_from_host_prestage(...)`.

Target:

```text
P0.6.33 — Production Provider Aggregator RFC
```

### 14.2 Failure Priority Matrix for concurrent aggregation

The current test-only provider harness is sequential and fail-fast. A future
production aggregator may poll independent providers concurrently, but then it
must define deterministic failure priority.

Future topic:

```text
If multiple provider failures arrive concurrently, the aggregator must choose a
single deterministic failure to route first.
```

Illustrative priority order for future discussion:

```text
MISSING_REQUEST_CONTEXT / UNAUTHORIZED / FORBIDDEN
  > SCHEMA_MISMATCH / INVALID_INPUT
  > NOT_FOUND
  > TIMEOUT / UNAVAILABLE
  > BACKPRESSURE_REJECTED
  > CANCELLED
```

Target:

```text
P0.6.33 — Production Provider Aggregator RFC
```

### 14.3 INVALID_INPUT with agent_id

Current P0.6.30 behavior:

```text
INVALID_INPUT -> SYSTEMIC_DISABLE
```

Future refinement:

```text
INVALID_INPUT + agent_id + agent-scoped evidence
  -> candidate for AGENT_QUARANTINE
```

This requires production aggregator context and is not decided in P0.6.31.

Target:

```text
P0.6.33 — Production Provider Aggregator RFC
```

---

## 15. Storage backend deferred

P0.6.31 does not select, configure, or prefer a concrete storage backend.

Deferred backends include:

```text
PostgreSQL
SQLite
Kafka
NATS
S3 / object storage
Redis
CAS
filesystem
```

A future implementation RFC must evaluate backend-specific tradeoffs, including
ordering, atomicity, retention, replay performance, schema evolution, migration,
security, and operational ownership.

---

## 16. Explicit non-goals

P0.6.31 explicitly does not define or implement:

```text
OutboxAuditSink implementation
AuditRelay implementation
external store implementation
database schema
Kafka / queue / broker topic design
file / database / CAS writes
persistent idempotency store
production provider aggregator
provider shape normalization implementation
concrete provider adapters
production ENABLED readiness
runtime behavior changes
as2_runtime_wiring.py changes
```

---

## 17. Roadmap impact

P0.6.31 establishes the audit persistence design layer before persistent
idempotency and production provider aggregation.

Recommended sequence:

```text
P0.6.31 — Audit Persistence / Transactional Outbox RFC, doc-only
P0.6.32 — Persistent Idempotency Store RFC, doc-only
P0.6.33 — Production Provider Aggregator RFC
           - provider output normalization
           - failure priority matrix
           - INVALID_INPUT agent-scoped refinement
P0.6.34 — Production ENABLED readiness vote
```

Production `ENABLED` remains locked until audit persistence, persistent
idempotency, and production provider aggregation decisions are accepted.
