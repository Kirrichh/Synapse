# AS2 Legacy Bridge Design RFC / Host Pre-Stage Protocol

**Patch:** Alpha3g P0.6.10  
**Status:** DESIGN — DOC-ONLY  
**Scope:** Bridge design, Host Pre-Stage Protocol, readiness checklist, forbidden reads registry.  
**Runtime code:** NOT AUTHORIZED.  
**Bridge implementation:** LOCKED until a future explicit team vote.

---

## 1. Purpose

This document defines the design boundary for connecting the legacy runtime world to the already approved standalone AS2 path.

The standalone AS2 path is already available as a quarantined, explicit-input flow:

```text
explicit AS2 inputs
  -> validate_as2_inputs(...)
  -> project_validated_as2_inputs(...)
  -> AgentSnapshot + AdapterDerivationRecord
```

P0.6.10 does **not** implement a bridge. It defines the future bridge design and the Host Pre-Stage Protocol required before any bridge code may be authorized.

---

## 2. Non-goals

P0.6.10 does not authorize any of the following:

```text
synapse/ code changes
tests/ fixture or harness changes
AgentRuntime imports
Environment imports
runtime wiring
feature flag implementation
to_agent_snapshot()
AgentRuntime.to_dict() canonical usage
Environment._json_safe() changes
bridge implementation
bridge fixture corpus
profile selector
real provider registry
FunctionDescriptor runtime registry
Integrate / Dream / CVM integration
```

---

## 3. Airlock Pattern

The future bridge follows an **Airlock Pattern**.

The bridge is not a direct converter:

```text
AgentRuntime -> AgentSnapshot
```

That path remains forbidden.

The approved design is one-way and staged:

```text
Host / Runtime context
  -> Host Pre-Stage Protocol
  -> explicit AS2 inputs
  -> validate_as2_inputs(...)
  -> project_validated_as2_inputs(...)
  -> AgentSnapshot + AdapterDerivationRecord
```

The bridge must not move data back from canonical AS2 state into the legacy runtime. It prepares explicit AS2 inputs for the already isolated AS2 validation/projection path.

---

## 4. Future bridge entrypoint reservation

The future bridge entrypoint name is reserved as:

```python
prepare_as2_inputs_from_host_prestage(...)
```

This name is intentional:

```text
prepare         -> the bridge prepares inputs; it does not project snapshots
as2_inputs      -> output is explicit AS2 input objects
host_prestage   -> source data has already passed Host Pre-Stage Protocol
```

The following names remain forbidden for AS2 bridge surface:

```python
to_agent_snapshot(...)
convert_agent_runtime(...)
snapshot_from_runtime(...)
adapt_legacy_agent(...)
```

Reason: those names imply direct conversion from legacy runtime object to canonical snapshot, which contradicts the AS2 explicit-input design.

---

## 5. Host Pre-Stage Protocol

The Host Pre-Stage Protocol is the required boundary before any future bridge call.

### Step 0 — Host Capability Verification

Before preparing AS2 inputs, Host must verify that it has authority and deterministic sources for the conversion boundary.

Host must verify:

```text
- authority over the AgentRuntime instance being staged;
- deterministic identity source;
- deterministic definition source;
- access to canonical memory storage for externalization;
- declarative capability grant manifest;
- selected StaticModelRegistry snapshot;
- no dependency on live runtime introspection for AS2 canonical values.
```

If Step 0 fails, Host must fail closed before any AS2 inputs are constructed.

Step 0 failures are Host pre-stage failures, not AS2 adapter failures.

---

### Step 1 — Construct AdapterIdentityContext

Host constructs `AdapterIdentityContext` from deterministic identity inputs.

Forbidden identity sources:

```text
AgentRuntime.name as agent_id seed
AgentRuntime.to_dict() output
Environment._json_safe() output
hidden interpreter state
wall-clock time
UUID generation
runtime mailbox or scheduler state
```

`legacy name` may be preserved only as a non-canonical alias/routing hint when explicitly represented by the approved AS2 identity schema.

---

### Step 2 — Construct AdapterDefinitionSource

Host constructs `AdapterDefinitionSource` for:

```text
AgentDefinitionRef
config
canonical_fields
```

This source must be explicit and deterministic. It must not be inferred from legacy serialization.

---

### Step 3 — Select StaticModelRegistry snapshot

Host selects the StaticModelRegistry snapshot used to map legacy model identifiers into AS2 model references.

Rules:

```text
- registry snapshot must be explicit;
- registry selection must be deterministic;
- unknown models fail closed;
- real provider metadata is not queried dynamically;
- provider lookup is not performed by the bridge.
```

---

### Step 4 — Externalize legacy memory

Host externalizes legacy memory before invoking the bridge.

Host responsibilities:

```text
- derive or obtain approved memory-space policy version;
- externalize legacy memory into canonical storage / CAS;
- obtain canonical memory references;
- construct MemoryRefSource;
- provide expected memory-space boundary for AS2 validation.
```

Bridge responsibilities:

```text
- accept already prepared MemoryRefSource;
- pass MemoryRefSource to AS2 validation/projection;
- never write to storage;
- never mint memory refs from inline memory dumps;
- never repair, filter, or normalize memory refs.
```

If memory externalization I/O fails during Host preparation, the failure is a Host pre-stage failure.

Reserved failure names for future design:

```text
HostPreStageIOError
HostMemoryExternalizationError
```

These are not AS2 adapter errors because AS2 validation/projection has not started.

---

### Step 5 — Construct MemoryRefSource

Host constructs `MemoryRefSource` from the result of successful memory externalization.

Missing memory source and empty memory are distinct:

```text
empty memory -> explicit empty MemoryRefSource refs list
missing source -> fail closed before AS2 projection
```

---

### Step 6 — Construct CapabilityGrantSource

Host constructs `CapabilityGrantSource` from declarative grants.

Allowed source:

```text
declarative capability grant manifest
```

Forbidden sources:

```text
AgentRuntime.tools
AgentRuntime.tools.keys()
callable inspection
Python signatures
decorators
runtime tool registry
```

If declarative grants are unavailable, Host must not attempt to synthesize them from live tools.

---

### Step 7 — Reject live callable introspection

Any attempt to derive AS2 grants from live Python callables is an ambient-authority violation.

The future bridge must treat this as fail-closed behavior and route it into the existing AS2 error surface only after explicit AS2 input construction has begun. If the violation occurs before AS2 input construction, it remains a Host pre-stage failure.

---

### Step 8 — Reject AgentRuntime.to_dict() as canonical input

`AgentRuntime.to_dict()` must not be used as canonical source material.

It may remain a legacy diagnostic/export surface, but it is not authoritative for AS2.

Reason:

```text
- legacy shape is narrow;
- legacy shape is not canonical;
- identity data is incomplete;
- memory is inline;
- capability grants are absent;
- envelope shape conflicts with AS2 canonical output.
```

---

### Step 9 — Validate explicit AS2 inputs

After Host constructs explicit inputs, the bridge may pass them to:

```python
validate_as2_inputs(...)
```

Validation failures must use the existing AS2 typed errors and AS2ViolationContext.

---

### Step 10 — Hand off validated inputs to Host/Pipeline projection

Only after validation succeeds, the future **Host/Pipeline** may pass the prepared explicit AS2 inputs to:

```python
project_validated_as2_inputs(...)
```

This produces:

```text
AgentSnapshot
AdapterDerivationRecord
```

The bridge must not bypass validation and must not call projection itself. The bridge returns prepared AS2 inputs; Host/Pipeline owns orchestration of projection. This P0.6.14 clarification supersedes the earlier P0.6.10 wording that allowed the future bridge to pass inputs to projection.

---

## 6. Host responsibilities vs Bridge responsibilities

| Responsibility | Host | Bridge |
|---|---:|---:|
| Own runtime/pre-stage authority | yes | no |
| Read raw legacy runtime state | yes, under Host rules | no direct canonical read |
| Perform storage/CAS I/O | yes | no |
| Externalize memory | yes | no |
| Select registry snapshot | yes | consumes only |
| Prepare declarative grants | yes | consumes only |
| Construct explicit AS2 input objects | yes / future bridge boundary | yes, only from Host-prepared artifacts |
| Validate AS2 inputs | no / delegated | yes |
| Project AS2 inputs | yes, Host/Pipeline orchestration | no |
| Read live tools | no for canonical grants | forbidden |
| Use `AgentRuntime.to_dict()` as canonical source | forbidden | forbidden |

---

## 7. Forbidden Reads Registry

The following registry is normative for future bridge implementation review.

| Runtime source | Forbidden use | Reason | AS2 boundary |
|---|---|---|---|
| `AgentRuntime.to_dict()` | canonical AS2 source | legacy export is incomplete and non-canonical | explicit AS2 inputs only |
| `AgentRuntime.name` | `agent_id` seed source | legacy name is alias/routing hint, not identity | `AdapterIdentityContext` |
| `AgentRuntime.model` | direct `model_ref` source | bare string requires registry mapping | `StaticModelRegistry` |
| `AgentRuntime.memory` | direct snapshot memory | inline dumps are forbidden | `MemoryRefSource` |
| `AgentRuntime.tools` | capability source | live callable registry is ambient authority | `CapabilityGrantSource` |
| `AgentRuntime.tools.keys()` | grant derivation | namespace listing is runtime introspection | declarative grants only |
| `AgentRuntime.llm` | canonical input | live backend handle is runtime envelope | out of AS2 state |
| `AgentRuntime.env` | canonical input | environment handle is runtime envelope | out of AS2 state |
| `Environment._json_safe()` | AS2 envelope source | legacy envelope conflicts with AS2 envelope isolation | AS2 envelope only |
| interpreter hidden state | AS2 input source | hidden state is ambient authority | explicit inputs only |
| actor runtime mailbox | snapshot input | mailbox is runtime envelope | out of AS2 state |
| scheduler timers | snapshot input | timers are runtime envelope | out of AS2 state |
| sockets / process handles | snapshot input | live handles are non-canonical | out of AS2 state |

Future bridge review must reject any implementation that reads these sources for canonical AS2 values.

---

## 8. Feature flag reservation

Future bridge implementation must be feature-flagged.

Reserved flag name:

```text
AS2_HOST_PRESTAGE_BRIDGE_ENABLED
```

P0.6.10 does not introduce this flag in code.

Future requirements:

```text
- default disabled;
- activation requires explicit team vote;
- implementation patch must document all call sites;
- legacy runtime must not call bridge without flag guard.
```

---

## 9. Security considerations

### 9.1 Ambient authority

The bridge must not infer authority from object reachability. Having access to `AgentRuntime` does not mean the bridge may derive identity, memory refs, model refs, or capability grants from arbitrary runtime fields.

### 9.2 Memory contamination

Host must externalize memory before AS2 input construction. AS2 validation then verifies memory-space boundaries. The bridge must not repair mixed or foreign memory refs.

### 9.3 Capability inflation

Bridge must not inflate capabilities by inspecting live tools. Grants must come from a declarative source.

### 9.4 Envelope collision

The AS2 path must not reuse the legacy `{"__type__": "agent", "data": ...}` envelope.

### 9.5 Host I/O failure

Host pre-stage I/O failure is not an AS2 adapter failure. It must not result in partial AS2 inputs.

---

## 10. Readiness checklist for future bridge code

Bridge implementation remains locked until all items below are complete.

```text
[ ] P0.6.10 bridge design approved.
[ ] Host Pre-Stage Protocol accepted.
[ ] Forbidden Reads Registry accepted.
[ ] Memory externalization ownership assigned to Host.
[ ] CapabilityGrantSource ownership assigned to Host/declarative manifest.
[ ] Feature flag name reserved.
[ ] P0.6.11 bridge fixture corpus approved.
[ ] Bridge fixture harness covers Host pre-stage success path.
[ ] Bridge fixture harness covers Host pre-stage failure paths.
[ ] Separate team vote authorizes bridge implementation.
```

---

## 11. Future staging

Approved staging after P0.6.10:

```text
P0.6.10 — Bridge Design RFC / Host Pre-Stage Protocol (doc-only)
P0.6.11 — Bridge Fixture Corpus / Host Pre-Stage Harness (test-only/data)
P0.6.12 — Flagged Bridge Implementation (completed, local flag disabled by default)
P0.6.13 — Host Pre-Stage Bridge Hardening (completed)
P0.6.14 — Runtime Wiring Design RFC (doc-only)
```

No bridge code is authorized by P0.6.10.

---

## 12. Acceptance criteria for P0.6.10

P0.6.10 is accepted when:

```text
[ ] This design document is added.
[ ] Airlock Pattern is defined.
[ ] Host Pre-Stage Protocol includes Step 0 and Steps 1-10.
[ ] Host and Bridge responsibilities are separated.
[ ] Memory externalization is assigned to Host.
[ ] Bridge storage/CAS I/O remains forbidden.
[ ] CapabilityGrantSource is declarative only.
[ ] Live tool introspection is forbidden.
[ ] AgentRuntime.to_dict() is forbidden as canonical source.
[ ] Forbidden Reads Registry is present.
[ ] AS2_HOST_PRESTAGE_BRIDGE_ENABLED is reserved, not implemented.
[ ] P0.6.11/P0.6.12 staging is documented.
[ ] synapse/ remains unchanged.
[ ] tests/ remains unchanged.
```

---

## 13. P0.6.11 Bridge Fixture Corpus / Host Pre-Stage Harness

P0.6.11 materializes this design as a test-only/data fixture corpus. It does not implement bridge code.

Artifacts:

```text
tests/fixtures/as2_bridge/
tests/test_as2_bridge_harness_p0611.py
```

### 13.1 Fixture schema

All bridge fixtures use:

```text
alpha3g.as2_bridge_fixture.v1
```

Required top-level fields:

```text
schema_version
case_id
description
polarity
bridge_stage
legacy_runtime_shape
host_prestage_outputs
expected_as2_inputs
forbidden_reads
protocol_steps
expected_error
expected_error_context
rationale
```

`legacy_runtime_shape` is documentary only. It must be a mocked JSON dictionary and must not be treated as canonical AS2 input.

### 13.2 Naming debt: `legacy_agent_runtime_to_dict`

Current standalone AS2 validation requires a model selector through the existing key:

```text
legacy_agent_runtime_to_dict.model
```

P0.6.11 positive bridge fixtures satisfy this current API with a synthetic selector payload:

```json
{
  "legacy_agent_runtime_to_dict": {
    "_note": "synthetic model selector for current validate_as2_inputs API; does not authorize AgentRuntime.to_dict() as canonical source",
    "model": "mock-agent-model"
  }
}
```

This is naming debt, not bridge authorization. It does not allow `AgentRuntime.to_dict()` as a canonical source. A future implementation-planning patch may rename this selector to a clearer `model_selection_source` or equivalent after explicit team approval.

### 13.3 Positive bridge fixtures

P0.6.11 adds four positive fixtures:

```text
positive_bridge_minimal_host_prestage.json
positive_bridge_empty_memory_refs.json
positive_bridge_no_live_tools_with_empty_grants.json
positive_bridge_minimal_host_prestage_with_audit_context.json
```

For these fixtures, `expected_as2_inputs` must validate through the existing standalone AS2 `validate_as2_inputs(...)` boundary. The bridge harness must not call `project_validated_as2_inputs(...)`.

### 13.4 Negative bridge fixtures

P0.6.11 adds twelve negative fixtures covering Host Pre-Stage failure paths and forbidden reads:

```text
negative_bridge_uses_agentruntime_todict.json
negative_bridge_reads_live_tools.json
negative_bridge_inline_memory_not_externalized.json
negative_bridge_missing_identity_source.json
negative_bridge_missing_definition_source.json
negative_bridge_missing_model_registry.json
negative_bridge_missing_capability_grants.json
negative_bridge_host_io_failure_during_memory_externalization.json
negative_bridge_environment_json_safe_used.json
negative_bridge_actor_mailbox_used_as_snapshot_input.json
negative_bridge_uses_agentruntime_name_as_identity.json
negative_bridge_uses_agentruntime_model_as_model_ref.json
```

Bridge-specific expected errors are string identifiers only. P0.6.11 does not introduce Python exception classes for bridge failures.

### 13.5 Coverage requirements

P0.6.11 requires:

```text
Forbidden Reads Registry coverage: 100%
Host Pre-Stage Protocol Step 0-10 coverage: 100%
```

Step 10 is covered by matrix/fixture readiness only. Bridge harness execution of `project_validated_as2_inputs(...)` remains forbidden.

### 13.6 Scope discipline

P0.6.11 remains test-only/data + docs. The following remain locked:

```text
synapse/ changes
AgentRuntime imports
Environment imports
runtime wiring
feature flag implementation
bridge code
project_validated_as2_inputs(...) calls in bridge harness
to_agent_snapshot()
Python bridge exception classes
real legacy runtime objects
real I/O
```

## P0.6.12 implementation status — Flagged Host Pre-Stage Bridge Skeleton

P0.6.12 implements the first bridge-code skeleton under the approved Host Pre-Stage boundary.

Implemented module:

```text
synapse/agent_snapshot_bridge.py
```

Implemented entrypoint:

```text
prepare_as2_inputs_from_host_prestage(payload)
```

The entrypoint accepts Host Pre-Stage mappings, not `AgentRuntime` or `Environment` instances. It parses the mapping into frozen DTOs, constructs `PreparedAS2Inputs`, and validates those inputs through the existing standalone AS2 `validate_as2_inputs(...)` boundary.

The bridge module uses a local disabled-by-default guard:

```text
AS2_HOST_PRESTAGE_BRIDGE_ENABLED = False
```

Tests enable the guard explicitly. P0.6.12 does not introduce runtime feature-flag system integration.

### P0.6.12 DTO boundary

```text
Host Pre-Stage Mapping -> HostPreStagePayload -> PreparedAS2Inputs -> validate_as2_inputs(...)
```

`PreparedAS2Inputs` is data-only. It does not construct `AgentSnapshot` and does not call `project_validated_as2_inputs(...)`.

### Naming debt isolation

The bridge public/data boundary uses:

```text
model_selection_source
```

The current standalone AS2 validation API still expects:

```text
legacy_agent_runtime_to_dict.model
```

P0.6.12 isolates this naming debt inside `PreparedAS2Inputs.to_validate_kwargs()`. This does not authorize `AgentRuntime.to_dict()` as canonical source.

### Bridge-local errors

P0.6.12 adds bridge-local errors rooted at `AS2BridgeError`. Host Pre-Stage failures are kept separate from standalone AS2 validation errors.

### Still locked after P0.6.12

```text
AgentRuntime imports
Environment imports
runtime wiring
Environment._json_safe() migration
AgentRuntime.to_dict() migration
runtime profile selector
project_validated_as2_inputs(...) call inside bridge
AgentSnapshot construction inside bridge
Integrate / Dream / CVM integration
real provider registry
FunctionDescriptor runtime registry
golden fixture migration
validate_as2_inputs(...) parameter rename
```


---

## P0.6.13 hardening status — Host Pre-Stage Bridge Boundary

P0.6.13 hardens the first bridge-code boundary introduced by P0.6.12. It does not expand bridge responsibility: the bridge still prepares and validates explicit AS2 inputs only.

### Strict Host Pre-Stage payload contract

Host Pre-Stage payloads are now treated as a closed contract. Unknown top-level fields are rejected with `HostPreStageUnexpectedFieldError` instead of being silently ignored.

Approved top-level payload fields remain:

```text
adapter_identity_context
adapter_definition_source
static_model_registry
memory_ref_source
capability_grant_source
model_selection_source / model_selector
host_stage_failure
forbidden_runtime_reads
inline_memory_payload
notes
```

Unknown nested fields are also rejected for AS2 boundary structures where the bridge can determine the approved field set. This covers identity seed leakage, memory-ref inline data, capability-grant live callable markers, registry shape drift, and definition-ref shape drift.

### Missing / null / empty semantics

P0.6.13 fixes Host Pre-Stage diagnostics to distinguish source absence from invalid source shape:

| Payload condition | Bridge behavior |
|---|---|
| required source key missing | specific `HostPreStageMissing*Error` |
| required source value is `null` | specific `HostPreStageMissing*Error` |
| required source value is `{}` | `HostPreStageInvalidAS2InputsError` via standalone AS2 validation |
| required source value is wrong type | `HostPreStageInvalidAS2InputsError` |
| unknown field present | `HostPreStageUnexpectedFieldError` |

### Mutation safety

Bridge DTOs defensively freeze nested host-prestage structures. `PreparedAS2Inputs.to_validate_kwargs()` returns fresh mutable dict/list copies for the existing standalone `validate_as2_inputs(...)` API. External mutation of the original Host payload after bridge preparation must not change the prepared AS2 input payload.

### Scope locks after P0.6.13

Still locked:

```text
project_validated_as2_inputs(...) calls inside bridge
AgentSnapshot construction inside bridge
AgentRuntime / Environment / interpreter / actor_runtime imports
runtime wiring
runtime feature flag system
production environment poison pill
legacy_agent_runtime_to_dict rename
bridge fixture schema v2
caching / performance optimization
```

---

## 15. P0.6.14 Runtime Wiring Design Addendum

P0.6.14 adds a dedicated runtime wiring design document:

```text
docs/AS2-RUNTIME-WIRING-DESIGN.md
```

This addendum clarifies the post-P0.6.13 bridge lifecycle:

```text
Bridge responsibility: prepare and validate Host Pre-Stage payloads.
Host/Pipeline responsibility: orchestrate projection after bridge success.
Projection call inside bridge: forbidden.
AgentSnapshot construction inside bridge: forbidden.
```

The approved execution graph is:

```text
Host authorized providers
  -> Host Pre-Stage payload
  -> prepare_as2_inputs_from_host_prestage(payload)
  -> PreparedAS2Inputs
  -> Host/Pipeline calls project_validated_as2_inputs(...)
  -> AgentSnapshot + AdapterDerivationRecord
```

P0.6.14 also classifies current Host Pre-Stage payload keys into production success inputs, test/failure modelling keys, compatibility aliases, and diagnostic notes. Runtime wiring must not treat test/failure modelling keys as production payload data.

Runtime wiring code remains locked until a future explicit team vote.
