# AS2 Implementation Plan — P0.6.4

**Status:** DRAFT — Alpha3g P0.6.4 implementation planning / fixture harness design  
**Scope:** docs + test-only fixture/invariant harness  
**Runtime scope authorized:** none  
**Adapter implementation authorized:** no  
**Depends on:** `RFC-AGENT-SNAPSHOT-ADAPTER.md` v1.0, `AS2-INDEPENDENT-VERIFICATION-MATRIX.md`, `AGENTRUNTIME-TODICT-DRIFT-REPORT.md`

P0.6.4 creates executable guardrails for a future AS2 adapter implementation.
It does **not** implement the adapter, does **not** introduce a public adapter API,
and does **not** modify legacy runtime serialization.

The goal is to convert the approved AS2 RFC into data-level fixtures and an
invariant harness that future implementation patches must satisfy.

---

## 1. Authorized work

P0.6.4 may add or modify only:

```text
docs/AS2-IMPLEMENTATION-PLAN.md
docs/AS2-DRIFT-HARNESS-DESIGN.md
docs/AS2-FIXTURE-CORPUS-SPEC.md
docs/MIGRATION-READINESS-CHECKLIST.md
docs/CHANGELOG.md
docs/ALPHA3F_PLANNING_GATE.md
tests/fixtures/as2/*.json
tests/test_as2_fixture_matrix_p064.py
```

The test file is a fixture/invariant validator. It must not import or call an
AS2 adapter.

---

## 2. Explicit locks

The following remain **LOCKED** in P0.6.4:

```text
synapse/agent_snapshot_adapter.py
to_agent_snapshot()
any AS2 adapter public API
AgentRuntime.to_dict() migration
Environment._json_safe() migration
runtime profile selector
FunctionDescriptor runtime registry
central schema/profile registry
real provider model entries
exception class definitions
production derive_memory_space_id()
Integrate / Dream / CVM paths
golden fixture rewrites
```

Any patch that introduces these changes is out of scope and must be rejected.

---

## 3. Implementation staging

```text
P0.6.3  AS2 RFC APPROVED v1.0                      ✅ complete
P0.6.4  Implementation planning + fixture harness   current
P0.6.5  Flagged adapter skeleton                    locked; requires explicit vote
P0.6.x  Legacy/runtime integration                  locked; separate gates
```

P0.6.4 is not an implementation patch. It is the bridge between the approved
RFC and the first implementation authorization vote.

---

## 4. Fixture corpus baseline

P0.6.4 establishes the baseline corpus under:

```text
tests/fixtures/as2/
```

The minimum corpus is:

```text
positive_minimal_valid_projection_inputs.json
negative_missing_identity_context.json
negative_incomplete_identity_context.json
negative_unknown_model_ref.json
negative_missing_memory_ref_source.json
negative_memory_space_mismatch.json
negative_missing_capability_grant_source.json
negative_legacy_envelope_conflict.json
negative_inline_memory_rejected.json
negative_subagent_out_of_scope.json
negative_ambient_authority_forbidden.json
```

Fixtures are canonical data assets. They are not runtime tests and must not imply
an adapter API signature.

---

## 5. Harness contract

The P0.6.4 harness validates fixture structure and invariant intent only.

Allowed:

```text
load fixture JSON
validate schema fields
validate stable naming conventions
validate mock-only StaticModelRegistry entries
validate expected_error names against approved taxonomy
validate negative memory-space mismatch is structurally present
validate no fixture encodes legacy envelope as canonical output
validate AdapterDerivationRecord shape on positive fixture
```

Forbidden:

```text
import synapse.agent_snapshot_adapter
call to_agent_snapshot()
instantiate adapter error classes
define production exception classes
read live AgentRuntime state
modify legacy runtime or serialization
```

---

## 6. Pre-flight gate-closure checklist before P0.6.5

P0.6.5 is not authorized until a separate team vote confirms that these gates are
satisfied or explicitly scoped out:

| Gate | Required before P0.6.5? | P0.6.4 status |
|---|---:|---|
| AS2 RFC v1.0 approved | Yes | satisfied by P0.6.3 |
| Fixture corpus schema accepted | Yes | established by P0.6.4 |
| Fixture matrix harness merged | Yes | established by P0.6.4 |
| Negative-path fixture corpus complete for P0.6.5 skeleton | Yes | baseline corpus established |
| AdapterDerivationRecord serialization baseline | Yes | fixture shape established; production serialization TBD |
| StaticModelRegistry mock-only baseline | Yes | established by P0.6.4 |
| Memory-space policy test vectors | Yes | fixture-level expected values only |
| FunctionDescriptor ref validation boundary | Yes | schema-only; runtime registry remains locked |
| WATCH-01 presence-marker resolution plan | Yes | P0.6.4 keeps as implementation-planning gate |
| WATCH-02 historical wording authority | Yes | P0.6.1+ RFC remains normative |
| Explicit P0.6.5 team vote | Yes | pending |

P0.6.4 does not close runtime deployment gates. It prepares the checklist needed
for a later implementation vote.

---

## 7. P0.6.5 preliminary boundary

If later approved, P0.6.5 should be limited to a flagged standalone adapter
skeleton. The expected initial code boundary is:

```text
synapse/agent_snapshot_adapter.py only
```

Still forbidden in P0.6.5 unless separately authorized:

```text
AgentRuntime.to_dict() changes
Environment._json_safe() changes
profile selector integration
Integrate / Dream path changes
golden fixture migration
real provider registries
central schema registry deployment
```

P0.6.5 requires a separate structured team vote after P0.6.4 is reviewed.

---

## P0.6.6 status update — validation hardening / fixture-driven enforcement

P0.6.6 hardens the P0.6.5 AS2 skeleton against edge cases discovered by an
adversarial probe of the validation surface. It is **validation-only** (team
Vote A consolidated). No projection function is added, no `AgentSnapshot` is
constructed, no hash is computed, no feature flag is introduced, and no
legacy integration is opened.

### Implemented scope (P0.6.6)

```text
synapse/agent_snapshot_adapter.py
  point fixes only, no API expansion:
  - reject whitespace-only alias (identity drift surface)
  - reject negative or bool identity_version
  - reject duplicate legacy_model in StaticModelRegistry
  - reject duplicate (space, key, mode) memory refs
  - reject conflicting access_mode for same (space, key)
  - reject duplicate tool_namespace in CapabilityGrantSource
  - reject any legacy __type__ envelope marker (not only 'agent')

tests/test_as2_adapter_validation_p066.py
  83 new fixture-driven and edge-case tests
  fixture-driven matrix: all 11 fixtures dispatched through validate_as2_inputs
  exact-leaf-class assertion (no generic base, no upcast)
  positive fixture validates silently
  discipline anchors: no to_agent_snapshot, no projection name, no AgentSnapshot
    construction, no FeatureFlag machinery

docs/RFC-AGENT-SNAPSHOT-ADAPTER.md
  §17 name reservation for project_validated_as2_inputs (P0.6.7 only)
  §18 R8 capability_grant shape gap recorded with options R8-A/B/C

docs/AGENTRUNTIME-TODICT-DRIFT-REPORT.md
  §9 R8 entry added

docs/MIGRATION-READINESS-CHECKLIST.md
  P0.6.6 gate update

docs/CHANGELOG.md
  P0.6.6 prepended

docs/ALPHA3F_PLANNING_GATE.md
  P0.6.6 status appended
```

### NOT implemented (locked)

```text
to_agent_snapshot()                   FORBIDDEN permanently
project_validated_as2_inputs()        implemented for fixture-driven standalone projection
build_*_from_*_inputs()                FORBIDDEN names; no function
AgentSnapshot construction             not authorized
AdapterDerivationRecord hash compute   not authorized (form-only validation kept)
feature flag machinery                 not authorized (module remains isolated)
AS2ViolationContext class              deferred to P0.6.7
AgentRuntime imports                   FORBIDDEN
Environment imports                    FORBIDDEN
interpreter / actor_runtime imports    FORBIDDEN
profile selector                       not authorized
real provider registry entries         not authorized
FunctionDescriptor runtime registry    not authorized
golden fixture migration               not authorized
```

### Results

```text
Full suite: 920 passed, 1 skipped (P0.6.5.1 baseline 837 + 83 hardening tests)
Zero regression.
Skip baseline preserved at 1 (existing test_golden_replay parametrization).
synapse/ outside agent_snapshot_adapter.py: byte-identical to P0.6.5.1.
```

### Next gate

P0.6.7 — Fixture-driven minimal standalone projection (`project_validated_as2_inputs`).
Requires separate team vote. Will introduce:

```text
project_validated_as2_inputs() — pure deterministic projection from validated inputs
AS2 -> core CapabilityGrant resolution (R8-A reduction by default)
AdapterDerivationRecord population with synthetic values (no hashes yet)
AS2ViolationContext for richer error attribution (deferred from P0.6.6)
```

P0.6.7 will NOT introduce: legacy AgentRuntime bridge, profile selector,
production hash computation, or any modification to legacy serialization.


---

## P0.6.7 status update — fixture-driven minimal standalone projection

P0.6.7 implements the first minimal AS2 projection function under the scope
approved by team review. The function name is:

```text
project_validated_as2_inputs(...)
```

Implemented scope:

```text
synapse/agent_snapshot_adapter.py
  - AdapterDefinitionSource value skeleton and validator (R9 closure)
  - project_validated_as2_inputs(...)
  - R8-A canonical projection from AS2 CapabilityGrant to core CapabilityGrant
  - synthetic AdapterDerivationRecordSkeleton population only

tests/test_as2_adapter_projection_p067.py
  - positive fixture projection to real AgentSnapshot
  - deterministic snapshot_hash repeatability check
  - selected field assertions
  - negative fixture validation boundary remains before projection

tests/fixtures/as2/positive_minimal_valid_projection_inputs.json
  - adapter_definition_source added
  - expected_derivation_record includes adapter_definition_source_hash
  - expected snapshot selected fields updated
```

Still locked:

```text
to_agent_snapshot()
legacy AgentRuntime bridge
Environment._json_safe() migration
real AdapterDerivationRecord hash computation
AS2ViolationContext
feature flag machinery
real provider registry
FunctionDescriptor runtime registry
golden fixture migration
```

Next gate: P0.6.8 — AdapterDerivationRecord hash computation /
Merkle-transparent audit trail, subject to separate review.


---

## P0.6.8 status update — AdapterDerivationRecord hashing / Merkle-transparent audit

P0.6.8 implements real stable-canonical input hashes for the AdapterDerivationRecord returned by the standalone AS2 projection path.

Implemented scope:

```text
synapse/agent_snapshot_adapter.py
  - real derivation input hashes via stable_canonical_hash
  - five required input-hash entries
  - unchanged AgentSnapshot projection semantics

tests/test_as2_adapter_derivation_p068.py
  - expected-hash fixture assertions
  - direct stable_canonical_hash parity checks
  - repeatability checks
  - proof that derivation record does not affect snapshot_hash()

tests/fixtures/as2/positive_minimal_valid_projection_inputs.json
  - expected_derivation_record input hashes replaced with real canonical digests
```

Still locked:

```text
AS2ViolationContext
legacy AgentRuntime bridge
Environment._json_safe() migration
feature flag machinery
real provider registry
FunctionDescriptor runtime registry
golden fixture migration
Integrate / Dream / CVM paths
```

Next gate: team review to choose between diagnostic hardening (`AS2ViolationContext`) and bridge-design preparation. Legacy bridge remains locked until a separate authorization.

## P0.6.9 status — AS2ViolationContext / forensic error attribution

P0.6.9 adds structured context to AS2 fail-closed errors without changing the
successful standalone projection path.

Implemented scope:

```text
synapse/agent_snapshot_adapter.py
  - AS2ViolationContext frozen value object
  - context support on existing AS2AdapterError hierarchy
  - canonical RFC-reference validation for context values

tests/fixtures/as2/*.json
  - all negative fixtures include expected_error_context

tests/test_as2_violation_context_p069.py
  - exact leaf exception assertions remain active
  - expected_error_context subset matching
  - frozen context validation
  - canonical rfc_reference format validation
  - proof that context is not mixed into AdapterDerivationRecord
```

Still locked:

```text
legacy AgentRuntime bridge
Environment._json_safe() migration
projection semantics changes
AdapterDerivationRecord hash changes
AgentSnapshot schema changes
feature flag wiring
R8-B / R8-C schema migration
```

Next gate: team review to decide whether the next patch starts bridge-design
preparation or performs further standalone diagnostics hardening.


## P0.6.10 status — AS2 Legacy Bridge Design RFC / Host Pre-Stage Protocol

P0.6.10 closes the standalone AS2 cycle for the current track and starts bridge design without opening bridge code.

Implemented scope:

```text
docs/AS2-LEGACY-BRIDGE-DESIGN.md
  - Airlock Pattern
  - Host Pre-Stage Protocol
  - Step 0 Host Capability Verification
  - Host vs Bridge responsibility split
  - Forbidden Reads Registry
  - future feature flag reservation
  - readiness checklist for bridge code
```

Still locked:

```text
AgentRuntime imports
Environment imports
runtime wiring
feature flag implementation
to_agent_snapshot()
AgentRuntime.to_dict() canonical usage
Environment._json_safe() migration
bridge fixture corpus
bridge implementation
profile selector
real provider registry
FunctionDescriptor runtime registry
Integrate / Dream / CVM paths
```

Next gate: P0.6.11 Bridge Fixture Corpus / Host Pre-Stage Harness, pending explicit team authorization.


## P0.6.11 status — Bridge Fixture Corpus / Host Pre-Stage Harness

P0.6.11 implements the test-only/data stage approved after P0.6.10.

Implemented scope:

```text
tests/fixtures/as2_bridge/
  - 4 positive bridge fixtures
  - 12 negative bridge fixtures
  - schema_version alpha3g.as2_bridge_fixture.v1

tests/test_as2_bridge_harness_p0611.py
  - fixture schema validation
  - deterministic sorted JSON validation
  - positive expected_as2_inputs validation through validate_as2_inputs(...)
  - Forbidden Reads Registry coverage check
  - Host Pre-Stage Protocol Step 0-10 coverage check
```

Current API alignment:

```text
positive bridge fixtures include synthetic legacy_agent_runtime_to_dict.model
for the current validate_as2_inputs(...) model-selection API. This is naming
debt and does not authorize AgentRuntime.to_dict() as canonical input.
```

Still locked:

```text
synapse/ changes
bridge code
AgentRuntime imports
Environment imports
feature flag implementation
project_validated_as2_inputs(...) calls in bridge harness
to_agent_snapshot()
Python bridge exception classes
real runtime objects
real I/O
```

Next proposed stage: P0.6.12 flagged bridge implementation planning/authorization under separate team vote.

## P0.6.12 status — Flagged Host Pre-Stage Bridge Skeleton

P0.6.12 implements the approved first bridge-code boundary.

Implemented:

```text
synapse/agent_snapshot_bridge.py
tests/test_as2_bridge_implementation_p0612.py
```

The bridge accepts Host Pre-Stage mappings, builds frozen DTOs, isolates `model_selection_source` from the current standalone AS2 naming debt, validates through `validate_as2_inputs(...)`, and returns `PreparedAS2Inputs`.

P0.6.12 remains bridge-skeleton scope only. It does not call `project_validated_as2_inputs(...)`, does not construct `AgentSnapshot`, does not import `AgentRuntime` or `Environment`, and does not wire into runtime paths.

The local bridge flag remains disabled by default:

```text
AS2_HOST_PRESTAGE_BRIDGE_ENABLED = False
```

Next proposed gate: bridge readiness review for controlled runtime wiring remains pending separate team authorization. Runtime integration is not implied by P0.6.12.


---

## P0.6.13 status — Host Pre-Stage Bridge Hardening

P0.6.13 hardens the P0.6.12 bridge skeleton before any projection handoff or runtime wiring.

Implemented hardening:

```text
- closed Host Pre-Stage payload contract with unknown-field rejection;
- nested-field rejection for approved AS2 boundary structures;
- explicit missing/null/empty/wrong-shape semantics;
- defensive freeze of bridge DTO internals;
- fresh mutable copies from PreparedAS2Inputs.to_validate_kwargs();
- deterministic adversarial bridge-hardening tests.
```

No projection handoff is authorized by P0.6.13. The bridge still does not call `project_validated_as2_inputs(...)`, does not construct `AgentSnapshot`, and does not import legacy runtime modules.

Next gates remain subject to separate team vote. Candidate future stages include projection handoff design or controlled API naming cleanup, but neither is authorized by P0.6.13.

---

## P0.6.14 status — Runtime Wiring Design RFC

P0.6.14 is a doc-only runtime wiring design stage. It does not authorize code, test, bridge, runtime, or feature-flag changes.

Added design artifact:

```text
docs/AS2-RUNTIME-WIRING-DESIGN.md
```

P0.6.14 defines:

```text
- Runtime Wiring Execution Graph;
- Host Pre-Stage Responsibility Map;
- Payload Key Classification Matrix;
- Forbidden Reads Registry Cross-Check;
- Projection Handoff Rule;
- Failure Handling Strategy;
- Feature Flag Placement;
- Strict Structural Validator Contract;
- Runtime Acceptance Checklist;
- Debt Register.
```

Key runtime-wiring decision:

```text
Bridge prepares and validates PreparedAS2Inputs.
Host/Pipeline orchestrates project_validated_as2_inputs(...).
Bridge never calls project_validated_as2_inputs(...).
Bridge never constructs AgentSnapshot.
```

Failure handling policy:

```text
single-agent payload failure -> quarantine that agent / task;
systemic Host provider or wiring failure -> disable AS2 runtime wiring globally;
no legacy canonical fallback is allowed.
```

Maintained locks:

```text
synapse/ changes: LOCKED
tests/ changes: LOCKED
AgentRuntime / Environment imports: LOCKED
runtime wiring code: LOCKED
runtime feature flag system: LOCKED
CAS/storage I/O implementation: LOCKED
Integrate / Dream / CVM wiring: LOCKED
legacy_agent_runtime_to_dict rename: DEFERRED
model_selector removal: DEFERRED
```

Next proposed gate: P0.6.15 Runtime Wiring Harness / Host Provider Mocks, pending explicit team authorization.
