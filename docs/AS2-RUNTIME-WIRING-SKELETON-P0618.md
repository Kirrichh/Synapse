# P0.6.18 — AS2 Runtime Wiring Skeleton under ENABLED_FOR_TEST Gate

Status: **SKELETON / TEST-GATED CODE**

P0.6.18 introduces the first runtime-owned AS2 wiring skeleton. The patch is
intentionally limited to preparation and validation. It does not activate
production AS2 runtime wiring, does not implement a production `AS2GateController`,
does not persist gate state, does not call projection, and does not construct
`AgentSnapshot`.

## Implemented Module

```text
synapse/runtime/as2_runtime_wiring.py
```

The module is the P0.6.18 data-plane skeleton. It consumes an already assembled
Host Pre-Stage payload and returns immutable typed outcomes for a future
control-plane caller.

## Skeleton Pipeline

```text
Host Pre-Stage payload
  → AS2WiringGateEvaluator(gate_state)
  → only ENABLED_FOR_TEST proceeds
  → prepare_as2_inputs_from_host_prestage(payload)
  → validate_as2_inputs(**prepared.to_validate_kwargs())
  → typed immutable outcome
```

The skeleton stops before:

```text
project_validated_as2_inputs(...)
AgentSnapshot construction
runtime execution
CAS/storage I/O
production Host provider access
```

## Gate State

P0.6.18 mirrors the five-state P0.6.17 RFC model:

```text
DISABLED_BY_DEFAULT
ENABLED_FOR_TEST
DISABLED_AGENT_QUARANTINE
DISABLED_SYSTEMIC
DISABLED_OPERATOR_OVERRIDE
```

`ENABLED` is intentionally absent. The skeleton can only proceed when the caller
supplies `ENABLED_FOR_TEST`.

## Gate Evaluator

`AS2WiringGateEvaluator` is a skeleton-level evaluator, not a production
`AS2GateController`.

It:

- receives gate state as an explicit dependency;
- returns `None` when the skeleton may proceed;
- returns `WiringGateClosed` when the gate is not `ENABLED_FOR_TEST`;
- does not mutate, persist, transition, or audit gate state.

## Typed Outcomes

P0.6.18 uses frozen dataclasses, not a bare enum, so each outcome can carry the
context needed by future audit/control-plane logic.

```text
WiringSuccess
  - correlation_id
  - prepared_inputs

WiringGateClosed
  - correlation_id
  - current_state
  - reason

WiringAgentQuarantineRequest
  - correlation_id
  - agent_id
  - reason
  - reason_code

WiringSystemicDisableRequest
  - correlation_id
  - reason
  - reason_code
  - failure_context
```

All outcomes are immutable. `correlation_id` may be supplied by the caller; if it
is omitted, the skeleton generates one locally. In future stages, Host/Pipeline
should pass correlation identity into the skeleton rather than relying on local
UUID generation.

## Failure Classification

P0.6.18 returns declarative outcomes. It does not apply state transitions.

```text
gate_state != ENABLED_FOR_TEST
  → WiringGateClosed

single-agent bad payload / validation input failure
  → WiringAgentQuarantineRequest

bridge disabled / Host Pre-Stage I/O / model-selection conflict / unexpected systemic failure
  → WiringSystemicDisableRequest
```

`SYSTEMIC_DISABLE_REQUESTED` is a request for the future control plane. It is not
state persistence and not a direct transition to `DISABLED_SYSTEMIC`.

## Boundary Guard Update

P0.6.18 extends AS2 architectural fitness tests to include:

```text
synapse/runtime/as2_runtime_wiring.py
```

The skeleton module is checked for forbidden legacy runtime imports and forbidden
projection/snapshot calls. The guard remains AST-based and detects both direct
calls and attribute calls.

Forbidden imports:

```text
synapse.agent_runtime
synapse.environment
synapse.interpreter
synapse.actor_runtime
```

Forbidden calls:

```text
project_validated_as2_inputs
AgentSnapshot
```

The skeleton is allowed to use the existing Host Pre-Stage preparation entrypoint
`prepare_as2_inputs_from_host_prestage(...)` and the standalone adapter validation
entrypoint `validate_as2_inputs(...)`.

## Locked in P0.6.18

The following remain locked:

```text
production AS2GateController
production AS2 runtime activation
production Host providers
production ENABLED state
projection from skeleton or bridge
AgentSnapshot construction inside skeleton or bridge
persisted gate state mutation
CAS/storage I/O
degraded mode
Contract phase / legacy alias removal
AgentRuntime / Environment / interpreter / actor_runtime imports
Integrate/Dream/CVM wiring
```

## Test Result Target

P0.6.18 is accepted only if the full suite remains green and the AS2 boundary
fitness tests include the new skeleton module.

## P0.6.19 hardening note

P0.6.19 hardens this skeleton without changing its production-locked posture:

- `AS2WiringReasonCode` is the canonical reason-code taxonomy for wiring outcomes.
- `WiringBridgeDisabled` distinguishes an open skeleton gate from a disabled bridge safety flag.
- `correlation_id` remains Host-preferred with skeleton root fallback for hardening and fixture execution.
- Static migration evidence now checks that `legacy_agent_runtime_to_dict` stays outside primary test paths.
- Projection, `AgentSnapshot` construction, production Host providers, production `AS2GateController`, persisted gate state mutation, degraded mode, and Contract phase remain locked.


## P0.6.20 Contract update

The AS2 model-selection naming Contract phase is complete. The validation boundary now accepts only `model_selection_source`; the previous compatibility path is removed and protected by a permanent reintroduction guard.
