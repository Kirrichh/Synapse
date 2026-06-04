# P0.6.17 — AS2 Runtime Feature Gate RFC + Boundary Hardening

Status: **COMPLETED — RFC + TEST-ONLY BOUNDARY HARDENING**

P0.6.17 defines the AS2 runtime feature gate as an RFC-level state machine and
adds shared architectural fitness tests for the AS2 adapter/bridge boundary.
It does **not** implement the runtime feature gate, does **not** add production
Host providers, and does **not** wire AS2 into runtime.

## 1. Scope

P0.6.17 is limited to:

```text
docs/AS2-FEATURE-GATE-RFC-P0617.md
docs/CHANGELOG.md
tests/support/as2_boundary_guards.py
tests/test_as2_architectural_fitness.py
```

The patch may be read as the contract gate between the completed P0.6.16
Expand phase and the future P0.6.18 runtime wiring skeleton.

## 2. Explicitly not implemented

P0.6.17 does not introduce:

```text
production AS2GateController module in synapse/
runtime feature flag implementation
runtime wiring
production Host providers
production Provider Protocols
Contract phase / legacy alias removal
PENDING_RESET state
degraded mode
AgentRuntime / Environment changes
CAS/storage I/O
projection call from bridge
AgentSnapshot construction inside bridge
Integrate / Dream / CVM wiring
```

## 3. Feature gate design posture

AS2 runtime wiring remains disabled by default. Any future runtime skeleton must
be guarded by the gate defined here and must not treat AS2 runtime wiring as a
normal always-on path.

The gate is a **state machine**, not a boolean. Boolean gating is insufficient
because P0.6.15 and P0.6.16 established distinct operational outcomes:

```text
single-agent bad payload -> scoped quarantine
systemic provider/wiring failure -> global sticky disable
operator emergency action -> explicit override
safe default -> disabled by default
```

## 4. AS2WiringGateState

P0.6.17 defines exactly five states:

```text
DISABLED_BY_DEFAULT
ENABLED_FOR_TEST
DISABLED_AGENT_QUARANTINE
DISABLED_SYSTEMIC
DISABLED_OPERATOR_OVERRIDE
```

`PENDING_RESET` is intentionally excluded from P0.6.17. The required causal
chain is already explicit through `DISABLED_OPERATOR_OVERRIDE` and
`DISABLED_BY_DEFAULT`.

### 4.1 State meanings

| State | Meaning |
|---|---|
| `DISABLED_BY_DEFAULT` | Safe default. AS2 runtime wiring is inactive until explicitly enabled by an operator/test control plane action. |
| `ENABLED_FOR_TEST` | AS2 runtime wiring may be exercised by a future skeleton under explicit gate control. This state is not a production-open claim. |
| `DISABLED_AGENT_QUARANTINE` | A single-agent failure has been scoped to the affected agent. Global wiring availability is not automatically revoked. |
| `DISABLED_SYSTEMIC` | A systemic provider/wiring failure has disabled AS2 runtime wiring globally. This state is sticky. |
| `DISABLED_OPERATOR_OVERRIDE` | An operator has explicitly acknowledged or invoked an override/kill-switch state. This state is sticky until reset to safe default. |

## 5. Explicit transition table

No implicit transitions are allowed.

```text
DISABLED_BY_DEFAULT
  -> ENABLED_FOR_TEST
     [operator: request_enable_for_test]

ENABLED_FOR_TEST
  -> DISABLED_AGENT_QUARANTINE
     [trigger: single-agent bad payload]

  -> DISABLED_SYSTEMIC
     [trigger: systemic provider failure]

  -> DISABLED_OPERATOR_OVERRIDE
     [trigger: emergency kill]

DISABLED_AGENT_QUARANTINE
  -> ENABLED_FOR_TEST
     [auto: quarantine resolved]

  -> DISABLED_SYSTEMIC
     [trigger: escalation]

DISABLED_SYSTEMIC [STICKY]
  -> DISABLED_OPERATOR_OVERRIDE
     [operator: acknowledge_systemic_failure]

DISABLED_OPERATOR_OVERRIDE [STICKY]
  -> DISABLED_BY_DEFAULT
     [operator: reset_to_disabled_by_default]
```

### 5.1 Forbidden transitions

The following transitions are explicitly forbidden:

```text
DISABLED_SYSTEMIC -> ENABLED_FOR_TEST
DISABLED_SYSTEMIC -> any auto-reset
any implicit state transition
```

Systemic disable must never silently recover through a health check, timeout, or
background retry. Any future recovery path must move through an explicit operator
action and return to the safe default before re-enabling.

## 6. Sticky systemic disable

`DISABLED_SYSTEMIC` is sticky. It requires explicit operator acknowledgement and
reset. This is required for:

- fail-closed posture;
- deterministic replay reasoning;
- forensic traceability;
- prevention of flapping or metastable recovery loops;
- clear separation of health reporting from re-enable authority.

Health checks may report that dependencies are healthy again, but they must not
transition the gate from `DISABLED_SYSTEMIC` to an enabled state.

## 7. AS2GateController operator-control contract

P0.6.17 defines `AS2GateController` only as an RFC-level operator-control
contract. It is **not** implemented in `synapse/` in this patch.

The controller is the only approved conceptual entrypoint for operator actions.
It is not part of the bridge, adapter, interpreter, or AgentRuntime.

Conceptual operations:

```text
AS2GateController.request_enable_for_test(reason, operator_identity)
AS2GateController.request_operator_override(reason, operator_identity)
AS2GateController.acknowledge_systemic_failure(reason, operator_identity)
AS2GateController.reset_to_disabled_by_default(reason, operator_identity)
```

### 7.1 Controller boundary rules

A future implementation of this controller must not:

```text
import AgentRuntime
import Environment
read isolated interpreter memory
call project_validated_as2_inputs(...)
construct AgentSnapshot
read actor mailboxes
read scheduler/timer/socket/process handles
perform CAS/storage I/O unless explicitly authorized by a later RFC
```

Operator reset is a Host Control Plane event. It is not an interpreter memory
mutation and must not be implemented by reaching into the runtime execution
state.

## 8. Transition audit record requirement

Every future gate transition must emit or materialize an auditable transition
record. P0.6.17 specifies the requirement only; it does not implement storage,
telemetry, or I/O.

Minimum transition record fields:

```text
from_state
to_state
trigger
reason_code
scope: global | agent
agent_id, when scoped
operator_identity, when operator-triggered
correlation_id or request_id
timestamp_source
```

The timestamp source must be explicit in future implementation designs so that
replay/forensic systems can distinguish event ordering from wall-clock recovery
logic.

## 9. Failure strategy binding

The P0.6.17 gate preserves the P0.6.15 failure strategy:

```text
single-agent bad payload
  -> DISABLED_AGENT_QUARANTINE
  -> affected agent only
  -> global wiring remains eligible for ENABLED_FOR_TEST

systemic provider/wiring failure
  -> DISABLED_SYSTEMIC
  -> global sticky disable
  -> no AS2 runtime projection attempts until operator path resets to safe default
```

## 10. Degraded mode exclusion

Degraded mode is explicitly excluded from P0.6.17.

Forbidden in this RFC:

```text
partial AgentSnapshot construction
fallback to AgentRuntime.to_dict()
partial memory_ref_source with fallback_policy
running AS2 with missing canonical inputs
silent downgrade to legacy runtime data
```

Degraded mode may only be considered in a future standalone RFC after runtime
wiring skeleton/hardening and after the project defines formal semantics for
partial state, replay, capability restriction, and audit behavior.

## 11. Legacy alias status after P0.6.16

`legacy_agent_runtime_to_dict` remains only a deprecated compatibility alias
from P0.6.16 Expand phase. New primary tests and fixtures must use
`model_selection_source`. Legacy usage is only acceptable in isolated
compatibility tests or future migration-audit tests.

Contract phase / legacy alias removal remains deferred to a future patch after
migration evidence.

## 12. Boundary hardening added in P0.6.17

P0.6.17 adds a shared AST-based boundary guard:

```text
tests/support/as2_boundary_guards.py
tests/test_as2_architectural_fitness.py
```

The production scan scope is intentionally narrow to avoid false positives in
unrelated modules:

```text
synapse/agent_snapshot_adapter.py
synapse/agent_snapshot_bridge.py
```

Forbidden imports:

```text
synapse.agent_runtime
synapse.environment
synapse.interpreter
synapse.actor_runtime
```

Forbidden bridge calls:

```text
project_validated_as2_inputs
AgentSnapshot
```

The guard detects:

```text
ast.Import
ast.ImportFrom
ast.Call with ast.Name
ast.Call with ast.Attribute
```

This means it catches both direct calls such as `AgentSnapshot(...)` and
attribute calls such as `module.AgentSnapshot(...)`.

Call checks are applied to the bridge boundary. The standalone adapter retains
its approved synthetic projection function and is therefore checked for legacy
runtime imports, not for its internal projection function definition.

## 13. Roadmap

```text
P0.6.14 — Runtime Wiring Design RFC              done
P0.6.15 — Runtime Wiring Harness                 done
P0.6.16 — Naming Debt Cleanup / Expand phase     done
P0.6.17 — Runtime Feature Gate RFC + Boundary Hardening
P0.6.18 — Runtime Wiring Skeleton under explicit gate
P0.6.19 — Runtime Wiring Hardening
P0.6.20 — Contract phase / legacy alias removal
```

P0.6.17 and P0.6.18 must not be merged. The feature gate semantics and boundary
fitness tests must exist before the first runtime wiring skeleton is introduced.
