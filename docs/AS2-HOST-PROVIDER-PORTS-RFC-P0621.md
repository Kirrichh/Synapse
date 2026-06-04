# AS2 Host Provider Ports RFC — P0.6.21

Status: **proposed / accepted for doc-only planning**  
Patch type: **doc-only RFC**  
Runtime implementation: **LOCKED**

P0.6.21 defines the production-facing Host Provider Port contracts that will
feed canonical AS2 Host Pre-Stage inputs. It follows the completed P0.6.20
Contract phase, where AS2 model-selection naming was canonicalized and the
legacy selector aliases were removed.

This RFC defines **ports**, not adapters. It does not implement production Host
providers, does not modify `synapse/`, and does not expand runtime wiring.

---

## 1. Motivation

The AS2 runtime wiring skeleton currently operates on an already assembled Host
Pre-Stage payload. Before production Host adapters are implemented, the project
must define the boundary contracts through which the Host will supply canonical
AS2 inputs.

The goals are:

- keep the AS2 core independent from external runtime systems;
- define responsibility boundaries for each Host-provided AS2 input;
- require request-context and correlation continuity;
- specify typed provider outcomes for expected I/O and boundary failures;
- define timeout, backpressure, cancellation, and retry ownership rules;
- avoid promoting P0.6.15 test-only protocols into production API without design
  review.

---

## 2. Non-goals and locked scope

P0.6.21 does **not** introduce:

```text
production Host provider implementations
production Provider Protocols in synapse/
AS2GateController implementation
runtime wiring expansion
project_validated_as2_inputs(...)
AgentSnapshot construction
CAS/storage I/O
gate-state persistence or mutation
degraded mode
production ENABLED state
LLM/capability execution
Integrate/Dream/CVM wiring
```

The RFC is documentation-only. Runtime expansion requires a later approved
patch.

---

## 3. Relationship to P0.6.15 test protocols

P0.6.15 introduced test-only mock providers and `typing.Protocol` contracts for
harness execution. Those protocols are **reference material only**.

They prove the Host responsibility map is executable in tests. They do not define
production API.

Production Host Provider Ports must account for behavioural guarantees that the
P0.6.15 mocks intentionally did not model:

- I/O boundary behaviour;
- timeout and deadline handling;
- cancellation and backpressure;
- security context propagation;
- request/correlation context propagation;
- failure classification;
- resource ownership;
- retry ownership;
- observability and forensic diagnostics.

---

## 4. Canonical AS2 responsibility map

P0.6.21 defines six Host Provider Ports. Each port owns exactly one canonical
AS2 Host Pre-Stage input family.

| Port | Canonical output responsibility |
|---|---|
| `HostIdentityProviderPort` | `adapter_identity_context` |
| `HostDefinitionProviderPort` | `adapter_definition_source` |
| `StaticModelRegistryProviderPort` | `static_model_registry` |
| `MemoryReferenceProviderPort` | `memory_ref_source` |
| `CapabilityGrantProviderPort` | `capability_grant_source` |
| `ModelSelectionProviderPort` | `model_selection_source` |

These outputs are assembled by Host/Pipeline code into the Host Pre-Stage payload
that enters:

```text
prepare_as2_inputs_from_host_prestage(payload)
  -> PreparedAS2Inputs
  -> validate_as2_inputs(..., model_selection_source=...)
```

The removed selector aliases `legacy_agent_runtime_to_dict` and `model_selector`
are not valid production-port outputs.

---

## 5. HostProviderRequestContext requirements

Every production Host Provider Port call must receive or access a request context.
This RFC defines required context content, not a final Python method signature.

Required request-context fields:

| Field | Required | Notes |
|---|---:|---|
| `correlation_id` | yes | Stable correlation identity for tracing and forensic linkage. |
| `request_id` | yes | Host/Pipeline request identity. |
| `timestamp` | yes | Canonical timestamp supplied by Host/Pipeline. |
| `security_context` | optional | Authorization/tenant/operator context, when applicable. |

Rules:

```text
request-context propagation is mandatory
production fallback correlation-id generation is forbidden
missing request context is a provider contract failure
concrete carrier/signature mechanism is deferred
```

Allowed future carriers include, but are not limited to:

- explicit function parameter;
- request object field;
- structured context carrier;
- async context mechanism;
- future Host/Pipeline orchestration context.

The RFC deliberately does not prescribe sync/async signature shape in P0.6.21.

---

## 6. ProviderOutcome model

Production Host Provider Ports must return typed outcomes for expected boundary
and I/O failures. They must not rely on untyped exceptions for normal control
flow.

Reference model:

```python
@dataclass(frozen=True)
class ProviderSuccess(Generic[T]):
    data: T
    correlation_id: str
    latency_ms: int

@dataclass(frozen=True)
class ProviderFailure:
    reason_code: ProviderReasonCode
    detail: str
    correlation_id: str
    latency_ms: int

ProviderOutcome = ProviderSuccess[T] | ProviderFailure
```

`latency_ms` is required for both success and failure outcomes. A fast failure
and a timeout-like failure carry different operational meaning and must remain
visible to Host/Pipeline diagnostics.

---

## 7. ProviderReasonCode taxonomy

The initial provider reason-code taxonomy is:

```text
TIMEOUT
UNAVAILABLE
UNAUTHORIZED
FORBIDDEN
NOT_FOUND
INVALID_INPUT
MISSING_REQUEST_CONTEXT
SCHEMA_MISMATCH
BACKPRESSURE_REJECTED
CANCELLED
```

### `MISSING_REQUEST_CONTEXT`

If a provider call lacks required request context, the provider reports:

```text
ProviderFailure(reason_code=MISSING_REQUEST_CONTEXT)
```

It must not silently create a production fallback `correlation_id`.

Skeleton/test fallback generation remains a local P0.6.18/P0.6.19 skeleton
behaviour and is not a production Host Provider Port rule.

---

## 8. Timeout, deadline, cancellation, and backpressure

P0.6.21 defers concrete sync/async implementation posture, but it does not defer
I/O boundary obligations.

Every production Host Provider Port contract must define:

```text
timeout or deadline semantics
cancellation semantics
backpressure reporting
no unbounded blocking
no unbounded queueing
expected failure classification
```

Concrete values are deferred:

```text
default timeout values: deferred
maximum timeout values: deferred
sync/async binding: deferred
transport mechanism: deferred
```

Provider ports must report timeout/backpressure/cancellation as typed
`ProviderFailure` outcomes.

---

## 9. Retry policy boundary

Provider ports classify failures. They do not own retry policy.

Rules:

```text
ports do not retry implicitly
ports do not hide retry loops
ports report failure reason codes
Host/Orchestrator/Control Plane owns retry policy
```

Reasoning: only the upper orchestration layer knows the overall request deadline,
current gate state, retry budget, operator policy, idempotency constraints, and
whether retrying could duplicate side effects.

---

## 10. Port-specific contracts

### 10.1 `HostIdentityProviderPort`

Responsibility:

```text
produce adapter_identity_context
```

Expected output content includes canonical identity material required by
`validate_as2_inputs(...)`.

Required failure handling:

- missing request context -> `MISSING_REQUEST_CONTEXT`;
- unauthorized identity lookup -> `UNAUTHORIZED` or `FORBIDDEN`;
- missing identity record -> `NOT_FOUND`;
- malformed identity shape -> `SCHEMA_MISMATCH` or `INVALID_INPUT`;
- provider unavailable -> `UNAVAILABLE`.

### 10.2 `HostDefinitionProviderPort`

Responsibility:

```text
produce adapter_definition_source
```

The port supplies the canonical adapter definition source without reading
`AgentRuntime.to_dict()` or other forbidden legacy runtime structures.

Required failure handling:

- definition missing -> `NOT_FOUND`;
- definition shape mismatch -> `SCHEMA_MISMATCH`;
- unauthorized definition access -> `UNAUTHORIZED` or `FORBIDDEN`;
- provider timeout/unavailability -> `TIMEOUT` or `UNAVAILABLE`.

### 10.3 `StaticModelRegistryProviderPort`

Responsibility:

```text
produce static_model_registry
```

The port supplies the static model registry used by AS2 validation to resolve the
canonical `model_selection_source`.

Required failure handling:

- registry unavailable -> `UNAVAILABLE`;
- model entry missing -> `NOT_FOUND`;
- registry shape mismatch -> `SCHEMA_MISMATCH`;
- request cancelled -> `CANCELLED`.

### 10.4 `MemoryReferenceProviderPort`

Responsibility:

```text
produce memory_ref_source
```

The port externalizes memory references as canonical references. It must not read
or inline live runtime memory handles into AS2 input structures.

Required failure handling:

- memory reference unavailable -> `UNAVAILABLE`;
- reference not found -> `NOT_FOUND`;
- backpressure or resource saturation -> `BACKPRESSURE_REJECTED`;
- invalid memory-ref shape -> `SCHEMA_MISMATCH`.

### 10.5 `CapabilityGrantProviderPort`

Responsibility:

```text
produce capability_grant_source
```

The port supplies canonical capability grant manifests. It must not expose live
callables, tool handles, or ambient authority into AS2 inputs.

Required failure handling:

- missing grant -> `NOT_FOUND`;
- unauthorized grant access -> `UNAUTHORIZED` or `FORBIDDEN`;
- invalid grant shape -> `SCHEMA_MISMATCH`;
- provider timeout/unavailability -> `TIMEOUT` or `UNAVAILABLE`.

### 10.6 `ModelSelectionProviderPort`

Responsibility:

```text
produce model_selection_source
```

The port supplies canonical model-selection source data. It must not emit the
removed aliases `legacy_agent_runtime_to_dict` or `model_selector`.

Required failure handling:

- missing model selection -> `NOT_FOUND`;
- invalid model selection shape -> `SCHEMA_MISMATCH` or `INVALID_INPUT`;
- unauthorized model selection access -> `UNAUTHORIZED` or `FORBIDDEN`;
- provider timeout/unavailability -> `TIMEOUT` or `UNAVAILABLE`.

---

## 11. Forbidden reads and outputs

Production Host Provider Ports must not output or rely on:

```text
legacy_agent_runtime_to_dict
model_selector
AgentRuntime.to_dict()
AgentRuntime.name as identity source
AgentRuntime.model as direct model_ref source
AgentRuntime.tools / tools.keys()
Environment._json_safe()
actor mailbox
scheduler/timers/socket/process handles
live callables
CAS/storage handles embedded into payloads
```

Provider ports may consult future approved Host systems, but only through
approved adapter implementations defined after this RFC.

---

## 12. WiringBridgeDisabled semantics

`WiringBridgeDisabled` remains a configuration/operator boundary event.

Host/Pipeline handling requirements:

```text
no retry
no agent quarantine
no systemic provider outage classification
record administrative/config boundary event
route to operator/config review
stop the current AS2 wiring attempt
```

`WiringBridgeDisabled` is not a transient provider failure.

---

## 13. Projection and AgentSnapshot remain locked

P0.6.21 does not authorize:

```text
project_validated_as2_inputs(...)
AgentSnapshot construction
projection handoff
snapshot persistence
runtime expansion beyond the existing skeleton
```

Projection handoff requires a separate design RFC.

---

## 14. Roadmap note

P0.6.21 defines Host Provider Ports only.

Before runtime wiring expansion, the team must resolve how typed wiring outcomes
are handled by control-plane logic. Two acceptable future paths remain open:

```text
A. approve AS2GateController / Control Plane RFC before Runtime Wiring Expansion
B. constrain Runtime Wiring Expansion to evaluator-only behavior with no persisted state mutation
```

Preferred roadmap:

```text
P0.6.21 — Host Provider Ports RFC
P0.6.22 — AS2GateController RFC / Control Plane Design
P0.6.23 — Projection Handoff Design RFC
P0.6.24 — Runtime Wiring Expansion under gate
```

The roadmap note is not itself authorization to implement control-plane code.

---

## 15. Acceptance criteria for this RFC

P0.6.21 is complete when:

```text
six ports are defined
canonical output responsibility is mapped for each port
HostProviderRequestContext requirements are defined
correlation continuity is mandatory
production fallback correlation generation is forbidden
ProviderOutcome success/failure model is defined
latency_ms is required on success and failure
ProviderReasonCode taxonomy is defined
MISSING_REQUEST_CONTEXT is a ProviderFailure
no implicit retry rule is defined
timeout/deadline/backpressure/cancellation obligations are documented
sync/async posture is deferred
concrete context carrier/signature is deferred
WiringBridgeDisabled is classified as config/operator event
P0.6.15 mocks are marked reference-only
projection remains locked
AS2GateController implementation remains locked
production providers remain locked
```
