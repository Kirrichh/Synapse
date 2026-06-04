# RFC-AGENT-CANONICALIZATION Structured Review Notes

**Status:** APPROVED — Alpha3g P0.5.5 final team vote complete; RFC v1.0 baseline archived  
**Review patch:** Alpha3g P0.5.5  
**Target RFC:** `docs/RFC-AGENT-CANONICALIZATION.md`  
**Process:** governed by `docs/RFC-PROCESS.md`  
**Runtime scope authorized:** none — documentation only  
**Finding prefix:** `AGENT-XX`  
**Related deferred gates:** STABLE-05, INT-07

This file is a process artifact / audit trail. The RFC remains the product
source of truth after approval. Finding lifecycle follows:

```text
OPEN -> RESOLVED -> VERIFIED
```

Self-verification is forbidden.

---

## Severity legend

| Severity | Meaning |
|---|---|
| BLOCKER | RFC cannot be approved until resolved. Runtime design would be unsafe or undefined. |
| MAJOR | Required before runtime implementation or must be explicitly deferred as an implementation gate. |
| MINOR | Clarification or forward-compatibility issue. |

## Status legend

| Status | Meaning |
|---|---|
| OPEN | Finding requires action or review. |
| RESOLVED | RFC text was revised and awaits independent verification. |
| VERIFIED | Independent review accepted the resolution. |
| DEFERRED | Accepted future implementation gate. |
| ACKNOWLEDGED | Known v1 boundary; not blocking. |
| SPLIT | Finding is intentionally split into a prerequisite RFC before runtime implementation. |
| PARTIALLY RESOLVED | RFC text clarified part of the finding; remaining policy/runtime work stays open or deferred. |
| PARTIAL | Scoped closure sufficient for a named planning/RFC milestone only; not full RESOLVED/VERIFIED. |

---

## Finding summary

| ID | Finding | Severity | Status | Related |
|---|---|---:|---|---|
| AGENT-01 | Agent id derivation rule is not yet specified | BLOCKER | VERIFIED | STABLE-05, INT-07 |
| AGENT-02 | Definition identity depends on unresolved FunctionDescriptor / AST hash boundary | BLOCKER | VERIFIED | STABLE-04, STABLE-05, RFC-FUNCTION-DESCRIPTOR v1.0 |
| AGENT-03 | Memory reference canonical id contract is underspecified | BLOCKER | VERIFIED | STABLE-05, INT-05 |
| AGENT-04 | Capability Grant ownership and scope hashing need policy linkage | MAJOR | DEFERRED | policy RFCs, STABLE-05 |
| AGENT-05 | CVM visibility rules require opcode-level acceptance criteria | MAJOR | DEFERRED | CVM RFCs, STABLE-05 |
| AGENT-06 | Model/provider descriptor drift policy needs compatibility table | MAJOR | PARTIAL | STABLE-02, canonical time, AS2 RFC |
| AGENT-07 | Current `AgentRuntime.to_dict()` compatibility path needs migration plan | MAJOR | DEFERRED | AgentRuntime |
| AGENT-08 | Subagent snapshot boundary needs explicit decision | MAJOR | PARTIAL | fracture/subagent runtime, AS2 RFC |
| AGENT-09 | Runtime envelope exclusion list should be tested against current runtime fields | MINOR | ACKNOWLEDGED | AgentRuntime |
| AGENT-10 | Error taxonomy should align with existing exception modules before implementation | MINOR | ACKNOWLEDGED | runtime errors |
| AGENT-11 | Schema version registry dependency for AgentSnapshot | MAJOR | DEFERRED | RFC-PROCESS, STABLE-03 |

### P0.5.4 status note

AGENT-01, AGENT-02, and AGENT-03 are now independently `VERIFIED` by team
review. `RFC-AGENT-CANONICALIZATION.md` is moved to `APPROVAL-CANDIDATE
v0.4-AC` for final vote. Remaining non-BLOCKER findings are documented as
implementation gates or acknowledged v1 boundaries and do not block the
approval-candidate transition.

---

## Approval gate

`RFC-AGENT-CANONICALIZATION.md` can move to `APPROVAL-CANDIDATE` after
AGENT-01, AGENT-02, and AGENT-03 are independently verified. P0.5.4 satisfies
that gate and records the verification metadata below.

Runtime implementation remains locked until the RFC reaches `APPROVED` and a
separate scoped runtime planning patch authorizes implementation.

---

## AGENT-01 — Agent id derivation rule is not yet specified

**Severity:** BLOCKER  
**Status:** VERIFIED  
**Target RFC area:** agent instance snapshot / stable agent id  
**Related IDs:** STABLE-05, INT-07  
**Resolution plan:** define how a stable `agent_id` is derived, recorded, or
referenced. The rule must avoid random UUIDs unless recorded-and-consumed.

### Concern

The draft requires an `agent_id` but does not yet specify whether that id is
provided by recorded event, derived from definition/config/material, or assigned
by a deterministic registry.

### Required outcome

The RFC must define a v1 id policy or explicitly make `agent_id` a deferred
prerequisite before runtime implementation.

### P0.5.1 resolution

**Status:** VERIFIED — independent team review accepted P0.5.1 resolution.

`RFC-AGENT-CANONICALIZATION.md` now defines:

```text
agent_id = sha256(stable-canonical.v1(agent_id_seed))
```

with `parent_anchor = parent_event_hash OR genesis_config_hash`, an assigned /
recorded / replay-consumed `spawn_nonce`, explicit exclusion of `causal_index`
from the seed, alias non-uniqueness semantics, and fail-closed
`AgentIdCollisionError` handling.

**Verification owner:** team / independent reviewer.
**Verification target:** RFC §7.1 Agent ID derivation.

### P0.5.4 independent verification

**Status:** VERIFIED.
**Verified by:** Team review.
**Verification method:** independent specification review against the v1 agent
identity contract and `stable-canonical.v1` seed requirements.
**Result:** accepted. The formula is deterministic, cold-start safe through
`genesis_config_hash`, excludes host UUID/object identity/wall-clock sources,
and defines fail-closed `AgentIdCollisionError`.

---

## AGENT-02 — Definition identity depends on unresolved FunctionDescriptor / AST hash boundary

**Severity:** BLOCKER  
**Status:** VERIFIED  
**Target RFC area:** Canonical Agent Definition  
**Related IDs:** STABLE-04, STABLE-05  
**Resolution plan:** decide whether v1 agent definitions may reference method
identity, and if so, whether that reference depends on a future FunctionDescriptor RFC.

### Concern

Agent class identity may depend on executable methods. Function/closure
canonicalization is still deferred. The RFC must avoid approving an agent
identity scheme that silently hashes host function objects.

### Required outcome

The RFC must either forbid method-body identity in v1 or pin it to an approved
future FunctionDescriptor contract.

### P0.5.1 resolution

**Status:** VERIFIED — prerequisite satisfied and independently accepted.

`RFC-AGENT-CANONICALIZATION.md` now limits v1 to externally declared,
statically versioned `agent_definition_ref` manifests. It explicitly excludes
Python bytecode, `inspect.getsource()`, `__code__`, host file paths, closure
identity, dynamic definitions, and ad-hoc AST hashing from canonical identity.

Executable agent class identity was delegated to prerequisite
`RFC-FUNCTION-DESCRIPTOR` in P0.5.1. That prerequisite is now approved as
`RFC-FUNCTION-DESCRIPTOR` v1.0 after P0.5.2.3.

### P0.5.3 dependency synchronization

**Status:** RESOLVED — prerequisite satisfied; pending independent verification.

The approved `RFC-FUNCTION-DESCRIPTOR` v1.0 provides the declarative callable
contract identity required by AGENT-02. `RFC-AGENT-CANONICALIZATION.md` now
explicitly depends on `RFC-FUNCTION-DESCRIPTOR` v1.0 APPROVED and states that
`agent_definition_ref.manifest_hash` MAY be based on approved
`function_descriptor_hash` values plus `config_schema_hash` and
`capability_schema_hash`.

P0.5.3 did not mark AGENT-02 as `VERIFIED`; independent team verification was
reserved for P0.5.4.

### P0.5.4 independent verification

**Status:** VERIFIED.
**Verified by:** Team review.
**Verification method:** independent specification review against
`RFC-FUNCTION-DESCRIPTOR` v1.0 and the Agent Definition bridge.
**Result:** accepted. The approved FunctionDescriptor v1.0 satisfies the v1
externally declared function contract prerequisite without introducing Python
bytecode, `inspect`, host path, closure, or runtime object identity into the
Agent RFC canonical path.

**Verification target:** RFC §6 Canonical Agent Definition and §21 P0.5.3 / P0.5.4 status.

---

## AGENT-03 — Memory reference canonical id contract is underspecified

**Severity:** BLOCKER  
**Status:** VERIFIED  
**Target RFC area:** memory refs  
**Related IDs:** STABLE-05, INT-05  
**Resolution plan:** specify the minimum shape and source of a canonical memory
reference id, or make memory refs deferred for v1 runtime.

### Concern

The RFC says snapshots should use memory refs, but the project does not yet have
a fully specified stable memory id derivation rule.

### Required outcome

The RFC must define whether memory refs are required in v1 snapshots, optional,
or deferred until a memory identity RFC exists.

### P0.5.1 resolution

**Status:** VERIFIED — independent team review accepted P0.5.1 resolution.

`RFC-AGENT-CANONICALIZATION.md` now defines `alpha3g.memory_ref.v1` with
`memory_space_id`, `memory_key`, `access_mode`, and profile
`stable-canonical.v1`. It defines:

```text
memory_ref_id = sha256(stable-canonical.v1(memory_ref))
```

and requires fail-closed `MemoryRefNotResolvedError` for unresolved references.
Memory refs are address-only; inline memory graph dumps, backend pointers, host
paths, and storage object repr are forbidden.

**Verification owner:** team / independent reviewer.
**Verification target:** RFC §11 Memory reference canonicalization.

### P0.5.4 independent verification

**Status:** VERIFIED.
**Verified by:** Team review.
**Verification method:** independent specification review against the v1 memory
reference contract.
**Result:** accepted. The memory reference shape is address-only, uses stable
`memory_space_id` and declared `access_mode`, excludes inline memory graph dumps
and host pointers, and fails closed with `MemoryRefNotResolvedError`.

---

## AGENT-04 — Capability Grant ownership and scope hashing need policy linkage

**Severity:** MAJOR  
**Status:** DEFERRED — implementation/policy gate  
**Target RFC area:** capability grants  
**Related IDs:** policy RFCs, STABLE-05  
**Resolution plan:** define whether capability schemas are owned by this RFC or
by policy/tool RFCs.

### P0.5.1 partial resolution

The RFC now defines mandatory attenuation: Runtime Envelope MUST reject tool
calls not covered by a declared `capability_grant`, even if the host has a live
tool object available. It also defines `scope_hash =
sha256(stable-canonical.v1(scope_definition))`.

Remaining open work: policy linkage, ownership model, and formal scope schema
may require a policy/capability RFC or integration with `RFC-FUNCTION-DESCRIPTOR`.

---

## AGENT-05 — CVM visibility rules require opcode-level acceptance criteria

**Severity:** MAJOR  
**Status:** DEFERRED — implementation gate  
**Target RFC area:** CVM Boundary Contract  
**Related IDs:** CVM RFCs, STABLE-05  
**Resolution plan:** add acceptance criteria for CVM-visible snapshot projection
before any opcode consumes agent state.

---

## AGENT-06 — Model/provider descriptor drift policy needs compatibility table

**Severity:** MAJOR  
**Status:** PARTIAL — sufficient for AS2 RFC design only  
**Target RFC area:** model/provider identity descriptor  
**Related IDs:** STABLE-02, canonical time / recorded effects, AS2 flagged adapter RFC  
**Resolution plan:** define how provider/model config changes are represented
and when they break replay compatibility.

### P0.5.11 partial closure

`RFC-AGENT-CANONICALIZATION.md` now defines a minimal `model_ref.v1` boundary
for AS2 design: `provider_namespace`, `model_id`, `model_version`,
`capability_profile_hash`, `schema_version`, and `profile`.

`provider_namespace` is an allowlisted enum (`mock`, `anthropic`, `openai`,
`local`, `custom`) and not a free-form provider string. AS2 RFC may use this
model reference to transform legacy `AgentRuntime.to_dict()["model"]` into
canonical `model_ref` or reject unknown providers fail-closed.

The following remain explicitly out of scope and keep `AGENT-06` from full
`RESOLVED` status: provider/model drift table, provider deprecation policy,
endpoint routing, transport classes, deployment credentials, recorded inference
replay semantics, and deterministic model execution policy.

**Result:** PARTIAL. Enough for AS2 RFC; not enough for deployment/runtime
provider compatibility.

---

## AGENT-07 — Current `AgentRuntime.to_dict()` compatibility path needs migration plan

**Severity:** MAJOR  
**Status:** DEFERRED — implementation gate  
**Target RFC area:** migration strategy  
**Related IDs:** AgentRuntime  
**Resolution plan:** define how current `to_dict()` / `from_dict()` state relates
to future AgentSnapshot and whether it remains a legacy/local profile.

---

## AGENT-08 — Subagent snapshot boundary needs explicit decision

**Severity:** MAJOR  
**Status:** PARTIAL — explicitly out of AS2 v1 scope  
**Target RFC area:** subagents / fracture runtime  
**Related IDs:** fracture/subagent runtime, AS2 flagged adapter RFC  
**Resolution plan:** decide whether subagents use the same schema or a separate
`subagent_snapshot` schema.

### P0.5.11 partial closure

`RFC-AGENT-CANONICALIZATION.md` now states that AS2 v1 covers only legacy
`AgentRuntime.to_dict()` compatibility for concrete `AgentRuntime` instances.
Current `SubAgentDef` is an AST-level construct and not a legacy
`to_dict()` serialization surface.

Subagent coordination currently belongs to runtime control flow / actor mailbox
behavior, which remains Runtime Envelope state and is not canonical replay
identity. AS2 v1 MUST NOT reserve a `subagent_snapshot_ref` field in
`AgentSnapshot v1`; subagent canonicalization requires a future approved schema
or RFC.

**Result:** PARTIAL. Enough to keep AS2 v1 scoped to `AgentRuntime.to_dict()`;
not enough for subagent snapshot runtime.

---

## AGENT-09 — Runtime envelope exclusion list should be tested against current runtime fields

**Severity:** MINOR  
**Status:** ACKNOWLEDGED — v1 review boundary  
**Target RFC area:** runtime envelope exclusion  
**Related IDs:** AgentRuntime  
**Resolution plan:** during implementation planning, map current `AgentRuntime`
fields to canonical vs envelope categories.

---

## AGENT-10 — Error taxonomy should align with existing exception modules before implementation

**Severity:** MINOR  
**Status:** ACKNOWLEDGED — v1 review boundary  
**Target RFC area:** error taxonomy  
**Related IDs:** runtime errors  
**Resolution plan:** decide module placement and inheritance for future agent
snapshot errors before code implementation.


## AGENT-11 — Schema version registry dependency for AgentSnapshot

**Severity:** MAJOR  
**Status:** DEFERRED  
**Target RFC area:** schema/profile registry  
**Related IDs:** RFC-PROCESS, STABLE-03  
**Resolution plan:** runtime implementation must define or integrate a schema
version registry before AgentSnapshot deployment.

### Concern

AgentSnapshot, MemoryRef, CapabilityGrant, and AgentDefinitionRef all depend on
explicit `schema_version` and profile strings. Without a registry and
fail-closed lookup, runtime code could silently accept unknown schemas or drift
between profile versions.

### Required outcome

Before runtime deployment, unknown `schema_version` or profile values MUST raise
`UnknownSchemaVersionError`. This finding does not block RFC revision but blocks
AgentSnapshot runtime implementation.
---

## P0.5.4 Independent Verification Record

```text
Patch: Alpha3g P0.5.4
Transition: REVISED -> APPROVAL-CANDIDATE
Verification type: role-based independent team review
Quorum: met under RFC-PROCESS role-based review practice
Blocking objections: none recorded
Result: accepted
```

### Verified blocker resolutions

| Finding | Verification result | Evidence |
|---|---|---|
| AGENT-01 | VERIFIED | Deterministic `agent_id_seed`, `genesis_config_hash`, assigned `spawn_nonce`, and fail-closed collision handling are specified in the RFC. |
| AGENT-02 | VERIFIED | `RFC-FUNCTION-DESCRIPTOR` v1.0 is APPROVED and provides the declarative contract identity prerequisite for v1 agent definitions. |
| AGENT-03 | VERIFIED | `alpha3g.memory_ref.v1`, `memory_space_id`, `access_mode`, address-only dereference, and fail-closed resolution semantics are specified in the RFC. |

### Deferred non-blocker implementation gates

The following non-BLOCKER findings do not block `APPROVAL-CANDIDATE`, but they
block AgentSnapshot runtime implementation or related runtime merge until
resolved or explicitly superseded by a later approved RFC.

| Finding | Deferral | Target milestone | Implementation gate | Risk if ignored |
|---|---|---|---|---|
| AGENT-04 | accepted | AgentSnapshot runtime planning / policy linkage RFC | Capability grant enforcement and policy linkage | Unauthorized tool access or scope ambiguity. |
| AGENT-05 | accepted | CVM visibility / opcode planning | CVM-visible AgentSnapshot use | Runtime envelope leakage into deterministic CVM state. |
| AGENT-06 | partial | AS2 `model_ref.v1` design boundary | Provider/model descriptor runtime | Provider drift or untracked backend changes. |
| AGENT-07 | accepted | AgentRuntime migration planning | Any `AgentRuntime.to_dict()` compatibility path | Legacy serialization leaking runtime objects. |
| AGENT-08 | partial | Explicit AS2 v1 exclusion | Subagent snapshot runtime | Ambiguous recursive snapshot boundaries. |
| AGENT-11 | accepted | Schema registry runtime planning | AgentSnapshot runtime deployment | Unknown schema/profile accepted silently. |

### Acknowledged minor findings

AGENT-09 and AGENT-10 remain tracked as v1 review boundaries and should be
covered by runtime-field audit and exception-module alignment before runtime
implementation, but they do not block RFC approval-candidate status.
---

## P0.5.5 Approval Vote Record

```text
Patch: Alpha3g P0.5.5
Transition: APPROVAL-CANDIDATE -> APPROVED
Vote type: role-based structured team approval
Quorum criteria: team majority (>= 50% + 1) with >= 60% participation
Quorum result: met
Blocking objections: none recorded
Outcome: APPROVED
Version baseline: RFC-AGENT-CANONICALIZATION v1.0
Final verifier role: RFC Process Reviewer
Runtime authorization: not granted by this vote; runtime requires separate scoped planning patch
```

### Scope of approval

The P0.5.5 vote approves `RFC-AGENT-CANONICALIZATION` v1.0 as the canonical
contract for:

```text
agent_id derivation
genesis_config_hash anchoring
causal spawn_nonce semantics
agent definition references backed by RFC-FUNCTION-DESCRIPTOR v1.0
agent instance snapshot boundary
memory_ref / memory_space_id / access_mode semantics
Runtime Envelope exclusion rules
Capability Grant declaration and mandatory attenuation requirement
CVM Boundary Contract for future deterministic visibility
AgentSnapshot implementation acceptance criteria
```

The following are explicitly excluded from this approval and remain blocked by
follow-up gates:

```text
AgentSnapshot runtime implementation
FunctionDescriptor runtime registry implementation
AgentRuntime.to_dict()/from_dict() migration
CVM opcode consumption of AgentSnapshot
provider/model descriptor runtime compatibility table
subagent snapshot runtime
schema version registry deployment
canonical time API
deterministic runtime IDs beyond the approved agent_id formula
```

### Cross-RFC alignment verification

| ID | Alignment check | Result | Notes |
|---|---|---|---|
| ALIGN-AGENT-STABLE-01 | `stable-canonical.v1` is the canonical profile for AgentSnapshot payloads, `agent_id_seed`, `memory_ref`, Capability Grants, and definition refs. | VERIFIED | Aligns with `RFC-STABLE-CANONICAL-IDENTITY.md` v1.0. |
| ALIGN-AGENT-FUNC-01 | `agent_definition_ref.manifest_hash` may be based on approved `function_descriptor_hash` values. | VERIFIED | Satisfies AGENT-02 through `RFC-FUNCTION-DESCRIPTOR.md` v1.0. |
| ALIGN-AGENT-INTEGRATE-01 | Agent canonicalization remains outside existing Integrate Category B artifact behavior until a separate flagged runtime migration patch. | VERIFIED | Preserves Integrate profile-awareness and avoids silent fixture drift. |
| ALIGN-AGENT-CVM-01 | Canonical Agent Instance Snapshot is the only future CVM-visible agent state boundary. | VERIFIED | Runtime Envelope objects remain invisible to deterministic CVM state. |
| ALIGN-AGENT-CAP-01 | Capability Grants are declarative and require mandatory attenuation in future runtime envelopes. | VERIFIED | Detailed policy linkage remains a deferred implementation/policy gate under AGENT-04. |

### Deferred gates preserved after approval

Approval of v1.0 does not close non-BLOCKER runtime gates. The following remain
blocking for AgentSnapshot runtime implementation or related merges:

| Finding | Status after approval | Must close before |
|---|---|---|
| AGENT-04 | DEFERRED / policy linkage gate | Capability grant runtime enforcement beyond the v1 minimum attenuation rule |
| AGENT-05 | DEFERRED / CVM visibility gate | Any CVM opcode consumes AgentSnapshot data |
| AGENT-06 | PARTIAL / AS2 `model_ref` boundary; provider drift gate remains | Provider/model descriptor runtime compatibility |
| AGENT-07 | DEFERRED / migration gate | Any AgentRuntime compatibility serialization path is migrated |
| AGENT-08 | PARTIAL / AS2 v1 exclusion; subagent boundary gate remains | Subagent snapshot runtime |
| AGENT-11 | DEFERRED / schema registry gate | AgentSnapshot runtime deployment |

AGENT-09 and AGENT-10 remain acknowledged v1 review boundaries and must be
covered during runtime implementation planning.

### Known limitations of v1 approval

```text
Executable behavioral identity is delegated to RFC-FUNCTION-DESCRIPTOR v1 contract identity and does not hash function bodies.
Detailed policy ownership and scope authorization semantics remain outside v1 and are tracked by AGENT-04.
CVM opcode-level projection is not implemented by this RFC.
Runtime schema/profile registry enforcement remains required before AgentSnapshot deployment.
No runtime implementation, test fixture migration, or golden replay rewrite is authorized by this approval.
```

### Review trigger / supersession criteria

This RFC MUST be reviewed for revision or supersession if any of the following
occur:

```text
FunctionDescriptor v2 / executable identity changes the agent definition boundary.
AgentSnapshot runtime implementation discovers a mismatch between current AgentRuntime fields and the canonical snapshot/envelope split.
CVM opcode planning requires additional visible agent state.
Capability/policy RFCs redefine grant ownership, scope hashing, or attenuation semantics.
Schema registry implementation requires a breaking schema/profile change.
A future Agent Canonicalization v2 is proposed.
```

### Version lineage

```text
Predecessor: RFC-AGENT-CANONICALIZATION v0.4-AC (APPROVAL-CANDIDATE, P0.5.4)
Approved baseline: RFC-AGENT-CANONICALIZATION v1.0 (P0.5.5)
Successor: none at time of approval
Supersedes: all previous Agent Canonicalization drafts v0.1 through v0.4-AC
```

