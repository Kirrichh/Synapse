# AS2 Production Provider Aggregator RFC — P0.6.33

Status: **proposed / accepted for doc-only planning**  
Patch type: **doc-only RFC**  
Runtime implementation: **LOCKED**

P0.6.33 defines the production contract for an AS2 Provider Aggregator. The
aggregator is the production-facing orchestration and normalization layer between
Host Provider Ports and the AS2 bridge-valid Host Pre-Stage payload consumed by
`prepare_as2_inputs_from_host_prestage(...)`.

This RFC is grounded in the provider-port harness completed across P0.6.28,
P0.6.29, and P0.6.30, and in the durable audit / idempotency design completed in
P0.6.31 and P0.6.32. It does **not** implement a production aggregator, does
**not** add concrete provider adapters, does **not** add real I/O, does **not**
change `as2_runtime_wiring.py`, and does **not** enable production `ENABLED`.

---

## 1. Purpose

P0.6.30 completed the test-only six-provider harness and demonstrated that the
AS2 runtime can accept a fully assembled Host Pre-Stage payload. It also exposed
a design debt: provider-native output shapes may contain fields that are not
accepted by the bridge-valid payload contract.

Example observed during P0.6.30 integration hardening:

```text
Provider-native model selection output:
  {"model": "mock-agent-model", "selection_source": "p0628_fake"}

Bridge-valid model_selection_source:
  {"model": "mock-agent-model"}
```

The production system therefore needs a dedicated aggregation and normalization
layer:

```text
Host Provider Ports
  -> Production Provider Aggregator
  -> normalization / validation / stripping
  -> bridge-valid Host Pre-Stage payload
  -> prepare_as2_inputs_from_host_prestage(...)
```

The aggregator is an Anti-Corruption Layer. It protects the AS2 bridge and
runtime from provider-native drift, extended provider metadata, transport fields,
and future adapter-specific shapes.

---

## 2. Scope

In scope:

```text
production provider aggregation contract
provider output normalization responsibilities
bridge-valid Host Pre-Stage payload construction
Failure Priority Matrix for concurrent aggregation
deterministic tie-breaker for same-priority failures
sequential vs concurrent aggregation semantics
agent-scoped INVALID_INPUT refinement
relationship to prepared_inputs_hash
relationship to persistent idempotency by data only
future namespace discussion
explicit non-goals and locked items
```

Out of scope:

```text
production aggregator implementation
concrete provider adapters
real network / file / DB / CAS / queue I/O
provider adapter namespace creation
as2_runtime_wiring.py changes
AS2ProjectionHandoff behavior changes
Persistent Idempotency Store implementation
Audit Persistence implementation
production ENABLED
```

---

## 3. Verified project facts

P0.6.33 is grounded in the accepted patch sequence and current codebase.

### 3.1 P0.6.28 provider interfaces

P0.6.28 materialized all six Host Provider Port interfaces in production
namespace:

```text
HostIdentityProviderPort
HostDefinitionProviderPort
StaticModelRegistryProviderPort
MemoryReferenceProviderPort
CapabilityGrantProviderPort
ModelSelectionProviderPort
```

It also introduced `HostProviderRequestContext`, `ProviderSuccess`,
`ProviderFailure`, `ProviderOutcome`, and the provider-boundary
`ProviderReasonCode` taxonomy.

### 3.2 P0.6.29 Stage 2 fakes and safe routing

P0.6.29 added Stage 2 full fakes, safe ProviderReasonCode routing through an
explicit mapping layer, TypeGuard helpers for ProviderOutcome narrowing, and a
test-only Stage 1 + Stage 2 Host Pre-Stage provider harness.

P0.6.29 also eliminated raw enum casting such as:

```text
AS2ProviderFailureReasonCode(failure.reason_code.value)
```

Provider-boundary errors must be mapped safely through an Anti-Corruption Layer.

### 3.3 P0.6.30 full six-provider harness

P0.6.30 completed Stage 3 provider fakes and expanded the test-only
`HostPreStageProviderHarness` to all six provider ports. It established:

```text
empty memory refs -> ProviderSuccess, not NOT_FOUND
empty capability grants -> ProviderSuccess, not NOT_FOUND
INVALID_INPUT -> systemic disable by current safe default
CANCELLED -> OBSERVE / no gate transition
sequential fail-fast test harness
full assembled payload can be handed to process_host_prestage(...)
```

### 3.4 P0.6.30 provider shape drift

P0.6.30 also exposed provider shape drift: provider-native fake outputs may
contain metadata rejected by bridge validation. This is not a P0.6.30 bug; it is
an architectural signal that a future production aggregator must normalize
provider output shapes before bridge handoff.

### 3.5 P0.6.31 audit persistence design

P0.6.31 defined Audit Persistence / Transactional Outbox as the preferred model
for durable audit persistence. Provider aggregation must not bypass that future
audit model when it eventually becomes implemented.

### 3.6 P0.6.32 persistent idempotency design

P0.6.32 defined durable idempotency for projection handoff only. Provider
aggregation occurs before `PreparedAS2Inputs` and before projection handoff.
Therefore, the aggregator does not call the idempotency store directly. Its only
linkage to idempotency is through the canonical payload that later becomes
`PreparedAS2Inputs` and then `prepared_inputs_hash`.

---

## 4. Aggregator role

The Production Provider Aggregator is an Anti-Corruption Layer between provider
ports and AS2 runtime.

It is responsible for:

```text
calling or orchestrating six provider ports
accepting provider-native outputs
validating required fields
normalizing output shapes
stripping non-bridge fields
constructing a bridge-valid Host Pre-Stage payload
selecting a deterministic representative ProviderFailure when multiple providers fail
preserving explicit HostProviderRequestContext propagation
```

It is not responsible for:

```text
calling project_validated_as2_inputs(...)
constructing AgentSnapshot
calling AS2ProjectionHandoffSkeleton
calling Persistent Idempotency Store
caching projection results
mutating gate state
writing audit storage directly
performing provider I/O itself
implementing concrete provider adapters
```

The aggregator is not a bridge, not runtime wiring, not a projection caller, not
a cache, and not an idempotency store.

---

## 5. Provider output normalization

Provider ports may return provider-native shapes. The aggregator must transform
those native outputs into the canonical shape accepted by the AS2 bridge.

Required normalization behavior:

```text
accept provider-native output
validate required fields
reject missing required fields
strip non-bridge fields
coerce only explicitly approved compatible representations
fail-closed on schema violation
construct canonical Host Pre-Stage payload
```

Example — model selection:

```text
Provider-native output:
  {"model": "mock-agent-model", "selection_source": "p0628_fake"}

Normalized bridge-valid payload fragment:
  {"model": "mock-agent-model"}
```

Example — definition source:

```text
Provider-native output:
  {"definition_version": "1.0", "class_name": "Agent", "debug_origin": "fake"}

Normalized bridge-valid payload fragment:
  {"definition_version": "1.0", "class_name": "Agent"}
```

Example — memory references:

```text
Provider-native output:
  {"externalized": false, "refs": [], "schema_version": "stage3_fake"}

Normalized bridge-valid payload fragment:
  {"externalized": false, "refs": []}
```

The exact bridge-valid schema is owned by the AS2 bridge contract and the
`PreparedAS2Inputs` validation boundary. The aggregator must not relax bridge
validation rules.

---

## 6. Bridge-valid Host Pre-Stage payload contract

The aggregator output must be a bridge-valid Host Pre-Stage payload suitable for
`prepare_as2_inputs_from_host_prestage(...)`.

Conceptual output shape:

```text
adapter_identity_context
model_selection_source
adapter_definition_source
static_model_registry
memory_ref_source
capability_grant_source
```

The aggregator may know provider-native schemas, but it must only emit the
canonical payload fields accepted by the bridge.

Non-bridge provider metadata must be handled as one of:

```text
stripped if transport/diagnostic metadata
converted if explicitly part of normalization contract
rejected if it indicates schema mismatch or contract drift
```

---

## 7. Failure Priority Matrix

If provider aggregation is sequential, the first failure may be returned in
execution order. If provider aggregation is concurrent, multiple provider
failures may be observed in the same aggregation attempt. The production contract
requires deterministic failure selection.

Failure priority order, highest to lowest:

```text
1. MISSING_REQUEST_CONTEXT
2. UNAUTHORIZED / FORBIDDEN
3. SCHEMA_MISMATCH / INVALID_INPUT
4. NOT_FOUND
5. TIMEOUT / UNAVAILABLE / BACKPRESSURE_REJECTED
6. CANCELLED
```

Rationale:

```text
MISSING_REQUEST_CONTEXT
  highest priority because the system cannot reliably identify or trace the requester.

UNAUTHORIZED / FORBIDDEN
  security boundary failures.

SCHEMA_MISMATCH / INVALID_INPUT
  contract or payload integrity failures.

NOT_FOUND
  required resource missing.

TIMEOUT / UNAVAILABLE / BACKPRESSURE_REJECTED
  infrastructure or capacity conditions.

CANCELLED
  operational termination; lowest priority.
```

The matrix selects the representative `ProviderFailure` routed to Control Plane
when more than one failure exists.

---

## 8. Failure Priority Matrix tie-breaker

When two or more providers fail with equal-priority reason codes, the aggregator
must choose a deterministic winner.

Required tie-breaker:

```text
winner = lexicographic min(provider_name)
```

Example:

```text
memory_reference -> TIMEOUT
model_registry -> TIMEOUT

Both are same priority.
The winner is the lexicographically first provider_name.
```

This is required for replay safety. Two runs with the same provider outcomes
must choose the same representative failure regardless of runtime scheduling,
thread interleaving, or async completion order.

---

## 9. Sequential and concurrent aggregation

P0.6.33 defines a contract compatible with both sequential and concurrent
aggregation.

### 9.1 Sequential fail-fast

Sequential fail-fast is acceptable as a first implementation model:

```text
call provider A
if failure -> return failure
call provider B
if failure -> return failure
...
```

Advantages:

```text
simple implementation
deterministic by construction
easier debugging
matches P0.6.30 test-only harness behavior
```

### 9.2 Concurrent aggregation

Concurrent aggregation is a production target for latency reduction:

```text
start independent provider calls
collect outcomes
if failures exist -> select representative failure using Failure Priority Matrix + tie-breaker
else normalize successes into bridge-valid payload
```

Concurrent aggregation must preserve deterministic failure selection. Runtime
scheduling must not determine the selected failure.

### 9.3 Contract invariant

The selected representative failure must be the same for the same set of provider
outcomes regardless of whether the implementation is sequential or concurrent.

```text
same provider outcomes -> same selected failure
```

If sequential execution uses fail-fast order and concurrent execution uses full
matrix selection, their failure result can differ unless order is aligned to the
priority matrix. Future implementations must either:

```text
use provider call order consistent with priority order
or normalize sequential behavior through the same matrix before returning
```

The implementation strategy is deferred; deterministic semantics are not.

---

## 10. INVALID_INPUT agent-scoped refinement

P0.6.30 routes `INVALID_INPUT` to systemic disable as a safe default. P0.6.33
refines future production semantics.

Rules:

```text
INVALID_INPUT without agent_id
  -> SYSTEMIC_DISABLE

INVALID_INPUT with agent_id + agent-scoped evidence
  -> AGENT_QUARANTINE
```

Agent-scoped evidence means all of the following are true:

```text
failure context includes agent_id
provider is operating on agent-scoped data
failure detail identifies agent-local configuration or data
there is no evidence of global schema, registry, or host-context corruption
```

Examples of agent-scoped evidence:

```text
memory reference payload for agent-42 has invalid local reference format
capability grant manifest for agent-42 has invalid grant entry
agent-local definition override is malformed while global definition registry is healthy
```

Examples that remain systemic:

```text
missing HostProviderRequestContext
global model registry schema mismatch
global definition schema mismatch
invalid provider response shape without agent_id
invalid input from shared registry or shared host configuration
```

This refinement reduces blast radius while preserving fail-closed behavior when
scope is unknown.

---

## 11. Provider failure routing

Provider failures remain value outcomes, not exceptions. The aggregator must not
throw uncontrolled exceptions for expected provider failure conditions.

Routing model:

```text
ProviderFailure
  -> Production Provider Aggregator selects representative failure
  -> future provider failure routing layer maps to Control Plane taxonomy
  -> AS2GateController decides observe/operator/systemic/quarantine action
```

P0.6.33 does not move the test-support routing mapper into production. That
belongs with a future production aggregator implementation skeleton.

---

## 12. Aggregator position in pipeline

Canonical position:

```text
Host Provider Ports
  -> Production Provider Aggregator
  -> bridge-valid Host Pre-Stage payload
  -> prepare_as2_inputs_from_host_prestage(...)
  -> PreparedAS2Inputs
  -> AS2ProjectionHandoffSkeleton
  -> Persistent Idempotency Store boundary
  -> project_validated_as2_inputs(...)
```

The aggregator is upstream of AS2 preparation and upstream of projection
idempotency.

The aggregator must not directly couple to:

```text
AS2ProjectionHandoffSkeleton
Persistent Idempotency Store
OutboxAuditSink implementation
AuditRelay
AgentSnapshot construction
concrete provider adapters
```

---

## 13. prepared_inputs_hash relationship

P0.6.32 defines the canonical idempotency key as:

```text
idempotency_key = correlation_id + prepared_inputs_hash
```

P0.6.33 clarifies that aggregator output contributes to `prepared_inputs_hash`
only after the AS2 preparation boundary constructs `PreparedAS2Inputs`.

Required hash discipline:

```text
prepared_inputs_hash = stable_canonical_hash(prepared_inputs.to_validate_kwargs())
```

Requirements:

```text
hash must not depend on dictionary key order
hash must not include provider-native extra fields stripped by aggregator
hash must be computed from canonical PreparedAS2Inputs validation shape
hash must not depend on wall-clock time, object identity, or transport metadata
```

The aggregator must produce a canonical payload such that logically identical
provider outputs produce the same `PreparedAS2Inputs` and therefore the same
`prepared_inputs_hash`.

---

## 14. Relationship with Persistent Idempotency Store

The aggregator has no direct idempotency-store dependency.

Only data flow:

```text
Aggregator output
  -> bridge-valid payload
  -> PreparedAS2Inputs
  -> prepared_inputs_hash
  -> idempotency_key
```

The aggregator must not:

```text
call the idempotency store
read dedup records
write dedup records
cache AgentSnapshot objects
cache projection results
return DUPLICATE projection outcomes
```

Explicit boundary:

```text
Persistent Idempotency Store is not a cache of projection results.
```

It exists for:

```text
deduplication
retry safety
Poison Pill detection
restart-safe projection handoff protection
forensic state reconciliation
```

It does not exist to:

```text
store AgentSnapshot artifacts
serve projection results
replace CAS
optimize artifact reads
act as long-term business storage
```

---

## 15. Relationship with Audit Persistence

The aggregator does not implement audit persistence. Future production aggregator
implementations may emit aggregation audit events through an approved audit port,
but P0.6.33 does not define or implement those events.

If future aggregation events are audited, they must follow P0.6.31 rules:

```text
Event Payload vs Event Envelope separation
stable deterministic record_hash for payload
previous_state_hash / CHAIN_START chain semantics
Transactional Outbox for critical state-changing events
policy-based failure handling
```

Aggregation diagnostics are not a substitute for Control Plane audit events.

---

## 16. Future namespace candidate

Candidate module for future implementation:

```text
synapse/runtime/as2_provider_aggregator.py
```

This is a candidate only. P0.6.33 does not create the module.

Concrete provider adapters remain a separate future topic. Possible future
adapter namespaces may be considered later, but no namespace is selected here.

---

## 17. Concrete provider adapters deferred

P0.6.33 does not design or implement concrete provider adapters.

Deferred topics:

```text
HTTP / gRPC / DB / file / CAS provider adapters
credential management
secrets handling
network retry policies
provider-specific circuit breakers
adapter namespace selection
adapter security review
observability and tracing instrumentation
```

Production provider adapters require separate design and implementation review.

---

## 18. Future refinements recorded

The following topics are recorded but not resolved in P0.6.33.

### 18.1 Poison Pill Byzantine signal detection

Mass Poison Pill events from the same `security_context.subject`, tenant, or
caller group may indicate Byzantine caller behavior or abuse.

Future topic:

```text
N Poison Pill records from one subject within time window T
  -> tenant-level operator escalation
  -> possible UNAUTHORIZED / FORBIDDEN security response
```

### 18.2 STALE_IN_PROGRESS and concurrent COMPLETED race

P0.6.32 requires conditional update semantics for idempotency store. A future
implementation must handle the race where:

```text
cleanup worker marks IN_PROGRESS -> STALE_IN_PROGRESS
original projection completes and tries to write COMPLETED
```

The conditional update winner determines the outcome. If the original projection
loses because the state is already stale, future policy may classify it as:

```text
FAILED(reason_code=COMPLETED_AFTER_STALE)
```

This remains an idempotency-store implementation topic.

### 18.3 Provider shape typed contracts

Provider payloads currently use mapping-style values. Future implementation may
introduce `TypedDict`, frozen dataclass, or schema objects for provider-native
outputs and normalized bridge payload fragments.

This is not required for P0.6.33.

---

## 19. Production ENABLED impact

Production `ENABLED` remains locked.

P0.6.33 closes the provider aggregation design gap, but does not provide
implementation skeletons for:

```text
Audit Persistence / OutboxAuditSink
Persistent Idempotency Store
Production Provider Aggregator
Concrete Provider Adapters
```

Current forward plan:

```text
P0.6.33 — Production Provider Aggregator RFC
P0.6.34 — Implementation Skeletons Planning / Gap Analysis
P0.6.35+ — implementation skeletons under ENABLED_FOR_TEST
Later — Production ENABLED readiness vote
```

A production readiness vote must be based on executable skeletons and evidence,
not only doc-only RFCs.

---

## 20. Explicit non-goals

P0.6.33 does not include:

```text
production code changes
synapse/ implementation changes
tests/ implementation changes
production provider aggregator implementation
as2_runtime_wiring.py changes
provider adapter namespace creation
concrete provider adapter implementation
real network / DB / file / CAS I/O
Audit Persistence implementation
Persistent Idempotency Store implementation
AS2ProjectionHandoff behavior changes
AgentSnapshot construction changes
production ENABLED
```

---

## 21. Locked items

The following remain locked after P0.6.33:

```text
synapse/ code changes
tests/ code changes
real I/O
concrete provider adapters
provider adapter namespace creation
provider aggregator implementation
audit storage implementation
idempotency store implementation
as2_runtime_wiring.py changes
projection behavior changes
production ENABLED
```

---

## 22. Acceptance criteria

P0.6.33 is complete when:

```text
1. docs/AS2-PRODUCTION-PROVIDER-AGGREGATOR-RFC-P0633.md exists.
2. RFC states the aggregator is an Anti-Corruption Layer between provider ports and AS2 runtime.
3. RFC defines provider output normalization responsibilities.
4. RFC explicitly addresses P0.6.30 provider shape drift.
5. RFC defines bridge-valid Host Pre-Stage payload construction.
6. RFC defines Failure Priority Matrix.
7. RFC defines deterministic same-priority tie-breaker by lexicographic provider_name.
8. RFC defines sequential and concurrent aggregation semantics.
9. RFC requires deterministic selected failure regardless of runtime scheduling.
10. RFC refines INVALID_INPUT with agent_id + evidence -> AGENT_QUARANTINE.
11. RFC defines aggregator position in the AS2 pipeline.
12. RFC states prepared_inputs_hash = stable_canonical_hash(prepared_inputs.to_validate_kwargs()).
13. RFC states provider-native extra fields must not affect prepared_inputs_hash.
14. RFC states idempotency store is not a cache and aggregator has no direct idempotency-store dependency.
15. RFC defers concrete provider adapters and real I/O.
16. RFC records future refinements without implementing them.
17. RFC explicitly locks production code changes, storage, adapters, runtime wiring changes, and production ENABLED.
18. docs/CHANGELOG.md records P0.6.33 as doc-only.
```
