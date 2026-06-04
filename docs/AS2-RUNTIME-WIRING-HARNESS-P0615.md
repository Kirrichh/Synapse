# AS2 Runtime Wiring Harness — P0.6.15

**Patch:** Alpha3g P0.6.15  
**Status:** TEST-ONLY HARNESS  
**Scope:** Host Provider mock ports, Host Pre-Stage payload assembly, bridge preparation/validation checks, boundary enforcement, and failure-policy simulation.  
**Production runtime code:** NOT AUTHORIZED.  
**Projection:** NOT AUTHORIZED.  
**AgentSnapshot construction:** NOT AUTHORIZED.  
**Runtime feature flag system:** NOT AUTHORIZED.

---

## 1. Purpose

P0.6.15 converts the P0.6.14 runtime wiring design into an executable test-only contract.

The harness proves that the approved Host Pre-Stage responsibility map can be expressed as deterministic provider ports and mocked Host payloads before any real runtime wiring is introduced.

Approved test graph:

```text
Mock Host Provider Protocols
  -> Mock Provider implementations
  -> MockHostPrestagePayloadBuilder
  -> Host Pre-Stage payload
  -> prepare_as2_inputs_from_host_prestage(payload)
  -> PreparedAS2Inputs
  -> validation boundary
```

P0.6.15 intentionally stops at `PreparedAS2Inputs`. It does not project those inputs into an `AgentSnapshot`.

---

## 2. Implemented test-only artifacts

P0.6.15 adds:

```text
tests/support/as2_runtime_wiring_harness.py
tests/fixtures/as2_runtime_wiring/p0615_runtime_wiring_contract.json
tests/test_as2_runtime_wiring_harness_p0615.py
```

No `synapse/` production code is changed.

---

## 3. Provider ports

The harness defines test-scope provider protocols only:

```text
IdentityProviderProtocol
DefinitionProviderProtocol
ModelRegistryProviderProtocol
MemoryExternalizationProtocol
CapabilityGrantManifestProtocol
ModelSelectionProtocol
```

These protocols are not production APIs. They are executable test contracts for the P0.6.14 Host responsibility map.

Each protocol has a matching mock provider:

```text
MockHostIdentityProvider
MockHostDefinitionProvider
MockStaticModelRegistryProvider
MockMemoryExternalizationProvider
MockCapabilityGrantManifestProvider
MockModelSelectionProvider
```

The mock providers return canonical AS2-ready payload fragments. They do not expose legacy shortcuts, live callables, runtime envelopes, inline memory, or storage handles.

---

## 4. Payload builder

`MockHostPrestagePayloadBuilder` assembles only production success keys:

```text
adapter_identity_context
adapter_definition_source
static_model_registry
memory_ref_source
capability_grant_source
model_selection_source
```

The builder must not emit:

```text
model_selector
host_stage_failure
forbidden_runtime_reads
inline_memory_payload
runtime envelope keys
legacy runtime fields
```

The compatibility alias `model_selector` remains accepted by the existing bridge for previous fixtures, but P0.6.15 harness payloads use `model_selection_source`.

---

## 5. Required checks

P0.6.15 covers:

```text
- provider protocols are implemented by the mock providers;
- Host Pre-Stage payload builder emits only production success keys;
- bridge preparation returns PreparedAS2Inputs for valid mocked payloads;
- identical mocked payloads produce identical PreparedAS2Inputs;
- missing production inputs fail closed;
- test/failure modelling keys are not happy-path production inputs;
- unknown runtime-envelope keys are rejected;
- support harness has no forbidden runtime imports;
- support harness does not call projection or construct AgentSnapshot;
- single-agent bad payload quarantines only that agent in harness simulation;
- systemic provider failure disables AS2 runtime wiring globally in harness simulation.
```

---

## 6. Failure-policy simulation

P0.6.15 simulates only the two P0.6.14-approved failure classes.

### 6.1 Single-agent bad payload

```text
one agent payload invalid
  -> only that agent is marked quarantine_agent
  -> other valid agents still prepare successfully
  -> global harness outcome remains prepared
```

### 6.2 Systemic provider failure

```text
shared provider unavailable before payload exists
  -> global harness outcome is disable_wiring_globally
  -> bridge preparation is not called
  -> runtime feature flag state is not mutated
```

This is a test-harness outcome model only. It is not a production feature flag system.

---

## 7. Locked boundaries

The following remain locked after P0.6.15:

```text
AgentRuntime imports
Environment imports
interpreter imports
actor_runtime imports
AgentRuntime.to_dict() migration
Environment._json_safe() migration
project_validated_as2_inputs(...) inside bridge
AgentSnapshot construction inside bridge
production Host provider implementation
production Provider Protocols
runtime feature flag system
CAS/storage I/O
degraded mode
naming debt cleanup
Integrate/Dream/CVM wiring
real runtime wiring
```

---

## 8. Deferred work

P0.6.15 does not close naming debt. The following remain candidates for a dedicated expand-contract refactor:

```text
legacy_agent_runtime_to_dict parameter naming
model_selector compatibility alias removal
bridge docstring drift
production/test key separation in future payload schema
```

Degraded mode is also deferred. It requires a separate RFC because it changes runtime failure semantics, replay posture, and capability/memory availability guarantees.

---

## 9. Recommended next sequence

```text
P0.6.14 — Runtime Wiring Design RFC
P0.6.15 — Runtime Wiring Harness / Host Provider Mocks
P0.6.16 — Naming Debt Cleanup / Expand-Contract Refactor
P0.6.17 — Runtime Feature Gate RFC / Implementation Boundary
P0.6.18 — Runtime Wiring Implementation under explicit gate
```
