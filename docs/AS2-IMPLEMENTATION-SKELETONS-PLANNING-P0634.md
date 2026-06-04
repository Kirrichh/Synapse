# AS2 Implementation Skeletons Planning / Gap Analysis — P0.6.34

Status: **proposed / accepted for doc-only planning**  
Patch type: **doc-only planning / gap analysis**  
Runtime implementation: **LOCKED**

P0.6.34 bridges the accepted design RFCs into a dependency-ordered implementation plan. It does not add production code, test code, storage, adapters, provider namespaces, or runtime behavior changes.

The planning scope is driven by three accepted design documents:

```text
P0.6.31 — AS2 Audit Persistence / Transactional Outbox RFC
P0.6.32 — AS2 Persistent Idempotency Store RFC
P0.6.33 — AS2 Production Provider Aggregator RFC
```

P0.6.34 defines how these designs must be materialized as skeletons, what must be proven before a readiness vote, and which architectural boundaries must be protected before code is written.

---

## 1. Purpose

The project now has a complete design surface for the next production-facing AS2 layer:

```text
Audit Persistence      -> durable event chain and transactional outbox
Persistent Idempotency -> restart-safe projection deduplication
Provider Aggregator    -> provider output normalization and bridge-valid payload assembly
```

The purpose of P0.6.34 is to convert those designs into an implementation dependency map:

```text
Design RFCs
  -> dependency-ordered skeleton plan
  -> acceptance gates
  -> boundary guard design
  -> readiness checklist
  -> integration harness target
  -> Production ENABLED readiness vote criteria
```

P0.6.34 is the final planning step before implementation skeletons. It ensures the skeletons are implemented in an order that preserves atomicity, replay-safety, deterministic hashing, and boundary isolation.

---

## 2. Scope

In scope:

```text
implementation sequence P0.6.35-P0.6.39
dependency graph across OutboxAuditSink, IdempotencyStore, and ProviderAggregator
pre-conditions for deterministic prepared_inputs_hash
future ProviderName enum requirement for deterministic tie-breaking
required backend capabilities without vendor selection
boundary guard matrix for future skeleton modules
acceptance gates for each skeleton
testing strategy per skeleton
observability requirements
rollback strategy
readiness checklist reference
future RFC backlog reference
explicit locked list
```

Out of scope:

```text
synapse/ code changes
tests/ code changes
OutboxAuditSink implementation
PersistentIdempotencyStore implementation
ProductionProviderAggregator implementation
backend vendor selection
real network / file / DB / CAS / queue I/O
production provider adapters
production ENABLED activation
changes to existing AS2 core behavior
```

---

## 3. Verified Project Facts

### 3.1 P0.6.31 — Audit Persistence / Transactional Outbox RFC

P0.6.31 established:

```text
AS2AuditSink remains the production-facing audit port.
Event Payload and Event Envelope are separate.
Only deterministic Event Payload fields participate in record_hash.
CHAIN_START is the explicit first-record marker.
Transactional Outbox is the preferred production model.
Critical audit failures fail closed; diagnostic events are best-effort.
Replay / forensic APIs are defined at contract level.
Storage backend remains deferred.
```

### 3.2 P0.6.32 — Persistent Idempotency Store RFC

P0.6.32 established:

```text
Idempotency applies to projection handoff only.
idempotency_key = correlation_id + prepared_inputs_hash.
Poison Pill = same correlation_id + different prepared_inputs_hash.
Poison Pill is represented as FAILED(reason_code=POISON_PILL), not a separate state.
Idempotency records require conditional update capability.
Idempotency state changes and audit outbox appends must be atomically linked.
STALE_IN_PROGRESS requires operator review; automatic retry is forbidden.
Store unavailable means fail-closed for projection.
Storage backend remains deferred.
```

### 3.3 P0.6.33 — Production Provider Aggregator RFC

P0.6.33 established:

```text
Provider Aggregator is an Anti-Corruption Layer.
Provider-native outputs must be normalized into bridge-valid Host Pre-Stage payload.
Failure Priority Matrix is required for deterministic concurrent aggregation.
Equal-priority failures require a deterministic tie-breaker.
prepared_inputs_hash = stable_canonical_hash(prepared_inputs.to_validate_kwargs()).
Idempotency Store is not a cache.
Aggregator has no direct coupling to the idempotency store.
Concrete provider adapters remain locked.
```

---

## 4. Implementation Sequence

The implementation skeleton sequence is dependency-ordered:

```text
P0.6.35 — Audit Persistence / OutboxAuditSink Skeleton
P0.6.36 — Persistent IdempotencyStore Skeleton
P0.6.37 — Production ProviderAggregator Skeleton
P0.6.38 — Integration Harness under ENABLED_FOR_TEST
P0.6.39 — Production ENABLED Readiness Vote
```

This order is mandatory for the next implementation phase unless the team records a new explicit decision.

### 4.1 Why OutboxAuditSink first

Persistent idempotency requires atomic linkage to the audit outbox:

```text
idempotency transition + audit outbox append = one atomic unit
```

Therefore, `PersistentIdempotencyStore` cannot be safely materialized before the audit outbox skeleton exists.

### 4.2 Why ProviderAggregator after IdempotencyStore

The aggregator itself does not call the idempotency store. However, its output participates in the idempotency key through canonical `PreparedAS2Inputs`:

```text
ProviderAggregator output
  -> bridge-valid Host Pre-Stage payload
  -> PreparedAS2Inputs
  -> prepared_inputs_hash
  -> idempotency_key
```

Provider aggregation must be implemented after the idempotency skeleton design surface exists so integration can verify the full canonical input-to-projection-dedup path.

### 4.3 Why Integration Harness after all three skeletons

P0.6.38 is the first stage that should prove the full stack together:

```text
ProviderAggregator
  -> bridge-valid payload
  -> PreparedAS2Inputs
  -> PersistentIdempotencyStore(IN_PROGRESS)
  -> Projection Handoff
  -> OutboxAuditSink(projection_completed)
  -> PersistentIdempotencyStore(COMPLETED)
```

This remains under `ENABLED_FOR_TEST`, not production `ENABLED`.

---

## 5. Dependency Graph

```text
OutboxAuditSink Skeleton
  required by -> PersistentIdempotencyStore Skeleton

PersistentIdempotencyStore Skeleton
  required by -> projection handoff production hardening
  required by -> full integration harness

ProviderAggregator Skeleton
  required by -> production input boundary
  required by -> full integration harness

OutboxAuditSink + PersistentIdempotencyStore + ProviderAggregator
  required by -> P0.6.38 Integration Harness

P0.6.38 Integration Harness evidence
  required by -> P0.6.39 Production ENABLED Readiness Vote
```

---

## 6. Required Backend Capabilities, Not Vendor Selection

P0.6.34 does not select a backend.

Future storage backends must be evaluated against required capabilities:

```text
atomic conditional update / compare-and-set
local transaction or equivalent atomic unit for idempotency + audit outbox
append-only audit outbox semantics
schema versioning support
TTL / expiry support for IN_PROGRESS records
read-by-correlation_id
read-by-idempotency_key
stale-record scan capability
deterministic replay support
operator inspection capability
```

Explicitly deferred vendor choices:

```text
PostgreSQL
SQLite
Redis
Kafka
NATS
S3
CAS
filesystem
any cloud-specific queue or database
```

Skeleton implementations may use in-memory structures only. Production backend selection remains locked.

---

## 7. Deterministic PreparedAS2Inputs Pre-condition

P0.6.33 fixed the idempotency input rule:

```text
prepared_inputs_hash = stable_canonical_hash(prepared_inputs.to_validate_kwargs())
```

P0.6.34 adds the implementation pre-condition: `PreparedAS2Inputs.to_validate_kwargs()` must own canonical output formation.

Requirements:

```text
stable key set
sorted / deterministic mapping order
canonical Python-neutral scalar types
canonical nested dict/list structures
stable ordering for semantic lists
canonical sorting for maps where semantic order is absent
no wall-clock values
no object identity
no provider-native extra metadata
same semantic inputs produce same output shape
```

The storage layer must not normalize `PreparedAS2Inputs`. Canonical AS2 input representation belongs to the AS2 boundary layer, not to audit storage, idempotency storage, provider aggregation, or a future backend adapter.

This is a pre-condition for P0.6.36. If the current implementation does not satisfy this property, the issue must be corrected before the persistent idempotency skeleton can be accepted.

---

## 8. Canonical ProviderName Requirement

P0.6.33 requires lexicographic tie-breaking for equal-priority provider failures. Because provider names become part of deterministic runtime behavior, future implementation must not rely on raw strings.

P0.6.37 must introduce or use a canonical provider-name enum before the production aggregator skeleton is accepted.

Candidate shape:

```python
class AS2ProviderName(Enum):
    HOST_IDENTITY = "host_identity"
    MODEL_SELECTION = "model_selection"
    HOST_DEFINITION = "host_definition"
    STATIC_MODEL_REGISTRY = "static_model_registry"
    MEMORY_REFERENCE = "memory_reference"
    CAPABILITY_GRANT = "capability_grant"
```

The tie-breaker must operate on canonical enum values, not ad-hoc strings. This prevents opaque bugs caused by string spelling drift such as `memory_reference` vs. `memory_ref`.

---

## 9. Boundary Guard Matrix for Future Skeletons

Boundary guards must be designed before implementation. Each future skeleton gets module-specific restrictions.

### 9.1 OutboxAuditSink Skeleton

Candidate future module:

```text
synapse/runtime/as2_outbox_audit_sink.py
```

Forbidden:

```text
network imports
file I/O imports
DB driver imports
provider imports
project_validated_as2_inputs
AgentSnapshot construction or import
AgentRuntime / Environment imports
projection handoff calls
```

Allowed:

```text
AS2AuditSink
AS2AuditEvent
stable_canonical_hash
Event Payload / Event Envelope value types
in-memory append-only skeleton structures
```

### 9.2 PersistentIdempotencyStore Skeleton

Candidate future module:

```text
synapse/runtime/as2_persistent_idempotency_store.py
```

Forbidden:

```text
project_validated_as2_inputs
AgentSnapshot construction or import
provider imports
AgentRuntime / Environment imports
real storage drivers
provider aggregation calls
runtime wiring expansion
```

Allowed:

```text
idempotency key types
idempotency state model
conditional transition helpers
audit outbox interface surface
in-memory skeleton state
```

### 9.3 ProviderAggregator Skeleton

Candidate future module:

```text
synapse/runtime/as2_provider_aggregator.py
```

Forbidden:

```text
project_validated_as2_inputs
AgentSnapshot construction or import
idempotency store direct calls
audit storage writes
real network / file / DB / CAS I/O
AgentRuntime / Environment imports
projection handoff calls
```

Allowed:

```text
provider port interfaces
ProviderOutcome
ProviderReasonCode
canonical ProviderName enum
normalization logic
Failure Priority Matrix
bridge-valid payload construction
```

---

## 10. Acceptance Gates per Skeleton

### 10.1 P0.6.35 — OutboxAuditSink Skeleton

Acceptance gate:

```text
NoOpAuditSink behavior preserved
OutboxAuditSink is injectable, not globally enabled
no real I/O
in-memory append-only skeleton behavior only
Event Payload vs Event Envelope separation preserved
CHAIN_START semantics preserved
schema_version carried
append-only contract covered by tests
no provider imports
no projection calls
no AgentSnapshot construction
boundary guard updated and green
```

### 10.2 P0.6.36 — PersistentIdempotencyStore Skeleton

Acceptance gate:

```text
state model represented:
  IN_PROGRESS / COMPLETED / FAILED / CANCELLED / STALE_IN_PROGRESS
FAILED(reason_code=POISON_PILL) supported
conditional update semantics represented
STALE_IN_PROGRESS transition tested
store unavailable behavior modeled as fail-closed for projection
no AgentSnapshot caching
no projection calls
atomic linkage surface with OutboxAuditSink defined
prepared_inputs_hash uses canonical PreparedAS2Inputs output
boundary guard updated and green
```

### 10.3 P0.6.37 — ProductionProviderAggregator Skeleton

Acceptance gate:

```text
provider-native output normalization implemented
non-bridge fields stripped
required fields validated
bridge-valid Host Pre-Stage payload constructed
Failure Priority Matrix implemented
ProviderName enum tie-breaker implemented
sequential-first implementation accepted
concurrent model remains contract-compatible
no idempotency store calls
no projection calls
no AgentSnapshot construction
no real I/O
boundary guard updated and green
```

### 10.4 P0.6.38 — Integration Harness under ENABLED_FOR_TEST

Acceptance gate:

```text
ProviderAggregator -> PreparedAS2Inputs path tested
PersistentIdempotencyStore IN_PROGRESS reservation tested
Projection Handoff under ENABLED_FOR_TEST tested
OutboxAuditSink projection_completed append tested
PersistentIdempotencyStore COMPLETED transition tested
Poison Pill route tested
STALE_IN_PROGRESS route tested
failure priority matrix regression tested
rollback/default-off behavior tested
production ENABLED remains locked
```

---

## 11. Observability Requirements

Future skeletons must be designed for observability, even if the first skeleton implementation uses in-memory structures.

Required design expectations:

```text
RED metrics:
  Rate
  Errors
  Duration

structured logging fields:
  correlation_id
  request_id
  provider_name when applicable
  event_type when applicable
  idempotency_key when applicable
  state transition when applicable

trace context:
  explicit context propagation
  no fallback correlation_id generation
  OpenTelemetry-ready span boundaries
```

P0.6.34 does not implement observability. It records the requirement so skeletons do not need redesign later.

---

## 12. Testing Strategy per Skeleton

Future implementation patches must include tests from the relevant categories below.

```text
unit tests:
  local state transitions
  normalization functions
  hash and key helpers

integration tests:
  outbox + idempotency linkage
  aggregator + runtime wiring handoff
  end-to-end ENABLED_FOR_TEST path

contract tests:
  AS2AuditSink port behavior
  idempotency store transition behavior
  provider aggregator input/output contract

boundary fitness tests:
  forbidden imports
  forbidden calls
  module allowlists

fault-injection tests:
  store unavailable
  audit append failure
  stale IN_PROGRESS
  Poison Pill
  provider failure priority conflicts

restart recovery tests:
  STALE_IN_PROGRESS detection
  completed-after-stale conditional update rejection

concurrency tests:
  deterministic provider failure tie-breaker
  conditional update winner semantics
```

---

## 13. Rollback Strategy

All skeletons are additive and default-off. No future skeleton should change existing behavior by default.

Rollback model:

```text
OutboxAuditSink rollback:
  revert dependency injection to NoOpAuditSink

PersistentIdempotencyStore rollback:
  use existing in-memory projection handoff dedup in test-only scope

ProviderAggregator rollback:
  use existing ready Mapping / Host Pre-Stage payload path

Integration Harness rollback:
  disable ENABLED_FOR_TEST skeleton wiring and retain existing isolated tests
```

Feature and injection gates must remain explicit. Production `ENABLED` remains unavailable until readiness evidence is complete.

---

## 14. Relationship to Readiness Checklist

The separate checklist file is the acceptance-gate artifact:

```text
docs/AS2-SKELETONS-READINESS-CHECKLIST.md
```

Rule:

```text
Production ENABLED readiness vote is not valid until the implemented/evidence column is complete.
```

P0.6.34 planning defines the map. The readiness checklist tracks the evidence.

---

## 15. Relationship to Future RFC Backlog

The separate backlog file is the deferred-design artifact:

```text
docs/AS2-FUTURE-RFC-BACKLOG.md
```

P0.6.34 moves non-blocking future decisions out of immediate implementation scope so they are visible but do not block skeleton materialization.

---

## 16. Explicit Locked List

Locked in P0.6.34:

```text
synapse/ code changes
tests/ code changes
backend vendor selection
production ENABLED
real network / file / DB / CAS / queue I/O
concrete provider adapters
provider adapter namespace creation
audit storage implementation
idempotency store implementation
production aggregator implementation
operator RPC
degraded mode
CAS artifact storage
CVM / LLM execution expansion
changes to existing AS2 core behavior
AgentRuntime / Environment imports in AS2 skeletons
```

---

## 17. Decision

P0.6.34 opens as:

```text
Implementation Skeletons Planning / Gap Analysis, doc-only
```

It creates planning artifacts only. It does not implement the skeletons. The next implementation patch is P0.6.35: Audit Persistence / OutboxAuditSink Skeleton.
