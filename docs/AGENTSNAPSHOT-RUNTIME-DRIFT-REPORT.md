# AgentSnapshot Runtime Drift Report

**Status:** GO — Alpha3g P0.5.7 gate-closure / drift report  
**Version:** v0.1  
**Patch:** P0.5.7 AgentSnapshot Runtime Gate Closure & Drift Report  
**Runtime scope authorized:** none — documentation + read-only canary test only  
**Depends on:** `AGENTSNAPSHOT-RUNTIME-PLAN.md`, `AGENTSNAPSHOT-RUNTIME-FIELD-AUDIT.md`, `RFC-AGENT-CANONICALIZATION.md` v1.0, `RFC-FUNCTION-DESCRIPTOR.md` v1.0  
**Output gate:** standalone AgentSnapshot schema/value core may start in P0.5.8 only under the constraints below.

P0.5.7 resolves the sequencing ambiguity discovered after P0.5.6. It does not
implement AgentSnapshot. It converts the P0.5.6 field audit into an explicit
runtime-readiness decision, separates standalone schema/value work from runtime
deployment, and records the minimum gate closures required before any code patch
creates `synapse/agent_snapshot.py`.

---

## 1. Analyzed surfaces

The drift report uses the P0.5.6 field audit as the source of truth and checks it
against current runtime serialization boundaries. The analyzed surfaces are:

| Surface | Current source | Finding |
|---|---|---|
| `AgentRuntime.to_dict()` | `synapse/builtins.py` | Legacy-only serialization. It emits `name`, `model`, `trust_level`, `trust_scope`, and raw `memory`; it is not an AgentSnapshot shape. |
| `AgentRuntime.llm` | `synapse/builtins.py` | Runtime Envelope only. Live backend and history must not enter canonical snapshot identity. |
| `AgentRuntime.tools` | `synapse/builtins.py` | Runtime Envelope / future `capability_grant` candidate. Live callables are excluded. |
| `AgentRuntime.env` | `synapse/builtins.py` / `interpreter.py` | Runtime Envelope only. Environment parent scopes and methods are excluded. |
| Raw `Memory.to_dict()` | `synapse/builtins.py` | Memory graph dump. Default AgentSnapshot v1 must use `memory_ref`; inline memory requires a later explicit schema. |
| Actor ids / promise ids | `synapse/runtime/actor_runtime.py` | UUID-derived runtime metadata. Not canonical `agent_id`. |
| `MemoryPalace.imprint().created_at` | `synapse/memory.py` | Wall-clock fallback. Recorded-event/envelope only, never implicit snapshot identity. |
| Storage backend handles | `synapse/storage_backends.py` | Runtime/storage envelope. Not canonical snapshot state. |

---

## 2. Drift classification result

| Classification | Count / status | Notes |
|---|---:|---|
| `snapshot_candidate` | bounded | Only allowlisted semantic/config fields may later enter `AgentSnapshot`. |
| `derived_descriptor` | bounded | `model` and method/definition surfaces require descriptor conversion. |
| `memory_ref_candidate` | present | Raw `Memory` content must become `memory_ref` by default. |
| `capability_grant_candidate` | present | Live tools must become declarative grants. |
| `runtime_envelope` | present and expected | LLM backend, env, UUIDs, promise ids, time defaults, handles. |
| `legacy_only` | present and expected | `AgentRuntime.to_dict()` and environment agent JSON shapes. |
| `unknown_requires_review` | **0** | No P0.5.6-audited field remains unclassified. |
| live handle marked `snapshot_candidate` | **0** | No live runtime handle is allowed into the future snapshot core. |

---

## 3. Canary enforcement added in P0.5.7

P0.5.7 adds a read-only canary test:

```text
tests/test_agentsnapshot_canary_p057.py
```

The canary does not mutate runtime behavior and does not introduce
AgentSnapshot code. It verifies that legacy `AgentRuntime.to_dict()` is still
structurally distinct from the approved AgentSnapshot allowlist:

```text
AgentSnapshot allowlist:
agent_id, definition_ref, config, canonical_fields, memory_refs,
model_ref, capability_grants, profile, schema_version
```

The canary intentionally asserts that legacy `to_dict()`:

```text
contains raw `memory`, not `memory_refs`
does not contain `agent_id`
does not contain `definition_ref`
does not contain `capability_grants`
does not contain `schema_version` or `profile`
```

This guards against future silent drift where legacy serialization might be
mistaken for a canonical AgentSnapshot payload.

---

## 4. Gate interpretation for FUNC-03 / FUNC-04 / AGENT-11

P0.5.7 intentionally does not fully close all deferred implementation gates. It
separates what blocks standalone schema/value core from what blocks deployment or
registry-backed runtime.

| Gate | P0.5.7 interpretation | Blocks P0.5.8 standalone core? | Blocks deployment / integration? |
|---|---|---:|---:|
| `FUNC-03` dependency manifest taxonomy | Not required for standalone AgentSnapshot value objects that only carry approved `function_descriptor_hash` / `definition_ref`. | No | Yes — FunctionDescriptor registry/runtime dependency validation |
| `FUNC-04` schema evolution registry | Standalone core may use a local schema-version allowlist and fail closed. | No, if local allowlist exists | Yes — central schema/profile compatibility registry |
| `AGENT-11` schema registry | Standalone core may proceed with a local AgentSnapshot schema allowlist and `UnknownSchemaVersionError`. | No, if local allowlist exists | Yes — production runtime deployment and integration |

---

## 5. Minimum schema allowlist for P0.5.8

P0.5.8 standalone AgentSnapshot schema/value core may start only if it uses a
local fail-closed allowlist equivalent to:

```text
alpha3g.agent_snapshot.v1
alpha3g.agent_definition_ref.v1
alpha3g.agent_id.v1
alpha3g.memory_ref.v1
alpha3g.memory_space_id.v1
alpha3g.capability_grant.v1
alpha3g.function_descriptor.v1
stable-canonical.v1
```

Unknown schema versions, unknown profile ids, unknown memory access modes, and
unknown capability grant schema families MUST fail closed. A central registry is
still deferred and remains required before deployment/integration.

---

## 6. GO / NO-GO decision

**Decision:** `GO` for P0.5.8 standalone AgentSnapshot schema/value core, under
strict limitations.

P0.5.8 is authorized only for:

```text
new isolated AgentSnapshot module
pure dataclasses / validators / serialization helpers
local schema-version allowlist
read-only unit tests
no consumer integration
```

P0.5.8 is not authorized for:

```text
AgentRuntime.to_dict() migration
actor_runtime.py integration
interpreter.py integration
Environment serialization changes
MemoryPalace dereference
FunctionDescriptor runtime registry
central schema registry
CVM/opcode visibility
Dream/Integrate integration
golden fixture rewrites
hard switch of any profile or serializer
```

---

## 7. Required P0.5.8 acceptance criteria

P0.5.8 must prove:

- [ ] standalone AgentSnapshot value objects reject unknown schema/profile ids;
- [ ] only allowlisted fields can be serialized;
- [ ] raw `AgentRuntime`, live tools, LLM backend, env, promises, actor refs,
      storage handles, and wall-clock fields fail closed;
- [ ] `memory_ref` is address-only and does not dereference storage;
- [ ] `capability_grant` is declarative and does not wrap live callables;
- [ ] `definition_ref` points to approved FunctionDescriptor hashes but does not
      instantiate a FunctionDescriptor registry;
- [ ] legacy `AgentRuntime.to_dict()` remains unchanged and non-canonical;
- [ ] all tests pass without changes to interpreter, actor runtime, CVM, memory,
      Integrate, Dream, or golden fixtures.

---

## 8. P0.5.7 verdict

```text
AgentSnapshot runtime integration: NOT AUTHORIZED
AgentSnapshot standalone schema/value core: AUTHORIZED NEXT (P0.5.8), with local fail-closed schema allowlist
AgentRuntime.to_dict() migration: BLOCKED
FunctionDescriptor runtime registry: BLOCKED by FUNC-03/FUNC-04 implementation gates
Central schema registry deployment: BLOCKED by AGENT-11 / FUNC-04
```

---

## P0.5.8 completion note

The P0.5.7 GO recommendation was consumed by P0.5.8.

Result:

```text
AgentSnapshot standalone schema/value core: COMPLETED
Runtime integration: NOT AUTHORIZED
Legacy serialization migration: NOT AUTHORIZED
Central schema registry: NOT IMPLEMENTED
```

The P0.5.8 implementation stays within the local fail-closed allowlist and does
not change any legacy runtime consumer. The next authorized step is standalone
hardening only.

---

## P0.5.9 completion note

P0.5.9 hardened the SA1 standalone core against adversarial edge cases. The
acceptance criteria from §7 are reinforced, not changed:

- `[x]` AgentSnapshot value objects continue to reject unknown schema/profile;
- `[x]` only allowlisted fields serialize;
- `[x]` runtime-envelope fields fail closed at every nesting depth;
- `[x]` `memory_ref` is address-only;
- `[x]` `capability_grant` is declarative;
- `[x]` `definition_ref` carries opaque FunctionDescriptor hashes;
- `[x]` legacy `AgentRuntime.to_dict()` remains unchanged;
- `[x]` no interpreter, actor runtime, CVM, memory, Integrate, Dream, or golden
  fixture changes.

Additional invariants established in P0.5.9:

```text
snapshot_hash() is stable under external mutation of any mapping or list
passed into config / canonical_fields / model_ref.

duplicate memory_refs, conflicting access_mode for the same memory address,
and duplicate capability_grants per tool_namespace fail closed.

whitespace-only memory_key fails closed.

AgentIdSeed.alias is normalized: None / "" / "   " all produce identical
agent_id, removing an identity-drift surface.

validator path (used by from_dict) enforces all the above for round-trip
payloads, not only direct constructor calls.
```

Result:

```text
AgentSnapshot standalone hardening: COMPLETED
Runtime integration: NOT AUTHORIZED
Legacy serialization migration: NOT AUTHORIZED
Central schema registry: NOT IMPLEMENTED
FunctionDescriptorRef: NOT IMPLEMENTED (blocked by FUNC-03)
```

The next authorized step is P0.5.10 — legacy `AgentRuntime.to_dict()` drift
analysis (read-only comparison, AS2-prep). Flagged adapter remains deferred.
