# AgentSnapshot Runtime Field Audit

**Status:** BASELINE AUDIT + CANARY REQUIREMENTS — Alpha3g P0.5.7  
**Version:** v0.2  
**Patch:** P0.5.7 AgentSnapshot Runtime Gate Closure & Drift Report  
**Runtime scope authorized:** none — documentation + read-only canary test only

This audit maps current runtime fields to the approved Agent Canonicalization
v1.0 boundary. It is intentionally conservative: if a field may contain a live
runtime handle, callable, wall-clock value, UUID-derived process id, backend
object, or implicit graph reference, it is classified as Runtime Envelope or as a
blocked gate, not as snapshot state.

---

## 1. Classification labels

| Label | Meaning |
|---|---|
| `snapshot_candidate` | May enter a future AgentSnapshot after schema validation and `stable-canonical.v1` hashing. |
| `derived_descriptor` | Must be transformed into `definition_ref`, `model_ref`, `function_descriptor_hash`, or similar descriptor. |
| `memory_ref_candidate` | Must become canonical `memory_ref`; raw memory graph is not accepted by default. |
| `capability_grant_candidate` | Must become declarative `capability_grant`; live callable is excluded. |
| `runtime_envelope` | Must never enter canonical snapshot identity. |
| `legacy_only` | Existing serialization behavior retained for backward compatibility only. |
| `blocked_until_deferred_gate` | Requires closure of a deferred AGENT/FUNC gate. |

---

## 2. `AgentRuntime` field audit

Current constructor fields and runtime additions are observed in
`synapse/builtins.py` and `synapse/interpreter.py`.

| Field / behavior | Current source | Classification | Required future handling |
|---|---|---|---|
| `name` | constructor | `snapshot_candidate` / descriptor input | Normalize as canonical string; may contribute to alias or definition metadata, not unique identity alone. |
| `model` | constructor | `derived_descriptor` | Convert to `model_ref` with provider/model drift table before stable runtime deployment. |
| `trust_level` | constructor | `snapshot_candidate` | Allowlisted config field if schema declares it. |
| `trust_scope` | constructor | `snapshot_candidate` | Stable-canonical array; validate values against approved schema. |
| `memory` | `Memory()` object | `memory_ref_candidate` | Raw `Memory.to_dict()` must not be default snapshot identity. Convert to memory refs or scoped snapshot only by explicit gate. |
| `llm` | `LLMBackend(model)` | `runtime_envelope` | Live backend object and call history excluded. Provider identity represented only by `model_ref`. |
| `tools` | dict of Python callables | `capability_grant_candidate` | Live callables excluded. Future snapshot contains `capability_grants` only. |
| `env` | interpreter environment | `runtime_envelope` | Excluded. Methods represented by approved FunctionDescriptor refs. |
| `energy_pool` | optional interpreter addition | `blocked_until_deferred_gate` | Requires separate canonical schema or runtime-envelope classification. |
| `soulprint` | optional interpreter addition | `snapshot_candidate` only if schema-approved | Must be stable-canonical and schema-versioned before inclusion. |
| `identity_version` | optional interpreter addition | `snapshot_candidate` only if schema-approved | Must not substitute for AgentSnapshot schema version. |

---

## 3. Legacy serialization audit

Legacy paths are not canonical AgentSnapshot paths.

| Path | Current behavior | Classification | P0.5.6 decision |
|---|---|---|---|
| `AgentRuntime.to_dict()` | returns `name`, `model`, `trust_level`, `trust_scope`, raw `memory.to_dict()` | `legacy_only` | Preserve for compatibility; do not treat as stable snapshot. |
| `AgentRuntime.from_dict()` | rebuilds agent and raw memory | `legacy_only` | Preserve; future AgentSnapshot loader must be separate or flagged. |
| `Environment._json_safe(AgentRuntime)` | embeds `{"__type__":"agent","data": agent.to_dict()}` | `legacy_only` | Keep separate from AgentSnapshot. |
| `Environment.to_dict().agents` | serializes env agents with `AgentRuntime.to_dict()` | `legacy_only` | Requires AGENT-07 migration plan before replacement. |
| `Interpreter.capture_agent_state()` | captures memory/soulprint/identity_version for rollback/diff | runtime transaction support | Not a canonical snapshot. Future mapping requires drift report. |

---

## 4. Actor/runtime identity audit

| Field / behavior | Current source | Classification | Required future handling |
|---|---|---|---|
| durable actor `process_id` | `synapse/runtime/actor_runtime.py`, UUID-based | `runtime_envelope` | Must not become `agent_id`. Canonical `agent_id` derives from Agent RFC seed. |
| durable promise `promise_id` | `synapse/runtime/actor_runtime.py`, UUID-based | `runtime_envelope` | Must stay outside snapshot identity. |
| `Interpreter.run_id` | UUID-based | `runtime_envelope` | Audit metadata only. |
| `Environment.env_id` | UUID-based | `runtime_envelope` | Not snapshot identity. |
| actor mailbox / node routing | actor runtime | `runtime_envelope` | Excluded unless future CVM/actor RFC defines recorded descriptor. |

---

## 5. Memory and storage audit

| Field / behavior | Current source | Classification | Required future handling |
|---|---|---|---|
| `Memory.short_term` | `synapse/builtins.py` | `memory_ref_candidate` or scoped snapshot candidate | Default must be memory ref; inline snapshot requires explicit schema. |
| `Memory.long_term` | `synapse/builtins.py` | `memory_ref_candidate` | Prefer canonical `memory_ref` to avoid graph dump. |
| `Memory.capacity` | `synapse/builtins.py` | `snapshot_candidate` | Config field if declared. |
| `MemoryPalace.backend` | `synapse/memory.py` | `runtime_envelope` | Storage handle excluded. |
| `MemoryPalace.backend_name` | `synapse/memory.py` | descriptor candidate | May become storage descriptor, not direct memory content. |
| `MemoryPalace.imprint().created_at` | `synapse/memory.py`, `time.time()` fallback | `runtime_envelope` / recorded event only | Never implicit snapshot identity. |
| storage backend generated ids | `synapse/storage_backends.py` | runtime/storage envelope | Convert only through approved memory_ref policy. |

---

## 6. Immediate blockers before runtime code

The next code patch must not begin until a read-only drift report answers these
questions:

1. Which current `AgentRuntime` fields are safe `snapshot_candidate` values after
   stable-canonical serialization?
2. Which raw memory fields must become `memory_ref` values?
3. Which `soulprint`, `energy_pool`, and subagent fields need separate schemas?
4. Which fields are legacy-only and must stay out of AgentSnapshot v1?
5. What fail-closed error is raised if a live callable, LLM backend, env object,
   actor ref, promise, or storage backend handle is encountered?

---


## 7. Canary requirements for P0.5.7

P0.5.7 adds a read-only canary test to prevent silent drift between the legacy
`AgentRuntime.to_dict()` shape and the future canonical AgentSnapshot allowlist.

The canonical AgentSnapshot v1 allowlist remains:

```text
agent_id
definition_ref
config
canonical_fields
memory_refs
model_ref
capability_grants
profile
schema_version
```

The canary MUST assert that legacy `AgentRuntime.to_dict()` is not a canonical
snapshot shape. In particular, legacy output currently contains raw `memory` and
lacks `agent_id`, `definition_ref`, `memory_refs`, `capability_grants`, `profile`,
and `schema_version`.

This test is intentionally read-only. It may instantiate an `AgentRuntime` and
inspect its legacy serialization shape, but it MUST NOT modify `AgentRuntime`,
`AgentRuntime.to_dict()`, `Environment`, actor runtime, memory backends, or any
golden fixture.

## 8. P0.5.7 audit verdict

```text
AgentSnapshot runtime integration: NOT AUTHORIZED
Read-only drift/gate patch: COMPLETED
Standalone schema/value core: AUTHORIZED NEXT under local fail-closed schema allowlist
Legacy serialization migration: BLOCKED until AGENT-07 plan
Schema registry deployment: BLOCKED until AGENT-11 / FUNC-04 central registry plan
FunctionDescriptor runtime registry: BLOCKED until FUNC-03 / FUNC-04 implementation gates
```
