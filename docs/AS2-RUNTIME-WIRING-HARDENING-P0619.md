# AS2 Runtime Wiring Hardening — P0.6.19

Status: **implemented**  
Patch type: **code + test hardening**  
Production activation: **LOCKED**

P0.6.19 hardens the P0.6.18 runtime-owned AS2 wiring skeleton without
expanding it into production runtime activation.

## Scope

P0.6.19 strengthens:

- reason-code taxonomy for AS2 wiring outcomes;
- bridge safety flag diagnostics;
- correlation-id propagation and fallback root correlation-id behavior;
- negative fixture coverage for gate/bridge/payload failure combinations;
- AS2BoundaryGuard explicit-allowlist enforcement;
- static migration evidence for `legacy_agent_runtime_to_dict`.

## Runtime wiring boundary

The skeleton remains limited to:

```text
Host Pre-Stage payload
  -> AS2WiringGateEvaluator
  -> ENABLED_FOR_TEST only
  -> prepare_as2_inputs_from_host_prestage(payload)
  -> validate_as2_inputs(**prepared.to_validate_kwargs())
  -> typed immutable outcome
```

The skeleton still does **not** call:

```text
project_validated_as2_inputs(...)
AgentSnapshot(...)
```

## Reason-code taxonomy

P0.6.19 introduces `AS2WiringReasonCode` as the stable diagnostic taxonomy for
runtime wiring outcomes.

### Gate closed

```text
GATE_DISABLED_BY_DEFAULT
GATE_DISABLED_SYSTEMIC
GATE_DISABLED_OPERATOR
GATE_DISABLED_QUARANTINE
```

### Bridge safety flag

```text
BRIDGE_SAFETY_DISABLED
```

`BRIDGE_SAFETY_DISABLED` is represented by the dedicated `WiringBridgeDisabled`
outcome. This is distinct from `WiringGateClosed`: the skeleton gate may be
`ENABLED_FOR_TEST`, but the bridge preparation boundary can still be locked by
`AS2_HOST_PRESTAGE_BRIDGE_ENABLED = False`.

### Agent-scoped failure

```text
VALIDATION_FAILED_AGENT_SCOPE
MISSING_IDENTITY_CONTEXT
MALFORMED_PAYLOAD_AGENT
INVALID_CAPABILITY_GRANT
```

### Systemic failure

```text
VALIDATION_FAILED_SYSTEMIC
MODEL_SELECTION_CONFLICT
PROVIDER_UNAVAILABLE
PAYLOAD_CLASSIFICATION_FAILED
UNEXPECTED_PREPARATION_FAILURE
```

Some reason codes are reserved for future fixtures. The hardening suite asserts
that reserved codes are explicit, not accidental gaps.

## Correlation ID policy

P0.6.19 keeps the P0.6.18 skeleton behavior:

```text
Host-supplied correlation_id -> preserved exactly
missing correlation_id       -> skeleton creates a root correlation_id fallback
```

The fallback exists for skeleton and fixture execution. Future production
Host/Pipeline integration should supply a correlation identifier explicitly.

No runtime telemetry, file logging, network logging, or audit storage is added
by P0.6.19.

## Legacy alias migration evidence

P0.6.19 adds a static migration-audit test for:

```text
legacy_agent_runtime_to_dict
```

The alias is still present in the approved expand-phase owner modules and in
explicit compatibility/migration tests. P0.6.19 does **not** execute the Contract
phase and does **not** remove the alias.

The audit is machine-readable evidence for a future Contract decision.

## Boundary hardening

`AS2BoundaryGuard` remains explicit-allowlist based. It does not scan all of
`synapse/runtime/` automatically. The guarded production scope remains targeted:

```text
synapse/agent_snapshot_adapter.py
synapse/agent_snapshot_bridge.py
synapse/runtime/as2_runtime_wiring.py
```

The skeleton is forbidden from importing legacy runtime layers or test support,
and it remains forbidden from calling projection or constructing `AgentSnapshot`.

## Locked invariants

P0.6.19 does not implement or introduce:

```text
project_validated_as2_inputs(...)
AgentSnapshot construction
production AS2GateController
production Host providers
persisted gate state mutation
global gate state mutation
CAS/storage I/O
degraded mode
Contract phase / legacy alias removal
production ENABLED state
LLM/capability execution
Integrate/Dream/CVM wiring
```


## P0.6.20 Contract update

The AS2 model-selection naming Contract phase is complete. The validation boundary now accepts only `model_selection_source`; the previous compatibility path is removed and protected by a permanent reintroduction guard.
