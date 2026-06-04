# RFC: Agent Canonicalization

**Status:** APPROVED — Alpha3g P0.5.5 final team vote complete  
**Version:** v1.0  
**Target milestone:** Alpha3g / P0.5.x  
**Patch:** P0.5.5 Agent RFC final team vote & approval  
**Runtime scope authorized:** none — documentation only  
**Process:** governed by `docs/RFC-PROCESS.md`  
**Depends on:** `docs/RFC-STABLE-CANONICAL-IDENTITY.md` v1.0, `docs/RFC-FUNCTION-DESCRIPTOR.md` v1.0 APPROVED, `docs/MIGRATION-READINESS-CHECKLIST.md`, `docs/RFC-INTEGRATE-REPLAY-APPLIER.md`  
**Related findings:** STABLE-05, INT-07
**Approval record:** `docs/RFC-AGENT-CANONICALIZATION-REVIEW-NOTES.md` — Approval Vote Record, Alpha3g P0.5.5

This RFC defines the canonical boundary for agent identity and agent instance
state. Its purpose is to prevent host runtime objects, provider handles,
actor mailboxes, live promises, callbacks, and process-local metadata from
entering Stable Canonical Identity, Integrate write-sets, Dream state deltas,
or future CVM replay.

The current runtime contains an `AgentRuntime` object with fields such as
`name`, `model`, `trust_level`, `trust_scope`, `memory`, `llm`, `tools`, and
`env` (`synapse/builtins.py`). Some of those fields are semantic and may be
eligible for a future canonical snapshot; others are runtime envelope objects
and MUST NOT be hashed as agent identity. This RFC does not implement a runtime
snapshot. It defines the contract that future runtime patches must satisfy.

---

## 1. Normative language

The words **MUST**, **MUST NOT**, **SHOULD**, **MAY**, and **FORBIDDEN** are
normative within Alpha3g design documents.

---

## 2. Motivation

`RFC-STABLE-CANONICAL-IDENTITY.md` v1.0 deliberately keeps agent instances
non-canonical until an AgentSnapshot contract exists. That deferred finding is
tracked as `STABLE-05`; Integrate also tracks agent instance canonicalization as
`INT-07`.

Without this RFC, a future implementation might serialize agents by dumping a
Python object, `__dict__`, `repr()`, pickle payload, provider client, mailbox,
actor handle, or callback. That would break replay determinism and create host
specific state in forensic artifacts.

This RFC establishes the rule:

```text
Agent canonicalization serializes semantic state, not runtime liveness.
```

---

## 3. Design goals

1. Define what agent identity means without depending on Python object identity.
2. Define a canonical snapshot boundary for agent instance state.
3. Explicitly exclude runtime envelope data from canonical hashes.
4. Provide a CVM boundary contract: canonical snapshots are the only agent data
   visible to future CVM deterministic execution.
5. Represent tools/capabilities by declarative grants, not live tool objects.
6. Align all snapshot payloads with `stable-canonical.v1`.
7. Provide a reviewable path to close `STABLE-05` and `INT-07`.

---

## 4. Non-goals for P0.5.0

P0.5.0 is documentation only. The following remain out of scope:

```text
AgentSnapshot runtime class
to_canonical_snapshot() implementation
interpreter.py changes
actor_runtime.py changes
CVM/opcode changes
golden fixture updates
provider adapter changes
memory backend migration
canonical time API
deterministic actor id generation
```

---

## 5. Three-layer agent model

Agent canonicalization is split into three layers:

```text
1. Canonical Agent Definition
2. Canonical Agent Instance Snapshot
3. Non-canonical Runtime Envelope
```

Only layer 1 and a restricted part of layer 2 may contribute to canonical
hashes. Layer 3 is explicitly outside canonical identity.

---

## 6. Canonical Agent Definition

The Agent Definition identifies the type of agent, not one live instance.

Alpha3g v1 deliberately separates **declared definition identity** from
**executable code identity**:

```text
Agent Canonicalization v1 supports only externally declared, statically
versioned agent definitions.

Executable body identity is not defined by this RFC.
Executable function/class identity is delegated to the approved
RFC-FUNCTION-DESCRIPTOR v1.0 declarative contract identity.
```

A v1 definition reference MUST be a stable manifest reference, not a Python
runtime object:

```json
{
  "type": "agent_definition_ref",
  "schema_version": "alpha3g.agent_definition_ref.v1",
  "namespace": "my_module",
  "class_name": "MyAgent",
  "declared_version": "1.0.0",
  "interface_schema_hash": "sha256:...",
  "config_schema_hash": "sha256:...",
  "capability_schema_hash": "sha256:...",
  "manifest_hash": "sha256:...",
  "profile": "stable-canonical.v1"
}
```

`manifest_hash` MUST be computed as:

```text
manifest_hash = sha256(stable-canonical.v1({
  "namespace": namespace,
  "class_name": class_name,
  "declared_version": declared_version,
  "interface_schema_hash": interface_schema_hash,
  "config_schema_hash": config_schema_hash,
  "capability_schema_hash": capability_schema_hash
}))
```

The v1 `manifest_hash` intentionally excludes executable method bodies,
Python bytecode, source-file paths, inline closures, and host-specific class
objects. It covers declared interface/config/capability schema identity only.
Executable code identity for v1 is satisfied by the approved
`RFC-FUNCTION-DESCRIPTOR` v1.0 declarative contract. For multi-method agents,
`agent_definition_ref.manifest_hash` MAY be based on the stable-canonical array
of approved `function_descriptor_hash` values, together with the existing
`config_schema_hash` and `capability_schema_hash`. This synchronization does
not extend v1 to behavioral/executable-body identity; it only binds Agent
Definition identity to the approved declarative FunctionDescriptor baseline.

The descriptor MUST NOT include:

```text
Python object id
memory address
module file path absolute location
host-specific source path
loaded class object repr
function object repr
closure object repr
inspect.getsource() output
__code__ / Python bytecode
inline dynamic class/function definitions
ad-hoc AST hashes not governed by RFC-FUNCTION-DESCRIPTOR
```

If a definition cannot be represented as an externally declared stable manifest
under `stable-canonical.v1`, it is not canonical-serializable in v1. Runtime
implementation for executable agent classes remains blocked until
`RFC-FUNCTION-DESCRIPTOR` is approved or the implementation is explicitly
restricted to the v1 static-manifest-only subset.

## 7. Canonical Agent Instance Snapshot

The Agent Instance Snapshot identifies replay-relevant state for one instance.
It is not a dump of the runtime object.

A v1 snapshot SHOULD have this shape:

```json
{
  "type": "agent_snapshot",
  "schema_version": "alpha3g.agent_snapshot.v1",
  "profile": "stable-canonical.v1",
  "agent_id": "sha256:...",
  "definition_ref": {
    "type": "agent_definition_ref",
    "schema_version": "alpha3g.agent_definition_ref.v1",
    "namespace": "my_module",
    "class_name": "MyAgent",
    "declared_version": "1.0.0",
    "interface_schema_hash": "sha256:...",
    "config_schema_hash": "sha256:...",
    "capability_schema_hash": "sha256:...",
    "manifest_hash": "sha256:...",
    "profile": "stable-canonical.v1"
  },
  "config": {},
  "canonical_fields": {},
  "memory_refs": [],
  "model_ref": {},
  "capability_grants": []
}
```

All values inside `config`, `canonical_fields`, `memory_refs`, `model_ref`, and
`capability_grants` MUST be serializable by `stable-canonical.v1`.

### 7.1 Agent ID derivation

`agent_id` MUST be derived deterministically:

```text
agent_id = sha256(stable-canonical.v1(agent_id_seed))
```

The v1 seed shape is:

```json
{
  "type": "agent_id_seed",
  "schema_version": "alpha3g.agent_id.v1",
  "parent_anchor": "<parent_event_hash OR genesis_config_hash>",
  "definition_hash": "<canonical definition hash>",
  "spawn_nonce": "<assigned causal nonce>",
  "alias": "<canonical alias or null>",
  "namespace": "<agent namespace>"
}
```

`parent_anchor` MUST be either:

```text
parent_event_hash      for an agent spawned from an existing recorded event
genesis_config_hash    for cold-start/bootstrap agents
```

`genesis_config_hash` is the hash of the bootstrap manifest / empty genesis
configuration used for deterministic startup. It MUST NOT be the hash of a
runtime-populated genesis state that already contains agent instances. This
prevents circular dependency where `agent_id` depends on `genesis_state_hash`
and `genesis_state_hash` depends on `agent_id`.

`spawn_nonce` MUST be explicitly assigned, recorded, and consumed during replay.
It MUST NOT be a wall-clock value, UUID, Python object id, process-local counter,
or host-global mutable counter. The nonce is monotonic only within one causal
execution path. Concurrent deterministic spawn requires explicit nonce assignment
in the recorded spawn event.

`causal_index` MAY appear in the recorded spawn event metadata for debugging,
human-readable audit, or deterministic trace ordering, but it MUST NOT
participate in `agent_id` derivation. `agent_id` uniqueness is determined by
`parent_anchor`, `definition_hash`, `spawn_nonce`, `alias`, and `namespace`.

`alias` is a human-readable routing hint. Alias uniqueness is not enforced by
`agent_id` derivation. If two agents in the same causal scope share the same
alias but have different `definition_hash` values, they are distinct agents with
distinct `agent_id` values.

If a recorded `agent_id` already exists in the same causal scope with a different
`definition_hash`, runtime MUST abort with `AgentIdCollisionError`. Silent
overwrite or best-effort renaming is forbidden.

## 8. Field allowlist policy

Agent snapshot fields are **allowlist-only**.

A future implementation MUST NOT serialize all fields except a denylist. It
MUST serialize only fields declared by an approved schema.

Allowed field classes for v1:

```text
primitive stable-canonical values
stable-canonical arrays/maps
memory references by canonical id
model/provider descriptors
capability grants
explicit agent config values
```

Forbidden by default:

```text
any field not declared in the snapshot schema
callables/functions/closures
provider client objects
runtime handles
actor refs unless represented by a future stable reference descriptor
promises
mailboxes
locks
threads/tasks
open files/sockets
caches
```

If a field is not explicitly declared canonical, it MUST fail closed with an
AgentSnapshotSerializationError or equivalent future error.

---

## 9. Non-canonical Runtime Envelope

The Runtime Envelope contains liveness and host integration data. It is required
for execution but forbidden from canonical identity.

Runtime envelope examples:

```text
actor mailbox
scheduler state
thread/task handles
DurablePromise live state
DurableActorRef live routing handle
provider API client
open network connection
open file handle
LLM backend object
function/callback object
tool implementation object
process id
thread id
wall-clock timestamp
ephemeral cache
logger / tracer / metrics handle
```

The envelope MAY be reconstructed by the host after replay or resume, but it
MUST NOT affect the canonical snapshot hash.

---

## 10. CVM Boundary Contract

The Canonical Agent Instance Snapshot is the deterministic boundary between the
host environment and the future Synapse CVM.

Normative rules:

1. The CVM MAY read canonical agent definition descriptors and canonical agent
   instance snapshots.
2. The CVM MUST NOT read the Runtime Envelope.
3. CVM opcodes MUST NOT observe provider handles, live sockets, mailboxes,
   process ids, thread ids, or host client objects through agent snapshots.
4. Any value projected into CVM deterministic state MUST already be representable
   under `stable-canonical.v1` or a future approved stable profile.
5. If the host cannot construct a canonical snapshot without reading envelope
   state, snapshot construction MUST fail closed.

This contract prevents CVM execution from accidentally depending on live host
state.

---

## 11. Memory reference canonicalization

Agent snapshots MUST NOT inline arbitrary memory graphs by default.

Memory-linked state MUST be represented as address-only references:

```json
{
  "type": "memory_ref",
  "schema_version": "alpha3g.memory_ref.v1",
  "memory_space_id": "<stable memory space id>",
  "memory_key": "<canonical encoded memory key>",
  "access_mode": "read|write|read-write",
  "profile": "stable-canonical.v1"
}
```

The canonical id is derived as:

```text
memory_ref_id = sha256(stable-canonical.v1(memory_ref))
```

`memory_space_id` identifies a logical memory space, not a host storage object.
It MUST be stable across program restarts and replay runs. If a memory space id
is derived from a runtime instance name, process id, ephemeral storage backend,
host path, or database connection handle, it violates replay determinism.

Recommended derivation:

```text
memory_space_id = sha256(stable-canonical.v1({
  "type": "memory_space_id_seed",
  "schema_version": "alpha3g.memory_space_id.v1",
  "namespace": namespace,
  "space_name": space_name,
  "space_schema_version": space_schema_version
}))
```

`access_mode` declares the intended memory capability for the agent snapshot.
A replay verifier MAY compare recorded access against declared memory refs. A
runtime/storage layer MUST reject memory operations that exceed the declared
`access_mode`.

A `memory_ref` points to an address, not to an inlined memory state. Dereference
is performed by the runtime/storage layer outside the canonical snapshot. If
`memory_space_id` or `memory_key` cannot be resolved, runtime MUST raise
`MemoryRefNotResolvedError`. Silent `null`, empty map, empty list, or best-effort
fallback is forbidden.

The snapshot MUST NOT include:

```text
inline dump of the agent's entire memory graph
Python object reference
memory backend pointer
host filesystem path
database connection handle
storage object repr
wall-clock-derived memory record material
```

Inlining a memory graph is forbidden unless a future RFC defines:

```text
cycle handling
ownership boundaries
snapshot size limits
stable memory id derivation
storage backend compatibility
state-delta interaction
```

This prevents agent snapshots from pulling unbounded or host-derived memory
structures into a single hash.

## 12. Model / provider identity descriptor

A model or provider must be represented by a descriptor, not by a live client.

Example shape:

```json
{
  "type": "model_ref",
  "provider": "mock",
  "model": "mock",
  "config_hash": "sha256:...",
  "schema_version": "alpha3g.model_ref.v1"
}
```

The descriptor MAY include stable semantic configuration such as model name,
provider namespace, deterministic decoding configuration, and policy ids.

It MUST NOT include:

```text
API key
session token
connection pool
HTTP client
provider SDK object
request id unless recorded-and-consumed
wall-clock timestamp
host endpoint auto-discovery result unless recorded-and-consumed
```

---

## 13. Capability Grants

Tools and capabilities are represented by declarative grants, not live tool
objects.

A future grant SHOULD have this shape:

```json
{
  "type": "capability_grant",
  "tool_namespace": "fs_read",
  "scope_hash": "sha256:...",
  "policy_ref": "policy:<stable-id>",
  "schema_version": "alpha3g.capability_grant.v1"
}
```

`scope_hash` MUST be derived from a canonical scope definition:

```text
scope_hash = sha256(stable-canonical.v1(scope_definition))
```

`scope_definition` describes permitted argument types, return type constraints,
resource boundaries, and policy-relevant limits. It MUST NOT be derived from
introspection of a live function object, provider client, Python callable, or
host runtime handle.

Rules:

1. A capability grant describes permission semantics, not a runtime object.
2. Tool implementation objects remain in the Runtime Envelope.
3. Replay MAY reconstruct or relink safe runtime handles from the grant, but the
   grant itself is the only canonical input.
4. If a capability's scope cannot be described canonically, the capability is
   not canonical-serializable.
5. Capability grants MUST be stable-canonical values.
6. The Runtime Envelope MUST reject any tool call not covered by a declared
   `capability_grant`, even if the host runtime physically has a live tool object
   available.

This mandatory attenuation rule turns capability grants into an enforceable
security boundary rather than passive metadata.

This prevents tool objects, database connections, filesystem handles, provider
clients, and callback closures from entering agent hashes.

## 14. Snapshot hash rules

Agent snapshot hashes MUST be computed as:

```text
agent_snapshot_hash = sha256(stable_canonical_json_bytes(agent_snapshot))
```

The hash MUST NOT use:

```text
Python hash()
repr(agent)
pickle bytes
object id
process-local addresses
runtime-created UUIDs unless recorded-and-consumed
```

Snapshots MUST declare:

```text
schema_version
stable-canonical profile
agent definition ref
```

Unknown snapshot schema versions MUST fail closed unless an approved
compatibility table declares otherwise.

---

## 15. Integrate write-set interaction

Until this RFC is approved and implemented, agent instances remain forbidden in
Integrate write-sets.

After a future implementation, Integrate MAY write an agent snapshot only if:

1. The value is a canonical AgentSnapshot payload, not an `AgentRuntime` object.
2. The payload declares `stable-canonical.v1` or a future approved stable profile.
3. The payload excludes Runtime Envelope fields.
4. The write-set entry includes enough profile metadata for replay verification.
5. REPLAY consumes the recorded snapshot and does not reconstruct it from live
   host objects.

This closes the path from `INT-07` to a future implementation gate.

---

## 16. Dream / state-delta interaction

Future Dream consume-only or state-delta replay MAY include agent snapshots only
under the same constraints as Integrate:

```text
snapshot payload recorded
stable profile declared
runtime envelope excluded
memory refs canonicalized
capability grants declarative
```

Dream replay MUST NOT execute host agent logic simply to reconstruct a snapshot.

---

## 17. Error taxonomy

Future runtime work SHOULD introduce explicit errors:

| Error | Meaning |
|---|---|
| `AgentSnapshotSerializationError` | Agent snapshot cannot be canonicalized. |
| `AgentSnapshotSchemaError` | Snapshot schema missing or unsupported. |
| `AgentRuntimeEnvelopeViolation` | Runtime-only object attempted to enter snapshot. |
| `AgentCapabilityGrantError` | Capability cannot be represented declaratively or requested call is not covered by a grant. |
| `AgentMemoryRefError` | Memory reference lacks stable canonical id. |
| `MemoryRefNotResolvedError` | Memory reference cannot be resolved by runtime/storage layer. |
| `AgentDefinitionRefError` | Definition hash/ref unavailable or unstable. |
| `AgentIdCollisionError` | Recorded agent id collides with a different definition in the same causal scope. |
| `UnknownSchemaVersionError` | Snapshot/schema/profile version is unknown to the runtime registry. |

All errors MUST be fail-closed and MUST NOT fall back to `repr()`, raw
`__dict__` serialization, host object identity, or best-effort substitution.

### 17.1 P0.5.7 local schema allowlist for standalone AgentSnapshot core

P0.5.7 partially closes `AGENT-11` only for standalone AgentSnapshot
schema/value core. It does not implement a central schema registry and does not
authorize runtime deployment or integration.

A standalone AgentSnapshot core may proceed only if it uses a local fail-closed
allowlist equivalent to:

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
unknown capability grant schema families MUST raise `UnknownSchemaVersionError`
or a more specific fail-closed schema error.

This local allowlist is sufficient only for P0.5.8 standalone value objects. A
central schema/profile registry remains required before any AgentSnapshot
deployment, actor runtime integration, interpreter integration, CVM visibility,
or legacy serialization migration.


### 17.2 P0.5.11 partial closure for AGENT-06 — `model_ref` v1 boundary for AS2 RFC

P0.5.11 partially closes `AGENT-06` only to the extent required to open the
AS2 flagged adapter RFC. It does not close provider/model runtime compatibility,
historical provider drift, provider deprecation rules, endpoint routing, or
recorded-inference replay semantics.

The minimum canonical model reference for AS2 design is:

```json
{
  "type": "model_ref",
  "schema_version": "alpha3g.model_ref.v1",
  "provider_namespace": "mock|anthropic|openai|local|custom",
  "model_id": "<canonical model identifier>",
  "model_version": "<declared model version or stable provider label>",
  "capability_profile_hash": "sha256:<stable-canonical capability profile hash>",
  "profile": "stable-canonical.v1"
}
```

`provider_namespace` is an allowlisted enum, not a free-form host/provider
string. The v1 allowlist for AS2 design is:

```text
mock
anthropic
openai
local
custom
```

Unknown provider namespaces MUST fail closed during AS2 design validation. The
`custom` namespace is reserved for explicitly declared local/provider manifests;
it MUST NOT be used as a silent fallback for unknown providers.

`model_ref` describes semantic model identity and declared capability profile.
It MUST NOT include deployment transport or runtime endpoint details such as:

```text
endpoint_class
base_url
region
API client object
credential handle
retry/backoff policy object
load balancer route
process-local provider instance
```

`deterministic_mode_hash` is intentionally excluded from `model_ref.v1`. Model
execution determinism, recorded inference, and provider replay compatibility
remain future runtime/replay contracts and are not closed by P0.5.11.

This partial closure is sufficient only for the AS2 RFC to describe how legacy
`AgentRuntime.to_dict()["model"]` can be transformed into a canonical `model_ref`
or rejected fail-closed. Full `AGENT-06` resolution still requires a provider /
model drift table before deployment or runtime provider compatibility.

### 17.3 P0.5.11 partial closure for AGENT-08 — subagents out of AS2 v1 scope

P0.5.11 partially closes `AGENT-08` only for AS2 RFC scoping.

AS2 v1 covers legacy `AgentRuntime.to_dict()` compatibility for concrete
`AgentRuntime` instances. It does not cover subagent canonicalization.

Current `SubAgentDef` is an AST-level construct rather than an
`AgentRuntime.to_dict()` serialization surface. Existing subagent coordination
occurs through runtime control flow / actor mailbox behavior, which belongs to
the Runtime Envelope and is not part of canonical AgentSnapshot replay identity.

Therefore:

```text
subagent snapshots:      OUT OF SCOPE for AS2 v1
subagent_snapshot_ref:   NOT RESERVED in AgentSnapshot v1
subagent runtime ids:    NOT canonicalized by AS2 v1
actor mailbox state:     Runtime Envelope only
```

AS2 MUST NOT add an empty or reserved `subagent_snapshot_ref` field to
`AgentSnapshot v1`. Empty reserved fields create identity-drift surfaces and
should be introduced only by a future approved schema bump or separate
`subagent_snapshot` RFC.

Full `AGENT-08` resolution remains required before any subagent runtime receives
canonical snapshot identity, recursive snapshot semantics, or replay-visible
subagent coordination.

## 18. Migration strategy

P0.5.x migration should proceed in phases:

```text
P0.5.0  RFC draft only
P0.5.1  Agent RFC revision / blocker strategy
P0.5.2  RFC-FUNCTION-DESCRIPTOR draft / revision / approval
P0.5.3  Agent RFC dependency update / AGENT-02 prerequisite satisfaction
P0.5.4  Agent RFC independent verification / approval-candidate gate
P0.5.5  Agent RFC approval vote
P0.5.6  AgentSnapshot runtime planning / drift analysis
P0.5.7  AgentSnapshot gate closure / drift report
P0.5.8  standalone AgentSnapshot schema/value core, if approved
```

Dependency graph:

```text
P0.5.1 Agent RFC Revision
  -> P0.5.2 RFC-FUNCTION-DESCRIPTOR Draft / Approval
  -> P0.5.3 Agent RFC Dependency Update / AGENT-02 RESOLVED
  -> P0.5.4 Agent RFC Independent Verification / Approval Candidate
  -> P0.5.5 Agent RFC Approval
  -> P0.5.6 AgentSnapshot Runtime Planning / Drift Analysis
  -> P0.5.7 AgentSnapshot gate closure / drift report
  -> P0.5.8 AgentSnapshot standalone schema/value core
  -> P0.5.9 AgentSnapshot standalone hardening
  -> P0.5.10 AgentRuntime.to_dict() drift analysis
  -> P0.5.11 AGENT-06/08 pre-RFC gate closure
  -> P0.6.0 AS2 flagged adapter RFC (design only)
```

AgentSnapshot runtime remains blocked until:

```text
Agent RFC is APPROVED
RFC-FUNCTION-DESCRIPTOR v1.0 is approved and AGENT-02 is independently verified
schema version registry gate is defined
runtime field audit confirms current AgentRuntime fields are mapped to Snapshot vs Envelope
```

No runtime patch may serialize agents into stable canonical hashes until this RFC
is approved.

## 19. Acceptance criteria for future runtime implementation

A future AgentSnapshot runtime implementation is acceptable only if:

- [ ] It serializes only allowlisted canonical fields.
- [ ] It excludes Runtime Envelope objects by construction.
- [ ] It represents tools as Capability Grants.
- [ ] It enforces mandatory capability attenuation: undeclared tool calls fail closed.
- [ ] It represents memory through canonical `memory_ref` values by default.
- [ ] It uses `memory_space_id` values stable across restarts and replay runs.
- [ ] It represents model/provider identity through descriptors.
- [ ] It uses `stable-canonical.v1` for all snapshot payloads.
- [ ] It derives `agent_id` from `agent_id_seed` and never from UUID, wall-clock, object id, or process-local counters.
- [ ] It uses `genesis_config_hash` for cold-start anchors, never runtime-populated genesis state hashes.
- [ ] It treats `causal_index` as recorded metadata only, not as `agent_id` seed material.
- [ ] It supports only externally declared static `definition_ref` values until `RFC-FUNCTION-DESCRIPTOR` v1.0 semantics are implemented through a scoped runtime patch.
- [ ] It fails closed on unknown fields, unknown schema versions, callables,
      provider clients, sockets, promises, mailboxes, locks, and host objects.
- [ ] It has dual tests proving that `AgentRuntime` object identity does not
      affect snapshot hashes.
- [ ] It has a verification gate mapping current `AgentRuntime`,
      `actor_runtime.spawn_actor()`, and `MemoryPalace` fields/behaviors to
      Canonical Snapshot vs Runtime Envelope categories.
- [ ] It has drift tests against representative current `AgentRuntime` shapes.
- [ ] It does not change Integrate or Dream behavior without a separate
      feature-flagged migration patch.

## 20. Open questions for structured review

1. What exact review artifact will approve `RFC-FUNCTION-DESCRIPTOR` for
   executable method identity?
2. Should capability grant policy linkage remain in this RFC or move to a
   dedicated capability/policy RFC?
3. How should model/provider descriptors represent provider version drift?
4. What is the transition plan for current `AgentRuntime.to_dict()` and
   `AgentRuntime.from_dict()`?
5. Should subagents share the same snapshot schema or use a separate
   `subagent_snapshot` schema?
6. What CVM opcode constraints are required before snapshots become CVM-visible?
7. What runtime module owns the schema version registry used by AgentSnapshot?
8. Should memory refs support historical dereference modes, or only current
   storage-layer dereference in v1?

## 21. P0.5.3 / P0.5.4 status

P0.5.0 opened this RFC as a DRAFT and authorized no runtime work.

P0.5.1 revised the RFC as a blocker strategy patch:

```text
AGENT-01 resolved by deterministic agent_id derivation.
AGENT-03 resolved by canonical memory_ref derivation.
AGENT-02 split to prerequisite RFC-FUNCTION-DESCRIPTOR.
AGENT-04 partially clarified by mandatory capability attenuation.
AGENT-11 added as a deferred schema registry implementation gate.
```

P0.5.2 created, revised, verified, and approved `RFC-FUNCTION-DESCRIPTOR` as
v1.0. P0.5.3 synchronizes this RFC with that approved prerequisite:

```text
AGENT-02 is now RESOLVED by the approved RFC-FUNCTION-DESCRIPTOR v1.0
prerequisite.
AGENT-02 is not VERIFIED in P0.5.3; independent verification is reserved for
P0.5.4 together with AGENT-01 and AGENT-03.
No new Agent RFC normative requirements are introduced in P0.5.3.
```

P0.5.4 independently verified the Agent RFC blocker resolutions and moved this
RFC to `APPROVAL-CANDIDATE v0.4-AC`:

```text
AGENT-01 VERIFIED — deterministic agent_id derivation is complete for v1.
AGENT-02 VERIFIED — prerequisite RFC-FUNCTION-DESCRIPTOR v1.0 satisfies the
                  declarative executable contract identity boundary.
AGENT-03 VERIFIED — canonical memory_ref derivation is complete for v1.
```

The remaining non-BLOCKER findings are accepted as implementation or runtime
verification gates and do not block `APPROVAL-CANDIDATE`. They do continue to
block AgentSnapshot runtime implementation until resolved or explicitly scoped
by the responsible follow-up patch.

P0.5.5 completed the final team vote and approved this RFC as the immutable
Agent Canonicalization v1.0 baseline:

```text
RFC-AGENT-CANONICALIZATION APPROVED v1.0.
Approval record is archived in RFC-AGENT-CANONICALIZATION-REVIEW-NOTES.md.
AGENT-01 / AGENT-02 / AGENT-03 remain VERIFIED.
AGENT-04..08 and AGENT-11 remain deferred implementation gates.
AGENT-09 / AGENT-10 remain acknowledged v1 review boundaries.
```

Runtime work remains locked until a separate scoped runtime planning patch
authorizes AgentSnapshot implementation. Approval of this RFC authorizes
planning of that runtime work, not direct code changes.
