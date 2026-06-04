# AS2GateController / Control Plane Design RFC — P0.6.22

Status: **proposed / accepted for doc-only planning**  
Patch type: **doc-only RFC**  
Runtime implementation: **LOCKED**

P0.6.22 defines the Control Plane design that will consume AS2 runtime wiring
outcomes and Host Provider outcomes. It builds on the P0.6.17 feature-gate state
machine, the P0.6.18/P0.6.19 runtime wiring skeleton/outcome model, the P0.6.20
Contract phase, and the P0.6.21 Host Provider Ports RFC.

This RFC does **not** implement `AS2GateController`, does **not** mutate or
persist gate state, and does **not** expand runtime wiring. It defines the
control-plane contract and identifies decisions that must be made before any
future implementation.

---

## 1. Verified project facts

P0.6.22 is grounded in facts verified against the current project code and
project documents.

### 1.1 Verified from executable scope

The following facts are true for the executable AS2 scope (`synapse/` and
primary tests):

```text
ModelSelectionConflictError is not present in executable AS2 scope
legacy_agent_runtime_to_dict is not an executable AS2 input
model_selector is not an executable AS2 input
```

`legacy_agent_runtime_to_dict` and `model_selector` remain only as permanent
reintroduction-guard targets in `tests/test_as2_legacy_reintroduction_guard.py`.
They are removed aliases, not supported runtime inputs.

### 1.2 Verified from project documents

Existing project documents establish these facts:

```text
AS2GateController exists only as an RFC-level contract in P0.6.17
no AS2GateController implementation exists in synapse/
transition audit records are required by P0.6.17
audit storage / telemetry / I/O are not implemented or authorized
WiringBridgeDisabled is documented as a configuration/operator event
Control Plane before Runtime Wiring Expansion is the preferred P0.6.21 roadmap
```

### 1.3 Consequence for P0.6.22

P0.6.22 must design the Control Plane, not implement it. It must also avoid
reintroducing removed legacy selector paths as hypothetical Control Plane inputs.

---

## 2. Scope

P0.6.22 defines:

```text
control-plane responsibility boundary
transition authority
explicit state transition rules
audit record schema
ProviderFailure -> Control Plane Action matrix
WiringOutcome -> Control Plane Action matrix
WiringBridgeDisabled handling
retry ownership boundary
quarantine ownership and escalation policy
operator reset workflow
sticky DISABLED_SYSTEMIC semantics
explicit non-inputs to AS2GateController
Decisions Required for persistence and threshold policy
```

The RFC is documentation-only.

---

## 3. Non-goals and locked scope

P0.6.22 does **not** introduce:

```text
production AS2GateController implementation
state persistence implementation
audit storage implementation
operator RPC implementation
production Host provider implementation
Host Provider Ports Harness
runtime wiring expansion
project_validated_as2_inputs(...)
AgentSnapshot construction
CAS/storage I/O
degraded mode
production ENABLED state
LLM/capability execution
Integrate/Dream/CVM wiring
```

Projection handoff remains locked and requires a separate RFC.

---

## 4. Control Plane responsibility boundary

`AS2GateController` is the conceptual Control Plane authority for AS2 runtime gate
state transitions. It consumes typed Data Plane signals and decides whether a
gate-state transition is required.

The controller is not part of:

```text
agent_snapshot_bridge.py
agent_snapshot_adapter.py
runtime wiring preparation logic
interpreter memory
AgentRuntime
Environment
projection
AgentSnapshot construction
```

### 4.1 Data Plane vs Control Plane

Data Plane responsibilities:

```text
assemble Host Pre-Stage payload
run gate evaluator
prepare AS2 inputs
validate AS2 inputs
return typed WiringOutcome
classify provider boundary failures as ProviderOutcome
```

Control Plane responsibilities:

```text
interpret typed outcomes
own transition decisions
own operator reset workflow
record audit transition intent/records
coordinate quarantine/global-disable decisions
preserve sticky DISABLED_SYSTEMIC semantics
```

The Data Plane must not mutate persistent gate state as a side effect of
preparation or validation.

---

## 5. AS2GateController input categories

The future controller may receive or be notified about:

```text
WiringSuccess
WiringGateClosed
WiringBridgeDisabled
WiringAgentQuarantineRequest
WiringSystemicDisableRequest
ProviderSuccess[T]
ProviderFailure
operator commands / operator acknowledgements
health observations, as observations only
```

Not every notification authorizes a state transition. Section 8 and Section 9
specify the action mapping.

---

## 6. Explicit non-inputs to AS2GateController

The following are explicitly **not** Control Plane inputs:

```text
ModelSelectionConflictError
legacy_agent_runtime_to_dict
model_selector
canonical-vs-legacy selector conflict path
WiringBridgeDisabled as a DISABLED_SYSTEMIC trigger
```

### 6.1 `ModelSelectionConflictError`

`ModelSelectionConflictError` was tied to the expand-phase canonical-vs-legacy
selector conflict path. P0.6.20 removed that path. The type is not present in the
executable AS2 scope and must not be handled as a future Control Plane input.

### 6.2 Removed selector aliases

`legacy_agent_runtime_to_dict` and `model_selector` are removed selector aliases.
They are blocked by the permanent reintroduction guard and must not be restored
as Control Plane inputs or provider outputs.

### 6.3 Removed conflict path

The canonical-vs-legacy selector conflict path cannot occur at runtime after the
Contract phase. Control Plane design must not reserve transitions for it.

---

## 7. Gate states and transition authority

P0.6.22 reuses the five-state gate model defined by P0.6.17:

```text
DISABLED_BY_DEFAULT
ENABLED_FOR_TEST
DISABLED_AGENT_QUARANTINE
DISABLED_SYSTEMIC
DISABLED_OPERATOR_OVERRIDE
```

No new state is introduced in P0.6.22.

### 7.1 Transition authority

Only the Control Plane may authorize future gate-state transitions. The runtime
wiring skeleton may return outcomes that request action, but it must not apply
state changes directly.

Allowed transition authority matrix:

| Transition | Authority | Trigger class |
|---|---|---|
| `DISABLED_BY_DEFAULT -> ENABLED_FOR_TEST` | Control Plane | explicit operator/test-control action |
| `ENABLED_FOR_TEST -> DISABLED_AGENT_QUARANTINE` | Control Plane | agent-scoped failure |
| `ENABLED_FOR_TEST -> DISABLED_SYSTEMIC` | Control Plane | systemic failure |
| `ENABLED_FOR_TEST -> DISABLED_OPERATOR_OVERRIDE` | Control Plane | emergency operator action |
| `DISABLED_AGENT_QUARANTINE -> ENABLED_FOR_TEST` | Control Plane | quarantine resolution decision |
| `DISABLED_AGENT_QUARANTINE -> DISABLED_SYSTEMIC` | Control Plane | escalation |
| `DISABLED_SYSTEMIC -> DISABLED_OPERATOR_OVERRIDE` | Control Plane | operator acknowledgement |
| `DISABLED_OPERATOR_OVERRIDE -> DISABLED_BY_DEFAULT` | Control Plane | operator reset to safe default |

All implicit transitions are forbidden.

---

## 8. WiringOutcome -> Control Plane Action matrix

| Wiring outcome | Control Plane action | State transition |
|---|---|---|
| `WiringSuccess` | Record success/visibility event when required by future policy. | none |
| `WiringGateClosed` | Respect current gate state; no runtime preparation should proceed. | none |
| `WiringBridgeDisabled` | Treat as configuration/operator boundary event. | none |
| `WiringAgentQuarantineRequest` | Evaluate agent-scoped quarantine decision. | candidate `DISABLED_AGENT_QUARANTINE` |
| `WiringSystemicDisableRequest` | Evaluate systemic-disable decision. | candidate `DISABLED_SYSTEMIC` |

`WiringSuccess` does not authorize projection by itself. Projection handoff remains
locked until a later RFC.

---

## 9. WiringBridgeDisabled handling

`WiringBridgeDisabled` is a configuration/operator boundary event, not a provider
failure and not a runtime systemic failure.

Required handling:

```text
Host/Pipeline short-circuits current AS2 wiring attempt
no retry
no agent quarantine
no systemic provider outage classification
record configuration/operator event
route to operator/config review
AS2GateController may receive audit/visibility notification
AS2GateController must not transition gate state because of this outcome
```

`WiringBridgeDisabled` must not trigger `DISABLED_SYSTEMIC`.

---

## 10. ProviderFailure -> Control Plane Action matrix

P0.6.21 defined typed provider outcomes. P0.6.22 defines how the Control Plane
should interpret provider failures at the policy boundary.

| Provider reason code | Control Plane action | Transition posture |
|---|---|---|
| `TIMEOUT` single provider / single occurrence | record observation; orchestration may retry within policy | no immediate transition |
| `TIMEOUT` repeated N times or across >=2 providers | escalate to systemic candidate | candidate `DISABLED_SYSTEMIC` |
| `UNAVAILABLE` single provider / single occurrence | record observation | no immediate transition |
| `UNAVAILABLE` repeated N times or across >=2 providers | escalate to systemic candidate | candidate `DISABLED_SYSTEMIC` |
| `BACKPRESSURE_REJECTED` | return to orchestrator capacity/backoff policy | no immediate transition |
| `BACKPRESSURE_REJECTED` repeated/systemic | escalation candidate after thresholds | candidate `DISABLED_SYSTEMIC` |
| `MISSING_REQUEST_CONTEXT` | caller contract violation | `WiringSystemicDisableRequest` / systemic handling |
| `SCHEMA_MISMATCH` | contract/version incompatibility | `DISABLED_SYSTEMIC` |
| `INVALID_INPUT` with known `agent_id` | agent-scoped bad input | candidate `DISABLED_AGENT_QUARANTINE` |
| `INVALID_INPUT` global/non-agent-scoped | pipeline contract failure | candidate `DISABLED_SYSTEMIC` |
| `NOT_FOUND` agent-scoped | agent-scoped missing dependency | candidate `DISABLED_AGENT_QUARANTINE` |
| `NOT_FOUND` global/model/registry | configuration or contract issue | operator review or systemic candidate |
| `UNAUTHORIZED` / `FORBIDDEN` | security/config review | no automatic transition by default |
| `CANCELLED` | expected termination / caller cancellation | no transition |

### 10.1 `MISSING_REQUEST_CONTEXT`

`MISSING_REQUEST_CONTEXT` is not an agent payload defect. It indicates the caller
failed to supply the required HostProviderRequestContext. It must not map to
agent quarantine.

Required classification:

```text
ProviderFailure(MISSING_REQUEST_CONTEXT)
  -> caller contract violation
  -> systemic/control-plane handling
  -> no retry
  -> no agent quarantine
```

### 10.2 Threshold semantics

Threshold values are not fixed by P0.6.22. The RFC requires the future
implementation design to define:

```text
N for repeated TIMEOUT / UNAVAILABLE / BACKPRESSURE_REJECTED
observation window
per-provider vs cross-provider aggregation
agent-scoped vs global aggregation
whether threshold counters survive restart
```

This is a **Decision Required** before implementation.

---

## 11. Retry ownership

P0.6.21 established that provider ports do not retry implicitly. P0.6.22 extends
that boundary:

```text
provider ports classify failures
runtime wiring returns typed outcomes
Control Plane decides transition eligibility
Host/Orchestrator owns retry scheduling and backoff policy
```

The Control Plane may deny retry because of gate state, sticky systemic disable,
operator override, retry budget exhaustion, or non-retryable reason codes.

The Control Plane itself must not hide retry loops inside state transition logic.

---

## 12. Quarantine ownership and escalation

Agent quarantine is scoped and must not automatically become global disable.

Agent-scoped quarantine requires:

```text
known agent_id or equivalent scoped identity
agent-scoped reason code
correlation_id / request_id
no evidence of broad provider/systemic failure
```

Escalation from `DISABLED_AGENT_QUARANTINE` to `DISABLED_SYSTEMIC` requires an
explicit Control Plane decision and audit record.

Potential escalation triggers:

```text
same failure across multiple agents
same provider failure across multiple agents
repeated quarantine beyond future threshold
schema/contract failures masquerading as agent-specific invalid input
```

Thresholds remain a **Decision Required** for implementation planning.

---

## 13. Sticky DISABLED_SYSTEMIC semantics

`DISABLED_SYSTEMIC` remains sticky.

Forbidden:

```text
DISABLED_SYSTEMIC -> ENABLED_FOR_TEST
DISABLED_SYSTEMIC -> auto-reset
health-check-only re-enable
background retry re-enable
implicit reset after restart without documented policy
```

Required recovery chain:

```text
DISABLED_SYSTEMIC
  -> DISABLED_OPERATOR_OVERRIDE
     [operator: acknowledge_systemic_failure]
  -> DISABLED_BY_DEFAULT
     [operator: reset_to_disabled_by_default]
  -> ENABLED_FOR_TEST
     [operator/test-control: request_enable_for_test]
```

Operator reset is a Host Control Plane event, not an interpreter memory mutation.

---

## 14. Audit record schema

P0.6.17 requires auditable transition records. P0.6.22 refines the conceptual
schema without implementing storage.

Minimum fields:

```text
event_id
correlation_id or request_id
from_state
to_state
trigger
reason_code
scope: global | agent | configuration
agent_id, when scoped
provider_name, when provider-originated
operator_identity, when operator-triggered
timestamp_source
decision_summary
observed_latency_ms, when provider-originated
action: transition | no_transition | audit_only
```

Audit records must distinguish:

```text
state-changing transitions
audit-only configuration events
provider observations
operator acknowledgements
operator resets
```

P0.6.22 does not implement audit persistence or storage.

---

## 15. Persistence model — Decisions Required

Existing documents require audit records, but they do not authorize audit storage
or persistent gate-state implementation. P0.6.22 must make this explicit instead
of assuming persistence.

### 15.1 Decision: in-memory vs persisted gate state

Open decision:

```text
Should the first AS2GateController implementation keep gate state in memory,
or should it require persistent/distributed state from the beginning?
```

Current team proposal to be evaluated:

```text
first implementation may use in-memory gate state
restart may fail safe to DISABLED_BY_DEFAULT
persistent/distributed gate state is deferred
```

### 15.2 Decision: restart behavior

Open decision:

```text
Should restart always return to DISABLED_BY_DEFAULT,
or should sticky DISABLED_SYSTEMIC survive restart through state persistence?
```

Fail-safe restart to `DISABLED_BY_DEFAULT` is compatible with default-off safety,
but it changes how sticky systemic state behaves across process restarts. This
must be decided before implementation.

### 15.3 Decision: audit persistence

Open decision:

```text
Are persistent append-only audit records required for the first implementation,
or only required before production activation?
```

P0.6.17 requires auditable records. It does not implement or authorize storage.
P0.6.22 does not implement storage.

### 15.4 Decision: future persistence hook

Open decision:

```text
Should P0.6.22 define a future persistence hook/interface, or defer the interface
entirely to the future implementation RFC?
```

No persistence hook is implemented in P0.6.22.

---

## 16. Operator reset workflow

The Control Plane must treat reset as an explicit operator workflow.

Conceptual commands inherited from P0.6.17:

```text
request_enable_for_test(reason, operator_identity)
request_operator_override(reason, operator_identity)
acknowledge_systemic_failure(reason, operator_identity)
reset_to_disabled_by_default(reason, operator_identity)
```

Reset requirements:

```text
operator_identity is required
reason is required
transition audit record is required
no direct memory mutation
no interpreter state reach-in
no hidden auto-reset
```

---

## 17. Health observations

Health checks may become inputs to dashboards or audit records, but they must not
re-enable AS2 runtime wiring automatically.

Rules:

```text
health observation may influence operator review
health observation may inform retry/backoff policy
health observation must not transition DISABLED_SYSTEMIC to ENABLED_FOR_TEST
health observation must not clear DISABLED_OPERATOR_OVERRIDE
```

---

## 18. Relationship to Projection Handoff

P0.6.22 does not authorize projection handoff.

`WiringSuccess` means preparation/validation succeeded under the current skeleton.
It does not mean:

```text
project_validated_as2_inputs(...) may be called
AgentSnapshot may be constructed
snapshot may be persisted
runtime wiring may expand to production path
```

Projection Handoff requires a later design RFC.

---

## 19. Acceptance criteria for this RFC

P0.6.22 is complete when:

```text
AS2GateController responsibility boundary is defined
transition authority is defined
explicit state transition table is included
sticky DISABLED_SYSTEMIC and no-auto-reset are preserved
operator reset workflow is defined
ProviderFailure -> Control Plane Action matrix is included
WiringOutcome -> Control Plane Action matrix is included
WiringBridgeDisabled is no-transition config/operator event
MISSING_REQUEST_CONTEXT maps to caller contract violation / systemic handling
quarantine ownership and escalation policy are defined
retry ownership boundary is defined
audit record schema is defined
Verified Project Facts are recorded
Explicit Non-Inputs are recorded
Decisions Required section covers persistence model, restart behavior, audit persistence, persistence hook, and threshold semantics
runtime implementation remains locked
projection remains locked
production Host providers remain locked
```

---

## 20. Changelog summary for P0.6.22

This RFC adds the Control Plane design contract needed before Runtime Wiring
Expansion. It does not implement control-plane code.
