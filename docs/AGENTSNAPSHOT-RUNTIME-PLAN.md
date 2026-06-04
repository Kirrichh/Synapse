# AgentSnapshot Runtime Planning & Drift Audit

**Status:** GATE-CLOSED — Alpha3g P0.5.7 drift/gate report  
**Version:** v0.2  
**Patch:** P0.5.7 AgentSnapshot Runtime Gate Closure & Drift Report  
**Runtime scope authorized:** none — documentation + read-only canary test only  
**Depends on:** `RFC-AGENT-CANONICALIZATION.md` v1.0, `RFC-FUNCTION-DESCRIPTOR.md` v1.0, `RFC-STABLE-CANONICAL-IDENTITY.md` v1.0, `MIGRATION-READINESS-CHECKLIST.md`  
**Related gates:** AGENT-04, AGENT-05, AGENT-06, AGENT-07, AGENT-08, AGENT-11, STABLE-05, INT-07

This document is the first post-approval planning gate for AgentSnapshot runtime
work. It does not authorize code. It defines the implementation sequence,
field-audit obligations, drift-analysis obligations, and acceptance criteria
that must be satisfied before any `synapse/` runtime patch serializes an agent
as a canonical value.

---

## 1. Scope of P0.5.7

P0.5.7 is a gate-closure and drift-report patch only. It explicitly authorizes one read-only canary test that checks the documented AgentSnapshot allowlist against legacy `AgentRuntime.to_dict()` shape. It does not authorize AgentSnapshot code or runtime integration.

Allowed:

```text
document current AgentRuntime / Environment / MemoryPalace field boundaries
document Snapshot vs Runtime Envelope mapping
define future runtime patch sequence
define drift-analysis corpus and GO/NO-GO gate
define checklist updates for AgentSnapshot runtime readiness
run one read-only canary test proving legacy `AgentRuntime.to_dict()` is not canonical AgentSnapshot
```

Forbidden:

```text
synapse/ code changes
tests/ code changes except `tests/test_agentsnapshot_canary_p057.py` read-only canary
golden fixture rewrites
AgentSnapshot runtime class
FunctionDescriptor runtime registry
schema registry implementation
CVM/opcode changes
interpreter.py changes
actor runtime changes
memory backend changes
canonical time API
deterministic ID runtime implementation
```

---

## 2. Current-code audit baseline

The approved Agent RFC requires a field audit before runtime work. The current
runtime has multiple serialization and host-state surfaces that must not be
migrated blindly into canonical agent snapshots.

| Runtime surface | Current code location | Current behavior | RFC classification |
|---|---|---|---|
| `AgentRuntime` semantic fields | `synapse/builtins.py` — `AgentRuntime.__init__`, `to_dict()` | `name`, `model`, `trust_level`, `trust_scope`, `memory` are persisted by legacy `to_dict()` | candidate snapshot inputs after transformation, not raw dump |
| `AgentRuntime.llm` | `synapse/builtins.py` — `self.llm = LLMBackend(model)` | live provider/mock backend object with history/call count | Runtime Envelope only |
| `AgentRuntime.tools` | `synapse/builtins.py` — `self.tools = {}` / `register_tool()` | live Python callables | Runtime Envelope only; future Capability Grants only |
| `AgentRuntime.env` | `synapse/builtins.py` / `interpreter.py` | environment object containing `self`, methods, parent scope | Runtime Envelope only |
| `Memory` inline dump | `AgentRuntime.to_dict()` and `Memory.to_dict()` | dumps `short_term`, `long_term`, `capacity` | not canonical v1 by default; must map to `memory_ref` unless explicitly scoped |
| `Environment._json_safe(AgentRuntime)` | `synapse/interpreter.py` | serializes agents through legacy `AgentRuntime.to_dict()` | legacy runtime serialization, not AgentSnapshot |
| `Environment.agents` | `synapse/interpreter.py` | maps agent names to `AgentRuntime` and serializes via `to_dict()` | migration candidate; must not become stable-canonical by default |
| actor process id | `synapse/runtime/actor_runtime.py` — `spawn_actor()` | `uuid.uuid4().hex[:12]` in process id | Runtime Envelope / actor runtime id, not canonical `agent_id` |
| durable promise id | `synapse/runtime/actor_runtime.py` — `create_durable_promise()` | `uuid.uuid4().hex[:16]` | Runtime Envelope only |
| `MemoryPalace.imprint()` timestamp | `synapse/memory.py` | `created_at = time.time()` default | Runtime Envelope / recorded event only; not implicit snapshot state |
| storage backend ids/timestamps | `synapse/storage_backends.py` | generated ids and `time.time()` fallbacks | storage-layer envelope, not snapshot identity |
| `Interpreter.run_id` / `Environment.env_id` | `synapse/interpreter.py` | UUID-backed process/session metadata | Runtime Envelope only |

The audit confirms that future runtime work must not reuse legacy `to_dict()`
outputs as canonical AgentSnapshot payloads.

---

## 3. Snapshot boundary required by v1

A future AgentSnapshot payload must be constructed from allowlisted fields only:

```text
agent_id
definition_ref
config
canonical_fields
memory_refs
model_ref
capability_grants
profile = stable-canonical.v1
schema_version = alpha3g.agent_snapshot.v1
```

The following current runtime objects must remain excluded by construction:

```text
AgentRuntime.llm
AgentRuntime.tools live callables
AgentRuntime.env
Environment parent pointers
actor mailbox / process id / promise ids
MemoryPalace backend objects
storage backend handles
wall-clock timestamps
runtime audit buffers
Python function objects / FnDef runtime references unless represented by approved FunctionDescriptor refs
```

---

## 4. Drift-analysis corpus for the next runtime gate

Before code is authorized, a future drift-analysis patch must build a read-only
corpus from current runtime shapes without mutating artifacts. The corpus should
cover at least:

1. Basic agent declaration with `name`, `model`, `trust_level`, and empty memory.
2. Agent with `trust_scope` and `memory_config` capacity.
3. Agent with `soulprint` / `identity_version` fields if present.
4. Agent with short-term and long-term legacy `Memory` content.
5. Agent with registered live `tools` to prove tools are excluded and represented
   only by future `capability_grants`.
6. Agent with `llm` backend history to prove provider history is excluded.
7. Agent with `env` methods to prove runtime environment does not enter snapshot.
8. Subagent shape produced by the existing fracture/subagent path.
9. MemoryPalace-backed records with `created_at` defaults to prove wall-clock
   fields remain outside snapshot identity.
10. Environment serialization containing `__type__: agent` to prove legacy
    serialization remains separate from AgentSnapshot.

The drift report must classify every observed field as one of:

```text
snapshot_candidate
derived_descriptor
memory_ref_candidate
capability_grant_candidate
runtime_envelope
legacy_only
blocked_until_deferred_gate
unknown_requires_review
```

GO criteria:

```text
0 unknown_requires_review fields
0 live runtime handles classified as snapshot_candidate
all memory fields either memory_ref_candidate or explicitly scoped for v1
all tool/capability fields excluded or mapped to capability_grant_candidate
all UUID/time/process fields classified runtime_envelope
```

NO-GO criteria:

```text
any live handle in snapshot_candidate
any implicit memory graph marked canonical without memory_ref policy
any function/callable without approved FunctionDescriptor reference
any unknown schema/profile field without fail-closed plan
```

---

## 5. Implementation sequence after this gate-closure patch

Recommended sequence:

```text
P0.5.6  AgentSnapshot runtime planning / field audit
P0.5.7  AgentSnapshot gate closure / drift report + read-only canary (this patch)
P0.5.8  AgentSnapshot schema/value core (standalone, no interpreter integration)
P0.5.9  AgentSnapshot hardening / edge-case coverage
P0.5.10 legacy AgentRuntime.to_dict() drift analysis and flagged adapter planning
P0.5.11 pre-RFC gate closure for AGENT-06 / AGENT-08
P0.6.0  AS2 flagged adapter RFC (design only, if team vote records approval)
P0.6.x  AgentSnapshot integration planning for Environment / Integrate / Dream
```

P0.5.8 must remain standalone. It may define schema dataclasses, a local
schema-version allowlist, fail-closed validators, and pure serialization helpers,
but it must not modify `interpreter.py`, `Environment`, `AgentRuntime.to_dict()`,
actor runtime, memory backends, CVM/opcodes, Integrate, or Dream paths.

---

## 6. Deferred gates after P0.5.7

| Gate | P0.5.7 status | Blocks standalone P0.5.8? | Blocks deployment / integration? |
|---|---|---:|---:|
| AGENT-04 capability policy linkage | PARTIAL — mandatory attenuation specified; policy linkage deferred | No | Yes, for full capability enforcement |
| AGENT-05 CVM visibility criteria | DEFERRED | No | Yes, for any CVM opcode consuming AgentSnapshot |
| AGENT-06 provider/model descriptor drift table | PARTIAL — `model_ref.v1` boundary sufficient for AS2 RFC; provider drift table deferred | No, for AS2 RFC design | Yes, for provider/model compatibility runtime |
| AGENT-07 legacy `AgentRuntime.to_dict()` migration plan | ACTIVE GATE | No | Yes, for any migration of legacy serialization to AgentSnapshot |
| AGENT-08 subagent snapshot boundary | PARTIAL — subagents explicitly out of AS2 v1; future RFC required | No, for AS2 RFC design | Yes, for subagent canonicalization runtime |
| AGENT-11 schema registry | PARTIAL — local fail-closed schema allowlist sufficient for standalone core | No, if local allowlist is implemented | Yes, for deployment / integration / central registry behavior |
| FUNC-03 dependency manifest taxonomy | PARTIAL — not required for standalone AgentSnapshot value core | No | Yes, for FunctionDescriptor runtime registry and dependency validation |
| FUNC-04 schema evolution registry | PARTIAL — local fail-closed allowlist sufficient for standalone core | No, if local allowlist is implemented | Yes, for FunctionDescriptor runtime registry and schema compatibility |

`AGENT-07`, `AGENT-11`, `FUNC-03`, and `FUNC-04` remain blocking for deployment, integration, or registry-backed runtime. P0.5.7 authorizes P0.5.8 standalone schema/value core only under a local fail-closed schema allowlist and only if legacy serialization remains untouched.

---

## 7. Acceptance criteria for P0.5.7 drift/gate patch

P0.5.7 is acceptable only if it:

- [x] reads current runtime shapes without mutating them;
- [x] produces `docs/AGENTSNAPSHOT-RUNTIME-DRIFT-REPORT.md`;
- [x] maps every audited runtime field to Snapshot vs Envelope categories;
- [x] explicitly covers `AgentRuntime`, `Memory`, `Environment`, actor runtime ids,
      `MemoryPalace`, provider/model fields, tools, and subagent shapes;
- [x] emits a GO/NO-GO recommendation for standalone AgentSnapshot schema/value core;
- [x] preserves all existing tests and golden fixtures;
- [x] keeps runtime code locked unless separately authorized;
- [x] adds a read-only canary proving `AgentRuntime.to_dict()` is legacy-only and not a canonical AgentSnapshot shape.

---

## 8. P0.5.7 decision

P0.5.7 authorizes P0.5.8 standalone AgentSnapshot schema/value core only under a local fail-closed schema allowlist. It does not authorize AgentSnapshot runtime integration, `AgentRuntime.to_dict()` migration, FunctionDescriptor runtime registry, schema registry deployment, actor runtime integration, interpreter integration, CVM/opcode visibility, golden fixture rewrites, or hard switches.

```text
Decision: READY FOR READ-ONLY AGENTSNAPSHOT DRIFT AUDIT
Next patch: P0.5.7 AgentSnapshot Runtime Drift Report
Runtime code: LOCKED
```

---

## P0.5.8 status update — standalone schema/value core completed

P0.5.8 implements the first standalone AgentSnapshot code under the local
fail-closed schema/profile allowlist authorized by P0.5.7.

Implemented scope:

```text
synapse/agent_snapshot.py
  standalone value objects
  local schema/profile allowlist
  fail-closed validators
  stable-canonical.v1 payload hashing

tests/test_agentsnapshot_core_p058.py
  standalone unit coverage only
```

No integration was performed. The following remain locked:

```text
AgentRuntime.to_dict() migration
actor_runtime.py integration
interpreter.py integration
builtins.py changes
memory dereference / MemoryPalace integration
CVM/opcodes
Integrate/Dream paths
golden fixtures
FunctionDescriptor runtime registry
central schema/profile registry
```

Next patch: P0.5.9 AgentSnapshot standalone hardening / edge-case coverage.

---

## P0.5.9 status update — standalone hardening completed

P0.5.9 hardens the SA1 value core against edge cases discovered during
adversarial probing. It does not extend the public surface: no new value
objects, no new schema versions, no integration, no FunctionDescriptorRef.

Defects closed in `synapse/agent_snapshot.py`:

```text
external mutation of `config` / `canonical_fields` / `model_ref` mappings
  after construction silently shifted snapshot_hash().
  Fixed: deep-freeze via types.MappingProxyType + tuple recursion stored as
  the canonical attribute value. snapshot_hash() is now stable under any
  external mutation of the original mapping.

duplicate memory_refs ((space_id, key, mode) repeated) silently accepted.
  Fixed: explicit AgentMemoryRefError on exact-duplicate detection.

conflicting access_mode on same (memory_space_id, memory_key) silently
accepted (e.g. 'read' and 'write' on identical address).
  Fixed: explicit AgentMemoryRefError; callers must use 'read-write' for
  combined access.

duplicate capability_grant per tool_namespace silently accepted.
  Fixed: explicit AgentCapabilityGrantError; one declarative grant per tool.

whitespace-only memory_key (`'   '`, `'\\t\\n'`) silently accepted.
  Fixed: explicit AgentMemoryRefError.

AgentIdSeed.alias='' vs alias=None vs alias='   ' produced three distinct
agent_id values, creating an identity drift surface.
  Fixed: _normalize_alias collapses empty/whitespace-only strings to None
  before hashing. agent_id is now identical for all three shapes.
```

Validator path (`validate_agent_snapshot_payload`) gained the same duplicate
checks, so round-trip `from_dict()` enforces the same invariants.

Test surface:

```text
tests/test_agentsnapshot_hardening_p059.py
  35 new tests
  covers mutation safety, duplicate / conflicting refs, alias normalization,
  whitespace-only keys, hash determinism, non-finite floats, non-string keys,
  hash format strictness, runtime-envelope leakage at depth, missing/typed
  payload fields.

tests/test_agentsnapshot_canary_p057.py: unchanged, 2 passed
tests/test_agentsnapshot_core_p058.py:    unchanged, 12 passed
```

Full suite: 775 passed, 1 skipped (P0.5.8 baseline + 35 hardening tests, zero regression).

Lock surface unchanged. The following remain locked:

```text
AgentRuntime.to_dict() migration
actor_runtime.py integration
interpreter.py integration
builtins.py changes
memory dereference / MemoryPalace integration
CVM/opcodes
Integrate/Dream paths
golden fixtures
FunctionDescriptor runtime registry
central schema/profile registry
FunctionDescriptorRef as standalone value object
```

Next patch: P0.5.10 legacy `AgentRuntime.to_dict()` drift analysis (AS2-prep,
read-only comparison of legacy serialization shape against canonical
AgentSnapshot allowlist; still no flagged adapter).

---

## P0.5.10 status update — legacy to_dict() drift analysis completed (AS2-prep)

P0.5.10 captures the actual `AgentRuntime.to_dict()` shape and classifies
every legacy field against the canonical AgentSnapshot v1 allowlist. It does
not implement, propose, or wire up an adapter.

Implemented scope:

```text
docs/AGENTRUNTIME-TODICT-DRIFT-REPORT.md
  observed legacy shape across 9 configurations
  field-by-field classification (migrates_as_is / requires_transform /
    legacy_only / excluded_from_canonical)
  identity surface asymmetry (agent_id, definition_ref, capability_grants,
    model_ref, profile, schema_version absent from legacy)
  soulprint / identity_version drift (in interpreter state, not in to_dict)
  adjacent legacy paths (Environment._json_safe, Environment.to_dict)
  subagent / fracture path classification (no current to_dict surface)
  AS2 adapter design risks R1..R7 (informational, not authorization)

tests/test_agentruntime_todict_drift_p0510.py
  26 read-only tests
  shape invariance across all 9 configurations
  field type invariants
  asymmetry vs canonical AgentSnapshot v1
  live handle isolation
  round-trip stability
  classification anchor (test fails if drift report falls out of sync)
```

GO/NO-GO outcome:

```text
AS2 flagged adapter design (RFC):     AUTHORIZED CONDITIONAL ON TEAM VOTE
AS2 adapter implementation:           NOT AUTHORIZED
AgentRuntime.to_dict() migration:     NOT AUTHORIZED
Environment serialization migration:  NOT AUTHORIZED
Subagent canonicalization (AGENT-08): REMAINS DEFERRED
Central schema registry (AGENT-11):   REMAINS DEFERRED
```

Lock surface unchanged. The following remain locked:

```text
synapse/builtins.py             AgentRuntime / to_dict() migration
synapse/interpreter.py          Environment serialization changes
synapse/actor_runtime.py        actor integration
synapse/agent_snapshot.py       standalone core (no adapter wired in)
synapse/memory.py               MemoryPalace dereference
CVM / opcodes
golden fixtures
FunctionDescriptor runtime registry
central schema registry
flagged adapter
profile selector
```

Next gate: P0.6.x AS2 flagged adapter RFC (design only), conditional on
explicit team vote in `ALPHA3F_PLANNING_GATE.md` and on AS2 RFC addressing
risks R1..R7 from `AGENTRUNTIME-TODICT-DRIFT-REPORT.md` §9.


---

## P0.5.11 status update — pre-RFC gate closure for AS2 design

P0.5.11 partially closes the two remaining AS2-design gates identified after
P0.5.10:

```text
AGENT-06 model/provider descriptor: PARTIAL
AGENT-08 subagent snapshot boundary: PARTIAL
```

The closure is intentionally scoped to AS2 RFC readiness only:

- `AGENT-06` now has a minimal `model_ref.v1` boundary for AS2 design:
  `provider_namespace`, `model_id`, `model_version`, and
  `capability_profile_hash`, with `provider_namespace` restricted to
  `mock | anthropic | openai | local | custom`.
- `AGENT-08` is explicitly out of AS2 v1 scope. Current subagents are AST-level
  constructs and do not have a legacy `AgentRuntime.to_dict()` serialization
  surface. Subagent coordination remains Runtime Envelope / actor-mailbox state.
- AS2 RFC must resolve R5 by choosing either identity omission as a documented
  limitation or identity sourcing from a dedicated runtime/interpreter source.
  Hybrid partial sourcing is forbidden.
- AS2 RFC must not reuse the legacy `{"__type__": "agent", "data": ...}`
  envelope as canonical AgentSnapshot envelope.

P0.5.11 does not authorize AS2 RFC by itself; it removes the pre-RFC ambiguity.
Opening P0.6.0 still requires explicit team vote. Adapter implementation remains
not authorized.


---

## 12. P0.6.0 AS2 Flagged Adapter RFC — design-only gate opened

P0.6.0 opens the AS2 flagged adapter RFC after the P0.5.10 drift report and
P0.5.11 pre-RFC gate closure. It is a documentation-only design patch.

Authorized artifacts:

```text
docs/RFC-AGENT-SNAPSHOT-ADAPTER.md
docs/RFC-AGENT-SNAPSHOT-ADAPTER-REVIEW-NOTES.md
docs/AGENTSNAPSHOT-RUNTIME-PLAN.md
docs/MIGRATION-READINESS-CHECKLIST.md
docs/CHANGELOG.md
docs/ALPHA3F_PLANNING_GATE.md
```

P0.6.0 does not authorize adapter implementation. It only opens structured
review for the future adapter contract.

The AS2 RFC must address the P0.5.10 risks R1..R7 before implementation can be
proposed:

```text
R1 identity asymmetry
R2 model wrapping
R3 memory dereference
R4 capability grant sourcing
R5 identity state sourcing
R6 schema/profile registry boundary
R7 envelope conflict
```

P0.6.0 decisions recorded in the draft:

```text
AGENT-06: model_ref.v1 is used as the design boundary; provider deployment remains future work.
AGENT-08: subagents remain out of AS2 v1; no subagent_snapshot_ref is reserved.
R5: Strategy B selected — identity state requires a dedicated read-only runtime/interpreter source.
R7: canonical AS2 output must not reuse legacy {"__type__": "agent", "data": ...} envelope.
```

Runtime remains locked:

```text
synapse/
tests/
AgentRuntime.to_dict()
Environment._json_safe
interpreter.py
actor_runtime.py
memory backends
CVM / opcodes
golden fixtures
FunctionDescriptor registry
central schema registry
adapter implementation
profile selector code
```

Next step after P0.6.0: structured review of `RFC-AGENT-SNAPSHOT-ADAPTER.md`.
Implementation remains blocked until the AS2 RFC reaches APPROVED status and a
separate implementation patch is authorized.
