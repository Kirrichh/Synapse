# AS2 Projection Handoff Design RFC — P0.6.23

Status: **proposed / accepted for doc-only planning**  
Patch type: **doc-only RFC**  
Runtime implementation: **LOCKED**

P0.6.23 defines the Projection Handoff design that governs when validated
`PreparedAS2Inputs` may be passed to `project_validated_as2_inputs(...)` to
produce `AgentSnapshot` and `AdapterDerivationRecord` artifacts.

This RFC follows:

```text
P0.6.14 — Runtime Wiring Design RFC
P0.6.15 — Runtime Wiring Harness / Host Provider Mocks
P0.6.16 — Naming Debt Expand phase
P0.6.17 — Feature Gate RFC + Boundary Hardening
P0.6.18 — Runtime Wiring Skeleton
P0.6.19 — Runtime Wiring Hardening
P0.6.20 — Contract phase / legacy alias removal
P0.6.21 — Host Provider Ports RFC
P0.6.22 — AS2GateController / Control Plane RFC
```

P0.6.23 does **not** implement projection handoff, does **not** call
`project_validated_as2_inputs(...)`, and does **not** construct `AgentSnapshot` in
runtime wiring. It defines the handoff contract for a later approved patch.

---

## 1. Motivation

The AS2 runtime wiring path can now produce typed preparation outcomes. In the
success case, runtime wiring returns validated `PreparedAS2Inputs`. The project
still needs an approved output-boundary contract for how those prepared inputs
may become canonical state:

```text
PreparedAS2Inputs
  -> project_validated_as2_inputs(...)
  -> AgentSnapshot + AdapterDerivationRecord
```

Without this RFC, a future runtime expansion could accidentally make projection
an automatic side effect of `WiringSuccess`, call projection from the bridge or
skeleton, retain snapshot references in the preparation layer, or classify core
projection failures as ordinary agent/provider failures.

P0.6.23 prevents that drift by defining the handoff boundary before code is
written.

---

## 2. Non-goals and locked scope

P0.6.23 does **not** introduce:

```text
projection handoff implementation
project_validated_as2_inputs(...) call from runtime wiring
AgentSnapshot construction in runtime wiring
idempotency implementation
projection compensation implementation
projection retry implementation
AS2GateController implementation
production Host provider implementation
Host Provider Ports Harness
state persistence implementation
audit storage implementation
CAS/storage I/O
runtime wiring expansion
degraded mode
production ENABLED state
LLM/capability execution
Integrate/Dream/CVM wiring
```

Projection remains locked until this RFC is accepted and a later implementation
patch is explicitly opened.

---

## 3. Verified project facts

P0.6.23 is grounded in the current code and project documents.

### 3.1 Verified code facts

The current executable scope establishes:

```text
Bridge does not call project_validated_as2_inputs(...)
Runtime skeleton does not call project_validated_as2_inputs(...)
Bridge does not construct AgentSnapshot
Runtime skeleton does not construct AgentSnapshot
ModelSelectionConflictError is not executable AS2 input
legacy_agent_runtime_to_dict and model_selector are removed aliases
```

The removed selector aliases remain only as permanent guard targets and are not
valid projection inputs, Control Plane inputs, or Host Provider outputs.

### 3.2 Verified document facts

Existing project documents establish:

```text
Bridge remains preparation/validation layer
Projection is called by Host/Pipeline after successful preparation
AS2GateController is an RFC-level control-plane contract
Control Plane before Runtime Wiring Expansion is the preferred roadmap
WiringSuccess is a successful preparation outcome, not projection authorization
WiringBridgeDisabled is a configuration/operator boundary event
```

### 3.3 Consequence for P0.6.23

P0.6.23 must define the handoff layer, not implement it. It must preserve the
existing bridge/skeleton boundaries and prevent projection from becoming an
implicit side effect of preparation.

---

## 4. Projection handoff responsibility boundary

Projection handoff is owned by a Host/Pipeline projection handoff layer. The RFC
does not require a final module or class name. Possible future names include
`AS2ProjectionOrchestrator` or `ProjectionHandoffLayer`, but the responsibility
is the architectural contract.

### 4.1 Authorized handoff layer responsibilities

The future handoff layer may be responsible for:

```text
receiving WiringSuccess and PreparedAS2Inputs
requesting fresh Control Plane authorization immediately before projection
creating projection audit records
calling project_validated_as2_inputs(...) after authorization
receiving AgentSnapshot and AdapterDerivationRecord
classifying projection failures
returning or routing projected artifacts to Host/Pipeline lifecycle
```

### 4.2 Explicitly forbidden callers

The following must not call projection:

```text
agent_snapshot_bridge.py
runtime wiring skeleton
AS2GateController itself
Host Provider Ports
provider adapters
legacy runtime objects
```

Control Plane authorizes or denies projection. It does not construct snapshots
and does not call projection directly.

---

## 5. Projection authorization flow

`WiringSuccess` is necessary but not sufficient for projection.

Required projection preconditions:

```text
WiringSuccess exists
PreparedAS2Inputs are available
valid request context / correlation_id exists
bridge safety layer is not disabled
Control Plane approves projection
permitted gate state is verified immediately before projection
projection attempt is audit-visible
```

A future implementation must not treat an earlier `WiringSuccess` as durable
projection permission. Gate state and Control Plane approval must be checked
freshly immediately before the projection call.

### 5.1 Temporal authorization requirement

Race condition to prevent:

```text
T0: runtime skeleton returns WiringSuccess
T1: gate transitions to DISABLED_SYSTEMIC or DISABLED_OPERATOR_OVERRIDE
T2: projection runs using stale success state
```

Required rule:

```text
Projection requires fresh Control Plane authorization at T2, immediately before
project_validated_as2_inputs(...). Earlier WiringSuccess does not bypass this
check.
```

This temporal rule is mandatory for runtime expansion.

---

## 6. Gate and Control Plane conditions

The first implementation is expected to use `ENABLED_FOR_TEST` as the only
projection-permitting state. A future production `ENABLED` state remains outside
P0.6.23 scope.

Projection is blocked when the Control Plane observes or reports:

```text
DISABLED_BY_DEFAULT
DISABLED_AGENT_QUARANTINE, if the target agent is scoped by the quarantine
DISABLED_SYSTEMIC
DISABLED_OPERATOR_OVERRIDE
WiringBridgeDisabled short-circuit
missing request context
operator denial
```

The exact implementation API for the Control Plane decision remains future work.
The RFC requires the decision to be explicit and audit-visible.

---

## 7. Projection audit lifecycle

Projection attempts must be audit-visible. P0.6.23 defines audit events without
implementing storage.

Projection lifecycle events:

```text
projection_requested
projection_approved
projection_denied
projection_started
projection_completed
projection_failed
```

Minimum projection audit fields:

```text
event_id
correlation_id
request_id
agent_id, if known
event_type
projection_decision
reason_code
snapshot_hash, on successful projection
adapter_derivation_record_id or adapter_derivation_record_hash, when available
previous_state_hash or audit_chain_link, when available
timestamp
decision_summary
```

### 7.1 Audit-chain linkage

For production forensic integrity, projection audit records should support hash
chaining or equivalent integrity linkage.

Policy:

```text
previous_state_hash / audit_chain_link may be optional in early harnesses
previous_state_hash / audit_chain_link is required before production activation
```

This RFC defines the requirement. It does not implement audit storage,
cryptographic hashing, or persistence.

---

## 8. AdapterDerivationRecord handling

Successful projection returns or materializes two distinct artifacts:

```text
AgentSnapshot
AdapterDerivationRecord
```

The RFC requires explicit lifecycle handling for `AdapterDerivationRecord`.

### 8.1 Receiver

After successful projection, the approved Projection Handoff Layer receives both
`AgentSnapshot` and `AdapterDerivationRecord` from the projection call.

### 8.2 Lifecycle ownership

Host/Pipeline owns downstream lifecycle of both projected artifacts after
successful handoff.

Forbidden retention:

```text
Bridge must not retain AdapterDerivationRecord
Runtime skeleton must not retain AdapterDerivationRecord
Control Plane must not own AdapterDerivationRecord lifecycle
```

### 8.3 Audit relationship

Projection audit records must reference `AdapterDerivationRecord` by stable
identifier or hash when available.

The audit record may include derivation summary metadata, but the
`AdapterDerivationRecord` remains a separate projection artifact unless a later
RFC defines embedded storage semantics.

Required linkage fields:

```text
correlation_id
request_id
snapshot_hash
adapter_derivation_record_id or adapter_derivation_record_hash
projection event id
```

### 8.4 Artifact boundary

`AdapterDerivationRecord` must not become an implicit bridge cache, runtime
skeleton cache, or Control Plane state object.

---

## 9. AgentSnapshot ownership and lifecycle

`AgentSnapshot` is constructed only by the approved projection handler/core path,
not by the bridge, runtime skeleton, or Control Plane.

Ownership after successful projection:

```text
Projection Handoff Layer receives AgentSnapshot
Host/Pipeline owns downstream AgentSnapshot lifecycle
Bridge does not own AgentSnapshot
Runtime skeleton does not own AgentSnapshot
Control Plane does not own AgentSnapshot
```

### 9.1 Forbidden reference retention

Transactional isolation requires:

```text
Bridge must not hold, cache, retain, or expose references to projected AgentSnapshot
Runtime skeleton must not hold, cache, retain, or expose references to projected AgentSnapshot
Bridge and runtime skeleton must not retain AdapterDerivationRecord references
```

Any projected artifacts must be returned to the approved Projection Handoff Layer
and then governed by Host/Pipeline lifecycle.

---

## 10. Transactional isolation

Projection execution must be isolated from bridge and skeleton state.

Required interface-level rules:

```text
projection worker must not mutate bridge state
projection worker must not mutate runtime skeleton state
projection must not depend on bridge internals
projection must consume validated PreparedAS2Inputs
projection must return AgentSnapshot + AdapterDerivationRecord or typed failure
```

At the handoff boundary, projection must be treated as deterministic and
side-effect isolated with respect to bridge and skeleton.

This RFC does not claim projection has no internal allocation or object creation.
It requires that projection does not create hidden state in preparation/validation
layers.

---

## 11. Idempotency and replay-safety semantics

Projection handoff must define replay-safe semantics before runtime expansion.

Required contract:

```text
Same PreparedAS2Inputs + same correlation context + same projection authorization
must not produce divergent AgentSnapshot semantics.
```

Repeated projection attempts with identical inputs and identical projection
context must be either:

```text
strictly idempotent
or governed by an explicit compensation/deduplication mechanism
```

### 11.1 Decisions Required

P0.6.23 requires a future implementation decision:

```text
strict idempotency vs explicit compensation mechanism
```

No runtime implementation may call projection from runtime wiring until
idempotency semantics are approved.

### 11.2 Interrupted projection

If projection is interrupted after authorization but before successful completion
(for example, OOM, kill signal, process interruption), the next attempt must not
silently create divergent snapshot semantics.

Until a compensation/idempotency mechanism is defined, interrupted projection is
classified conservatively as a systemic/core concern.

---

## 12. Projection failure classification

Projection failures are distinct from provider failures.

### 12.1 Agent-scoped projection failure

An agent-scoped projection failure may map to:

```text
WiringAgentQuarantineRequest candidate
```

Only if there is clear evidence that the failure is scoped to the target agent or
prepared agent inputs.

### 12.2 Systemic Core Failure

Default rule:

```text
Projection internal failure defaults to systemic/core failure unless agent-scoped
evidence exists.
```

Systemic Core Failure examples:

```text
projection invariant violation
unexpected AgentSnapshot construction error
OOM / resource exhaustion during projection
hidden core bug
non-deterministic projection result
projection interrupted without approved compensation semantics
```

Systemic Core Failure maps to:

```text
WiringSystemicDisableRequest candidate
DISABLED_SYSTEMIC candidate through Control Plane
```

### 12.3 Projection failure after successful validation

If `validate_as2_inputs(...)` succeeded but `project_validated_as2_inputs(...)`
fails, the failure must not be treated as an ordinary provider failure.

Classification must choose:

```text
agent-scoped projection failure, if evidence exists
Systemic Core Failure, by default
```

---

## 13. AdapterDerivationRecord and snapshot audit outcome

Projection completion audit must distinguish the returned artifacts:

```text
AgentSnapshot — canonical projected state artifact
AdapterDerivationRecord — derivation/provenance artifact
```

Required audit semantics:

```text
projection_completed includes snapshot_hash when available
projection_completed references AdapterDerivationRecord hash/id when available
projection_failed includes failure classification and reason_code
projection_denied includes Control Plane denial reason
```

`AdapterDerivationRecord` is not merely audit text. It is a separate artifact whose
lifecycle and reference semantics must be defined by the handoff layer and future
persistence design.

---

## 14. Explicit non-inputs and non-goals

Projection Handoff must not reintroduce removed or forbidden boundaries.

Explicit non-inputs:

```text
legacy_agent_runtime_to_dict
model_selector
ModelSelectionConflictError
canonical-vs-legacy selector conflict path
AgentRuntime.to_dict()
AgentRuntime.name as identity source
AgentRuntime.model as model_ref source
Environment._json_safe()
actor mailbox / scheduler / socket / process handles
```

Explicit non-goals:

```text
projection implementation
snapshot persistence implementation
audit storage implementation
control-plane persistence implementation
production Host provider implementation
projection retry engine
idempotency store
compensation engine
```

---

## 15. Relationship to Control Plane

Projection Handoff depends on Control Plane authorization but does not implement
Control Plane.

Control Plane responsibilities remain:

```text
approve or deny projection
interpret gate state
handle systemic/quarantine decisions
audit transition decisions
own operator reset workflow
```

Projection Handoff responsibilities remain:

```text
request authorization
record projection lifecycle events
call projection only when authorized
receive projection artifacts
classify projection failures
return or route artifacts to Host/Pipeline lifecycle
```

---

## 16. Relationship to future harness and expansion

P0.6.23 prepares the contract for later stages.

Suggested roadmap:

```text
P0.6.23 — Projection Handoff Design RFC
P0.6.24 — AS2GateController Harness + Projection Integration Tests
P0.6.25 — Runtime Wiring Expansion under gate
```

P0.6.24 must resolve or test against approved decisions for:

```text
threshold semantics for repeated provider failures
control-plane persistence model required for harness scope
projection idempotency / compensation model
projection authorization test matrix
```

P0.6.25 must not proceed until both Control Plane handling and Projection Handoff
have executable coverage.

---

## 17. Acceptance criteria

P0.6.23 is accepted when:

```text
docs/AS2-PROJECTION-HANDOFF-RFC-P0623.md exists
docs/CHANGELOG.md records the doc-only patch
synapse/ remains unchanged
RFC defines projection caller / handoff subject
RFC defines fresh Control Plane authorization immediately before projection
RFC states WiringSuccess is necessary but not sufficient
RFC defines gate/control-plane preconditions
RFC defines projection audit lifecycle
RFC includes snapshot_hash in successful projection audit
RFC includes previous_state_hash / audit-chain linkage as production requirement
RFC defines idempotency and replay-safety semantics
RFC includes strict idempotency vs compensation as Decision Required
RFC defines Projection Internal Failure / Systemic Core Failure
RFC defines AgentSnapshot ownership / lifecycle
RFC defines AdapterDerivationRecord handling:
  - who receives it
  - who owns lifecycle
  - whether audit embeds it or references it
  - how it links to correlation_id / snapshot_hash / projection event
RFC defines transactional isolation:
  - projection worker must not mutate bridge/skeleton state
  - bridge must not hold reference to projected AgentSnapshot
  - bridge/skeleton must not retain AdapterDerivationRecord references
RFC keeps bridge/skeleton projection boundaries locked
RFC keeps implementation locked
```

---

## 18. Locked after P0.6.23

The following remain locked after this RFC:

```text
project_validated_as2_inputs(...) runtime call
AgentSnapshot construction in runtime wiring
projection handoff implementation
idempotency implementation
compensation implementation
AS2GateController implementation
production Host provider implementation
state persistence implementation
CAS/storage I/O
audit storage implementation
degraded mode
production ENABLED state
runtime wiring expansion
LLM/capability execution
Integrate/Dream/CVM wiring
```
