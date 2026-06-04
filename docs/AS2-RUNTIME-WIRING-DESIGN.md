# AS2 Runtime Wiring Design RFC

**Patch:** Alpha3g P0.6.14  
**Status:** DESIGN — DOC-ONLY  
**Scope:** Runtime wiring topology, Host/Pipeline responsibility map, bridge usage contract, failure handling, and readiness gates.  
**Runtime code:** NOT AUTHORIZED.  
**Tests:** NOT AUTHORIZED.  
**Bridge implementation changes:** NOT AUTHORIZED.  
**Runtime wiring:** LOCKED until a future explicit team vote.

---

## 1. Purpose

P0.6.14 defines how the already hardened AS2 Host Pre-Stage bridge may be used by a future runtime pipeline.

The bridge boundary exists in code as:

```python
prepare_as2_inputs_from_host_prestage(payload)
```

The bridge prepares and validates AS2 inputs. It does **not** project those inputs into an `AgentSnapshot`.

P0.6.14 is the design contract for the next runtime-facing stage. It answers:

```text
- which Host providers prepare each required AS2 input;
- which runtime sources remain forbidden;
- where the bridge is called;
- where projection is called;
- how Host/Pipeline handles bridge and adapter failures;
- which debts must be closed before runtime activation.
```

---

## 2. Non-goals

P0.6.14 does not authorize any of the following:

```text
synapse/ code changes
tests/ fixture or harness changes
AgentRuntime imports
Environment imports
interpreter / actor runtime imports
runtime wiring
feature flag system implementation
project_validated_as2_inputs(...) call inside bridge
AgentSnapshot construction inside bridge
AgentRuntime.to_dict() canonical usage
Environment._json_safe() migration
memory CAS / storage I/O implementation
Integrate / Dream / CVM wiring
legacy_agent_runtime_to_dict rename
model_selector removal
bridge fixture schema v2
```

Any future code patch that touches these areas requires a separate team vote and an explicit readiness checklist.

---

## 3. Runtime Wiring Execution Graph

The approved runtime wiring topology is:

```text
Host authorized providers
  -> Host Pre-Stage payload
  -> prepare_as2_inputs_from_host_prestage(payload)
  -> PreparedAS2Inputs
  -> Host/Pipeline calls project_validated_as2_inputs(...)
  -> AgentSnapshot + AdapterDerivationRecord
```

The bridge is a preparation and validation boundary. Projection remains outside the bridge.

### 3.1 Forbidden execution graphs

The following graphs remain forbidden:

```text
AgentRuntime -> AgentSnapshot
AgentRuntime.to_dict() -> AgentSnapshot
Environment._json_safe() -> AS2 canonical envelope
bridge -> project_validated_as2_inputs(...)
bridge -> AgentSnapshot(...)
bridge -> CAS/storage write
bridge -> AgentRuntime / Environment imports
```

The Host/Pipeline is the orchestrator. The bridge is not the orchestrator.

---

## 4. Projection Handoff Rule

The bridge must never call:

```python
project_validated_as2_inputs(...)
```

The bridge must never construct:

```python
AgentSnapshot(...)
```

Projection is performed by Host/Pipeline after the bridge returns `PreparedAS2Inputs` and after AS2 validation has succeeded.

Approved handoff shape:

```text
prepared = prepare_as2_inputs_from_host_prestage(payload)
validate_as2_inputs(**prepared.to_validate_kwargs())
snapshot_result = project_validated_as2_inputs(...)
```

Implementation details for `snapshot_result` are intentionally outside this RFC. P0.6.14 defines the runtime responsibility split, not the projection call body.

---

## 5. Host Pre-Stage Responsibility Map

Future runtime wiring must provide each AS2 input through an authorized Host provider. The bridge must receive prepared data only.

| AS2 input block | Responsible Host provider | Authorized source | Forbidden source / check |
|---|---|---|---|
| `adapter_identity_context` | Host Identity Provider | explicit identity seed and audit metadata | not `AgentRuntime.name`, not `AgentRuntime.to_dict()`, not wall-clock/UUID |
| `adapter_definition_source` | Host Definition Registry | declarative agent definition manifest | not legacy serialized dictionary, not hidden interpreter state |
| `static_model_registry` | Static Model Registry Snapshot Provider | pinned immutable model registry snapshot | not live provider lookup, not heuristic parsing |
| `memory_ref_source` | Host Memory Externalization Stage | already externalized canonical memory refs | not inline memory dump, not mailbox, not scheduler state |
| `capability_grant_source` | Host Security / Capability Manifest Provider | declarative capability grant manifest | not `AgentRuntime.tools`, not `tools.keys()`, not callable introspection |
| `model_selection_source` | Host Runtime Router / Model Selection Provider | explicit model registry key selected by Host policy | not direct `AgentRuntime.model` as `model_ref` |

### 5.1 Host provider requirements

Each Host provider must be deterministic for the current runtime transaction.

Host providers must not:

```text
- infer identity from display names;
- inspect live callables;
- query model providers dynamically;
- write memory through the bridge;
- pass runtime-envelope data as AS2 state;
- repair invalid AS2 inputs after bridge rejection.
```

---

## 6. Payload Key Classification Matrix

The current hardened bridge recognizer accepts a closed set of Host Pre-Stage payload keys. Future runtime wiring must treat these keys according to their class.

| Key | Classification | Runtime status | Meaning |
|---|---|---|---|
| `adapter_identity_context` | Required success input | ALLOWED | Explicit identity/audit context for AS2 validation. |
| `adapter_definition_source` | Required success input | ALLOWED | Explicit definition/config/canonical-fields source. |
| `static_model_registry` | Required success input | ALLOWED | Pinned model registry snapshot. |
| `memory_ref_source` | Required success input | ALLOWED | Prepared canonical memory refs. |
| `capability_grant_source` | Required success input | ALLOWED | Declarative capability grants. |
| `model_selection_source` | Required success input | ALLOWED | Explicit model registry selection source. |
| `host_stage_failure` | Test/failure modeling key | TEST/DESIGN ONLY | Deterministic modelling of Host-stage failure. Must not appear in production payload. |
| `forbidden_runtime_reads` | Test/failure modeling key | TEST/DESIGN ONLY | Harness marker for forbidden-read coverage. Must not appear in production payload. |
| `inline_memory_payload` | Test/failure modeling key | TEST/DESIGN ONLY | Negative-path marker for inline memory leakage. Must not appear in production payload. |
| `model_selector` | Compatibility alias | DEPRECATED | Transitional alias accepted by bridge code; production wiring must prefer `model_selection_source`. |
| `notes` | Diagnostic/free-form key | DIAGNOSTIC ONLY | Human/audit notes. Must not carry canonical AS2 data. |

### 6.1 Production payload rule

Production Host Pre-Stage payloads may contain only production-allowed success keys and explicitly approved diagnostic notes.

The following keys are forbidden in production payloads:

```text
host_stage_failure
forbidden_runtime_reads
inline_memory_payload
```

The `model_selector` alias is accepted only for compatibility with the current bridge surface. Future runtime wiring must use `model_selection_source`.

---

## 7. Forbidden Reads Registry Cross-Check

Runtime wiring design must prove that each forbidden source is avoided.

| Forbidden source | Runtime wiring rule | Approved replacement |
|---|---|---|
| `AgentRuntime.to_dict()` | must not be canonical AS2 source | explicit Host providers |
| `AgentRuntime.name` | must not be `agent_id` seed | Host Identity Provider |
| `AgentRuntime.model` | must not become direct `model_ref` | `model_selection_source` + `static_model_registry` |
| `AgentRuntime.memory` | must not become snapshot memory | Host Memory Externalization Stage |
| `AgentRuntime.tools` | must not become grants | Host Security / Capability Manifest Provider |
| `AgentRuntime.tools.keys()` | must not drive grant derivation | declarative grant manifest |
| `AgentRuntime.llm` | must not enter AS2 inputs | out of AS2 state |
| `AgentRuntime.env` | must not enter AS2 inputs | out of AS2 state |
| `Environment._json_safe()` | must not provide AS2 envelope | AS2 canonical envelope only |
| interpreter hidden state | must not provide AS2 inputs | explicit Host providers only |
| actor runtime mailbox | must not become snapshot input | out of AS2 state |
| scheduler timers | must not become snapshot input | out of AS2 state |
| sockets / process handles | must not enter AS2 state | out of AS2 state |

Any future runtime implementation that reads these sources for canonical AS2 values fails P0.6.14 readiness.

---

## 8. Failure Handling Strategy

All AS2 runtime wiring failures must fail closed. No failure path may fallback to legacy canonical serialization.

### 8.1 Local agent quarantine

If the failure is attributable to a single agent payload, Host/Pipeline should quarantine only that agent and continue independent runtime flows.

Examples:

```text
HostPreStageUnexpectedFieldError
HostPreStageMissingIdentitySourceError
HostPreStageInvalidAS2InputsError
HostForbiddenRuntimeReadError for one payload
AdapterIdentityContextIncompleteError
AdapterMemorySpaceMismatchError
```

Required behavior:

```text
- do not emit AgentSnapshot;
- do not emit AdapterDerivationRecord;
- do not call projection;
- mark the agent/task as AS2_PRESTAGE_FAILED or QUARANTINED;
- preserve the error context for forensic review;
- do not repair, normalize, or re-route through AgentRuntime.to_dict().
```

### 8.2 Systemic AS2 wiring shutdown

If the failure indicates corruption or unavailability of a shared Host Pre-Stage provider, Host/Pipeline must disable AS2 runtime wiring globally until operator/team intervention.

Examples:

```text
feature flag inconsistency
StaticModelRegistry provider unavailable globally
CAS/storage policy unavailable globally
Forbidden Reads Registry violation in Host provider code
shared identity provider failure
shared definition registry mismatch
payload schema version incompatibility
repeated HostPreStageIOError across unrelated agents
```

Required behavior:

```text
- disable AS2 runtime wiring;
- do not fallback to legacy canonical path;
- emit a system-level incident;
- preserve existing non-AS2 runtime operation only if it does not claim AS2 canonical output;
- require explicit recovery authorization.
```

---

## 9. Feature Flag Placement

The bridge module currently contains a local disabled-by-default guard:

```text
AS2_HOST_PRESTAGE_BRIDGE_ENABLED = False
```

P0.6.14 does not authorize integration with any runtime feature flag system.

Future runtime wiring must document:

```text
- every call site guarded by AS2_HOST_PRESTAGE_BRIDGE_ENABLED or its approved successor;
- default disabled behavior;
- activation authority;
- rollback behavior;
- interaction with local agent quarantine and systemic shutdown.
```

A global feature flag system remains locked until a separate team vote.

---

## 10. Strict Structural Validator Contract

The bridge must be described as a strict structural input recognizer.

It is not a grammar parser, not a semantic repair layer, and not a projection layer.

Contract:

```text
- unknown top-level fields fail closed;
- known test/failure keys are not production AS2 inputs;
- missing/null required sources raise missing-source errors;
- present-but-empty or wrong-shaped sources raise invalid-input errors;
- external mutations after bridge call must not affect PreparedAS2Inputs;
- PreparedAS2Inputs.to_validate_kwargs() returns fresh validation kwargs;
- bridge does not normalize bad Host payloads into valid AS2 inputs.
```

---

## 11. Runtime Acceptance Checklist

A future runtime-code patch cannot be opened until the checklist below is satisfied.

```text
[ ] P0.6.14 Runtime Wiring Design RFC approved.
[ ] Runtime execution graph accepted.
[ ] Host Pre-Stage Responsibility Map accepted.
[ ] Payload Key Classification Matrix accepted.
[ ] Forbidden Reads Registry Cross-Check accepted.
[ ] Projection Handoff Rule accepted.
[ ] Failure Handling Strategy accepted.
[ ] Feature flag placement accepted.
[ ] Strict Structural Validator Contract accepted.
[ ] Debt Register accepted.
[ ] Separate team vote authorizes runtime harness or runtime implementation.
```

---

## 12. Debt Register

P0.6.14 records debt. It does not fix debt in code.

| ID | Debt | Current status | Required future handling |
|---|---|---|---|
| D-01 | `agent_snapshot_bridge.py` docstring still references earlier bridge skeleton lifecycle wording | accepted debt | hygiene/refactor patch updates module documentation to current lifecycle status |
| D-02 | `model_selector` compatibility alias | accepted transitional alias | expand-contract cleanup; production wiring must use `model_selection_source` |
| D-03 | `legacy_agent_runtime_to_dict` parameter in standalone `validate_as2_inputs(...)` | accepted naming debt | separate expand-contract rename toward model-selection terminology |
| D-04 | production/test payload key separation currently represented by docs and bridge field policy | accepted design debt | future runtime patch must enforce production key policy at call sites |

### 12.1 Expand-contract rule for naming cleanup

Naming cleanup must not be mixed into runtime wiring.

Approved cleanup pattern:

```text
1. add clearer API surface;
2. migrate bridge/fixtures/tests to new key;
3. keep old key as compatibility alias for one approved interval;
4. remove old key after separate authorization.
```

---

## 13. Locked items after P0.6.14

The following remain locked:

```text
project_validated_as2_inputs(...) inside bridge
AgentSnapshot construction inside bridge
AgentRuntime imports
Environment imports
interpreter / actor runtime imports
runtime wiring code
runtime feature flag system
CAS/storage I/O
Integrate / Dream / CVM wiring
legacy_agent_runtime_to_dict rename
model_selector removal
AgentRuntime.to_dict() migration
Environment._json_safe() migration
production activation
```

---

## 14. Acceptance criteria for P0.6.14

P0.6.14 is accepted when:

```text
[ ] This document is added.
[ ] Runtime Wiring Execution Graph is defined.
[ ] Host Pre-Stage Responsibility Map is defined.
[ ] Payload Key Classification Matrix is defined.
[ ] Forbidden Reads Registry Cross-Check is defined.
[ ] Projection Handoff Rule is defined.
[ ] Failure Handling Strategy is defined.
[ ] Feature Flag Placement is defined.
[ ] Strict Structural Validator Contract is defined.
[ ] Runtime Acceptance Checklist is defined.
[ ] Debt Register is defined.
[ ] synapse/ remains unchanged.
[ ] tests/ remains unchanged.
```
