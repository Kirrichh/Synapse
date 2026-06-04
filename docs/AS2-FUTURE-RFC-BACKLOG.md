# AS2 Future RFC Backlog

Status: **planning backlog**  
Introduced by: **P0.6.34**  
Purpose: **track deferred AS2 design topics without expanding current skeleton scope**

This backlog records important future design topics identified during P0.6.31-P0.6.34 discussions. Items in this file are visible engineering debt or future refinement topics. They are not active implementation scope until explicitly opened by a future patch mandate.

---

## 1. Backlog Policy

```text
Backlog item = recorded future design topic.
Backlog item does not authorize implementation.
Backlog item does not unlock production ENABLED.
Backlog item must be promoted to a patch mandate before code changes.
```

---

## 2. Byzantine Poison Pill Signal Detection

Source context: P0.6.32 / P0.6.33 discussions.

Problem:

```text
A single Poison Pill is a contract violation for one correlation_id.
Many Poison Pills from the same security_context.subject may indicate a Byzantine caller, compromised tenant, or systemic upstream mutation bug.
```

Future RFC question:

```text
If subject S emits N Poison Pill records in time window T, should the system escalate from request-level failure to tenant-level operator review or security quarantine?
```

Potential design topics:

```text
Poison Pill rate windows
subject / tenant aggregation
operator review thresholds
UNAUTHORIZED / FORBIDDEN escalation mapping
Dead Letter Channel semantics
forensic evidence requirements
```

Current status: **deferred**.

---

## 3. STALE_IN_PROGRESS + Concurrent COMPLETED Race

Source context: P0.6.32 discussions.

Problem:

```text
A cleanup worker may mark IN_PROGRESS as STALE_IN_PROGRESS after TTL while the original projection call is still running. The original caller may later attempt to mark COMPLETED.
```

Current design direction:

```text
conditional update semantics decide the winner.
COMPLETED may only write if current state is IN_PROGRESS.
If state is already STALE_IN_PROGRESS, late COMPLETED must not overwrite it.
```

Future RFC question:

```text
Should late COMPLETED after STALE_IN_PROGRESS become FAILED(reason_code=COMPLETED_AFTER_STALE), operator review, or a separate reconciliation state?
```

Current status: **deferred**.

---

## 4. Provider Shape Typed Contracts

Source context: P0.6.30 / P0.6.33 shape drift.

Problem:

```text
ProviderOutcome[Mapping[str, Any]] is flexible but weak. It allowed provider-native metadata like selection_source to appear in outputs that were not bridge-valid.
```

Future RFC question:

```text
Should provider outputs use TypedDict, frozen dataclasses, or schema objects instead of raw Mapping[str, Any]?
```

Potential design topics:

```text
TypedDict per provider output
frozen dataclass per provider output
normalization schema declarations
static type checker compatibility
runtime schema validation
provider-native metadata envelope
```

Current status: **deferred**.

---

## 5. ProviderName Enum Materialization

Source context: P0.6.33 / P0.6.34.

Problem:

```text
Failure Priority Matrix tie-breaking uses provider_name. If provider_name remains a raw string, spelling drift becomes business logic drift.
```

Future implementation requirement:

```text
ProviderAggregator skeleton should use canonical ProviderName enum members for tie-breaking.
```

Candidate enum:

```python
class AS2ProviderName(Enum):
    HOST_IDENTITY = "host_identity"
    MODEL_SELECTION = "model_selection"
    HOST_DEFINITION = "host_definition"
    STATIC_MODEL_REGISTRY = "static_model_registry"
    MEMORY_REFERENCE = "memory_reference"
    CAPABILITY_GRANT = "capability_grant"
```

Current status: **planned for P0.6.37 acceptance gate**.

---

## 6. Schema Upcasting Implementation

Source context: P0.6.31 / P0.6.32.

Problem:

```text
Persisted audit and idempotency records carry schema_version. Replay verifiers must read older records after schema evolution.
```

Future RFC question:

```text
How should old schema_version records be upcast into the current canonical replay structure?
```

Potential design topics:

```text
upcaster registry
version-to-version migrations
default values for new fields
forensic verification across schema versions
replay failure categories for unsupported schema versions
```

Current status: **deferred**.

---

## 7. Observability Implementation

Source context: P0.6.34 planning.

Problem:

```text
Skeletons must be observable before production ENABLED, but P0.6.34 only records observability requirements.
```

Future RFC or implementation topics:

```text
RED metrics naming
OpenTelemetry span boundaries
correlation_id propagation
structured log schema
operator dashboards
alert thresholds
```

Current status: **deferred**.

---

## 8. Operator Runbooks

Source context: P0.6.31 / P0.6.32 / P0.6.34.

Problem:

```text
STALE_IN_PROGRESS, Poison Pill, audit fail-closed, and systemic disable paths require operator action. The system should not reach production ENABLED without runbooks.
```

Future topics:

```text
STALE_IN_PROGRESS resolution runbook
Poison Pill investigation runbook
audit outbox outage runbook
provider aggregator failure runbook
manual retry / cancellation policy
operator review evidence bundle
```

Current status: **deferred**.

---

## 9. Production Backend Selection ADR

Source context: P0.6.31 / P0.6.32 / P0.6.34.

Problem:

```text
Backend capabilities are defined, but no storage backend has been selected.
```

Future ADR must evaluate:

```text
atomic conditional update support
transactional outbox support
TTL support
append-only behavior
read/query patterns
operational complexity
local development behavior
failure recovery
data retention and forensic requirements
```

Potential candidates remain deferred:

```text
PostgreSQL
SQLite
Redis
Kafka / compacted topic
NATS KV
S3 / object storage
CAS
filesystem
```

Current status: **deferred**.

---

## 10. Concrete Provider Adapter RFC

Source context: P0.6.28-P0.6.33.

Problem:

```text
The project has provider port contracts and test fakes, but no concrete production adapters.
```

Future RFC topics:

```text
adapter namespace
credential management
network timeout policy
provider retry policy
provider circuit breaker policy
provider observability
security review
real I/O boundary guards
```

Current status: **deferred**.

---

## 11. Production ENABLED Readiness Review

Source context: all P0.6.31-P0.6.34 planning.

Problem:

```text
Production ENABLED requires executable evidence, not only RFCs.
```

Future vote prerequisites:

```text
OutboxAuditSink skeleton accepted
PersistentIdempotencyStore skeleton accepted
ProviderAggregator skeleton accepted
Integration Harness accepted
operator review paths specified
boundary guards green
real I/O decisions explicitly approved or still locked
```

Current status: **locked until P0.6.39+**.

## P0.6.39 Readiness Follow-ups

```text
OPEN: backend vendor ADR with atomic CAS/conditional-update requirements.
OPEN: audit relay ADR approval and implementation.
OPEN: golden replay production fixture readiness.
OPEN: chaos/failure injection for projection side-effect before idempotency completion.
OPEN: concurrent provider execution RFC remains deferred.
OPEN: operator RPC and automatic stale retry remain locked.
```

