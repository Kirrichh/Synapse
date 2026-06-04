# RFC-FUNCTION-DESCRIPTOR Structured Review Notes

**Status:** APPROVED — Alpha3g P0.5.2.3 final team vote complete  
**Review patch:** Alpha3g P0.5.2.3  
**Target RFC:** `docs/RFC-FUNCTION-DESCRIPTOR.md`  
**Process:** governed by `docs/RFC-PROCESS.md`  
**Runtime scope authorized:** none — documentation only  
**Finding prefix:** `FUNC-XX`  
**Related deferred gates:** AGENT-02, STABLE-04, STABLE-05

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

---

## Finding summary

| ID | Finding | Severity | Status | Related |
|---|---|---:|---|---|
| FUNC-01 | Captured environment declaration contract may be too permissive or underspecified | BLOCKER | VERIFIED | AGENT-02, STABLE-04 |
| FUNC-02 | Effect policy schema and runtime enforcement boundary are not yet verified against Integrate/Dream barriers | BLOCKER | VERIFIED | NondeterminismBarrier, INT-03, DREAM strict RFC |
| FUNC-03 | Dependency manifest `ref_type` taxonomy and deterministic ordering need review | MAJOR | DEFERRED | AGENT-02, STABLE-03 |
| FUNC-04 | Schema evolution and compatibility registry policy need review | MAJOR | DEFERRED | STABLE-03, AGENT-11 |
| FUNC-05 | v2 executable identity / CVM canonical image gate is deferred but needs explicit future acceptance criteria | MAJOR | OPEN | CVM RFCs, AGENT-02 |
| FUNC-06 | Mapping from FunctionDescriptor to `agent_definition_ref` may be insufficient for multi-method agents | MINOR | OPEN | AGENT-02, AGENT-05 |
| FUNC-07 | v1 behavioral determinism relies on golden fixtures, not descriptor hash | MAJOR | OPEN | Golden Replay, AgentSnapshot runtime |
| FUNC-08 | Capability/effect policy ownership is shared with policy/capability RFCs | MAJOR | OPEN | AGENT-04, policy RFCs |
| FUNC-09 | Forbidden identity source list must be checked against current Python/runtime call paths before implementation | MINOR | OPEN | actor_runtime.py, builtins.py |
| FUNC-10 | Runtime exception taxonomy is not yet defined for descriptor registry failures | MINOR | OPEN | exception taxonomy |

---

## Approval gate

`RFC-FUNCTION-DESCRIPTOR.md` was eligible for `APPROVAL-CANDIDATE` after P0.5.2.2 and is now APPROVED by P0.5.2.3 final team vote because:

```text
FUNC-01 and FUNC-02 are independently VERIFIED.
FUNC-03 and FUNC-04 are explicitly DEFERRED as implementation / schema-registry gates.
FUNC-05 is acknowledged as a future v2 prerequisite with acceptance criteria.
No finding permits Python bytecode/source/inspect as canonical identity input.
P0.5.2.3 vote record reports quorum met and no blocking objections.
```

Runtime implementation remains blocked until a separate scoped implementation patch is authorized.


---

## Approval Vote Record — Alpha3g P0.5.2.3

**RFC:** `RFC-FUNCTION-DESCRIPTOR.md`  
**Previous status:** `APPROVAL-CANDIDATE v0.2-AC`  
**New status:** `APPROVED v1.0`  
**Vote type:** Team architecture approval  
**Vote result:** APPROVED  
**Vote date:** Alpha3g P0.5.2.3  
**Consensus status:** approved; no blocking objections recorded  
**Final verifier role:** RFC Process Reviewer  

### Quorum criteria

```text
Quorum model: role-based team approval
Majority threshold: >= 50% + 1 participating voting roles
Participation threshold: >= 60% of expected review roles represented
Abstentions: count toward participation, not toward approval majority
Blocking objection rule: any BLOCKER-level objection returns the RFC to REVISION
Quorum result: MET
Blocking objections: NONE
```

### Verified by roles

```text
- Architecture review group
- Runtime determinism reviewer
- RFC process reviewer
```

Role-based verification is used for this approval record. Individual names or
handles are intentionally not required by the current `RFC-PROCESS.md`; a future
process RFC may upgrade approvals to identity-based signatures if needed.

### Scope of approval

FunctionDescriptor v1.0 approves **declarative callable contract identity**:

```text
included:
- descriptor namespace and symbol metadata
- input / output schema hashes
- capability schema hash
- effect policy hash and barrier alignment
- dependency manifest hash boundary
- captured environment manifest hash boundary
- stable-canonical.v1 descriptor serialization profile
```

The following are explicitly outside v1.0 approval scope:

```text
excluded:
- executable body identity
- canonical AST hashing
- Python bytecode identity
- host source / inspect-based identity
- CVM image identity
- runtime closure serialization
- FunctionDescriptor runtime registry implementation
```

### Approval rationale

```text
FUNC-01 is VERIFIED: captured environment identity is explicit, fail-closed, and stable-canonical.v1 serializable.
FUNC-02 is VERIFIED: effect policy semantics align with project nondeterminism barriers and fail closed.
FUNC-03 is DEFERRED: dependency manifest taxonomy and cryptographic pinning are implementation gates, not v1 approval blockers.
FUNC-04 is DEFERRED: schema compatibility registry behavior is an implementation gate, not a v1 approval blocker.
No verified finding permits Python bytecode, inspect, source paths, repr(), closure cells, wall-clock values, UUIDs, or runtime object identity as canonical identity inputs.
```

### Cross-RFC alignment verification

```text
ALIGN-FUNC-STABLE-01: VERIFIED
FunctionDescriptor hash semantics are compatible with RFC-STABLE-CANONICAL-IDENTITY stable-canonical.v1.

ALIGN-FUNC-AGENT-01: VERIFIED
FunctionDescriptor v1 provides the prerequisite bridge for RFC-AGENT-CANONICALIZATION AGENT-02 through function_descriptor_hash / agent_definition_ref compatibility.

ALIGN-FUNC-BARRIER-01: VERIFIED
FunctionDescriptor effect_policy vocabulary is aligned with RFC-INTEGRATE-REPLAY-APPLIER and the project NondeterminismBarrier semantics used by Integrate/Dream isolation contracts.
```

These alignment notes record already-verified cross-RFC compatibility. They do
not change the normative body of `RFC-FUNCTION-DESCRIPTOR.md`.

### Known limitations of v1.0 approval

```text
- Executable body identity is not covered by this RFC.
- FUNC-03 dependency manifest taxonomy remains deferred until runtime implementation planning.
- FUNC-04 schema registry compatibility remains deferred until registry implementation planning.
- Runtime implementation is not authorized by this approval alone.
- Cross-host runtime validation remains future work.
```

### Review trigger / superseding conditions

`RFC-FUNCTION-DESCRIPTOR.md` v1.0 must be reviewed for amendment or superseding if:

```text
- canonical CVM compilation reaches design stage;
- executable body identity v2 is proposed;
- schema registry implementation reveals incompatibility with v1.0 descriptor schema;
- Agent Canonicalization approval requires changes to FunctionDescriptor boundary contracts;
- runtime implementation discovers unresolved FUNC-03 or FUNC-04 conflicts.
```

### Version lineage

```text
Predecessor: RFC-FUNCTION-DESCRIPTOR v0.2-AC (APPROVAL-CANDIDATE, P0.5.2.2)
Supersedes: RFC-FUNCTION-DESCRIPTOR v0.1 and v0.2-AC drafts
Successor: none; v2 executable identity not yet drafted
Baseline: v1.0 immutable approval baseline for future implementation patches
```

---

## FUNC-01 — Captured environment declaration contract may be too permissive or underspecified

**Severity:** BLOCKER  
**Status:** VERIFIED  
**Target RFC area:** captured environment boundary  
**Related IDs:** AGENT-02, STABLE-04  
**Resolution patch:** P0.5.2.1  
**Resolution references:** `RFC-FUNCTION-DESCRIPTOR.md` §10.1–§10.5  
**Verification owner:** team / independent reviewer
**Verified by:** Team review
**Verification method:** independent specification review against Integrate/Dream barrier vocabulary and v1 effect contract
**Verification result:** accepted
**Verified by:** Team review
**Verification method:** independent specification review against v1 contract and Agent Canonicalization dependency
**Verification result:** accepted

### Concern

FunctionDescriptor v1 forbids implicit closures, but it still permits explicit
captured binding manifests. The team must verify that this mechanism cannot
become a disguised serialization path for runtime objects.

### Resolution

P0.5.2.1 adds a normative `captured_environment_manifest` schema with:

```text
explicit binding kinds only: value, memory_ref, capability_grant, config_ref, schema_ref
deterministic binding ordering
required hash field per binding kind
explicit empty-manifest canonical form
valid / invalid examples
fail-closed rejection list for runtime repr, object id, closure cell source, module globals, host paths, live provider handles, and non-stable values
```

The empty manifest is no longer implicit. It must be the canonical object:

```json
{
  "type": "captured_environment_manifest",
  "schema_version": "alpha3g.captured_environment_manifest.v1",
  "bindings": [],
  "profile": "stable-canonical.v1"
}
```

`captured_env_hash` is always computed from a canonical object. Missing, `null`,
or free-form empty values are forbidden.

### Acceptance criteria for verification

FUNC-01 moved to `VERIFIED` after independent review confirmed:

```text
the manifest schema is explicit and stable-canonical.v1 serializable
empty manifest canonical form is specified
implicit closure traversal remains forbidden
runtime object bindings are rejected before descriptor hashing
valid / invalid examples match the normative rules
```

## FUNC-02 — Effect policy schema and runtime enforcement boundary are not yet verified against Integrate/Dream barriers

**Severity:** BLOCKER  
**Status:** VERIFIED  
**Target RFC area:** effect policy / nondeterminism barrier alignment  
**Related IDs:** INT-03, DREAM strict RFC, NondeterminismBarrier  
**Resolution patch:** P0.5.2.1  
**Resolution references:** `RFC-FUNCTION-DESCRIPTOR.md` §8.1–§8.5  
**Verification owner:** team / independent reviewer

### Concern

The RFC defines an effect vocabulary (`pure`, `memory_read`, `memory_write`,
`llm_call`, `dream`, `integrate`, `io_*`, actor/promise effects). The team must
ensure that future runtimes can enforce this vocabulary fail-closed and that it
does not undercut existing barrier contracts.

### Resolution

P0.5.2.1 replaces the prior prose-level mapping with a normative effect policy
schema containing:

```text
determinism_policy: pure | recorded | live_only | forbidden
nondeterminism_barrier_class enum: none | memory_read | memory_write | llm | dream | integrate | io | network | actor | promise | host | forbidden
registered effect_namespace vocabulary
effect-policy validation pseudocode
fail-closed runtime enforcement rule
valid pure policy example
invalid live IO policy example for Integrate/Dream strict contexts
```

The runtime context may be stricter than the descriptor but never looser.
Undeclared or context-forbidden effects must raise a fail-closed barrier
violation before or during execution.

### Acceptance criteria for verification

FUNC-02 moved to `VERIFIED` after independent review confirmed:

```text
effect policy schema uses explicit enum fields, not free-form prose
barrier classes map cleanly to existing Integrate/Dream nondeterminism barriers
undeclared effects fail closed
runtime-context strictness rule is unambiguous
pseudocode rejects unknown schema/profile/effect categories before execution
```

## FUNC-03 — Dependency manifest `ref_type` taxonomy and deterministic ordering need review

**Severity:** MAJOR  
**Status:** DEFERRED — implementation/schema-registry gate  
**Target RFC area:** dependency manifest  
**Related IDs:** STABLE-03, AGENT-02  
**Resolution patch:** P0.5.2.1  
**Resolution references:** `RFC-FUNCTION-DESCRIPTOR.md` §9.1–§9.2

### Concern

The dependency manifest must support deterministic ordering and a bounded
`ref_type` taxonomy without allowing host-runtime imports or implicit transitive
state into canonical identity.

### Resolution

P0.5.2.1 adds:

```text
initial ref_type taxonomy
deterministic ordering by (ref_type, ref_hash, declared_version)
required cryptographic ref_hash
weak_pin rules for external boundaries only
fail-closed rejection of unknown ref_type values unless approved by schema registry
```

### Deferred gate

Runtime implementation remains blocked from accepting descriptor dependencies
until the schema/profile registry can validate `ref_type`, schema version, and
compatibility entries fail-closed.

## FUNC-04 — Schema evolution and compatibility registry policy need review

**Severity:** MAJOR  
**Status:** DEFERRED — schema/profile registry implementation gate  
**Target RFC area:** schema evolution  
**Related IDs:** STABLE-03, AGENT-11  
**Resolution patch:** P0.5.2.1  
**Resolution references:** `RFC-FUNCTION-DESCRIPTOR.md` §12

### Concern

The RFC must define how descriptor version drift is handled before runtime call
routing or replay verification can trust descriptor compatibility.

### Resolution

P0.5.2.1 clarifies:

```text
declared_version is included in function_descriptor_hash
minor version bumps create new hashes
major version mismatch is fail-closed
compatibility is an explicit registry decision, not a name-based inference
registry decisions must record source/target descriptor hash, compatibility mode, RFC reference, and expiry/supersession if applicable
unknown schema/profile/compatibility modes fail closed
```

### Deferred gate

Runtime use of descriptor compatibility remains blocked until the schema/profile
registry exists and can enforce the above rules. Without registry approval, only
exact descriptor-hash matches are valid.

## FUNC-05 — v2 executable identity / CVM canonical image gate is deferred but needs explicit future acceptance criteria

**Severity:** MAJOR  
**Status:** OPEN  
**Target RFC area:** future executable identity  
**Related IDs:** AGENT-02, CVM RFCs  
**Resolution plan:** ensure future v2 work is bounded by concrete acceptance criteria.

### Concern

The RFC correctly avoids Python bytecode/source identity, but future executable
identity is still broad. Without acceptance criteria, v2 can become a moving
constraint rather than an actionable track.

### Required outcome

Keep or refine the v2 checklist: canonical IR, parser/lowering version pin,
closure serialization, import manifest, capability-body binding, drift analysis,
and migration plan.

---

## FUNC-06 — Mapping from FunctionDescriptor to `agent_definition_ref` may be insufficient for multi-method agents

**Severity:** MINOR  
**Status:** OPEN  
**Target RFC area:** Agent RFC bridge  
**Related IDs:** AGENT-02, AGENT-05  
**Resolution plan:** review single-method and multi-method bridge rules.

### Concern

The draft provides a single-method bridge and a multi-method manifest. The team
must verify whether method overloads, inherited methods, and capability-per-method
policies need additional fields.

### Required outcome

Either refine the bridge or mark inherited/overloaded methods as v1 limitations.

---

## FUNC-07 — v1 behavioral determinism relies on golden fixtures, not descriptor hash

**Severity:** MAJOR  
**Status:** OPEN  
**Target RFC area:** determinism boundary for v1  
**Related IDs:** Golden Replay, AgentSnapshot runtime  
**Resolution plan:** decide how v1 deployments prove behavior when descriptor hash does not cover the executable body.

### Concern

Two different implementations can share a v1 descriptor if they preserve the
same declared contract. This is acceptable only if the project explicitly moves
behavioral verification to host runtime tests / golden fixtures for v1.

### Required outcome

Approval should state whether golden fixtures are mandatory before any runtime
uses FunctionDescriptor v1 for agent execution.

---

## FUNC-08 — Capability/effect policy ownership is shared with policy/capability RFCs

**Severity:** MAJOR  
**Status:** OPEN  
**Target RFC area:** capability schema and effect policy ownership  
**Related IDs:** AGENT-04, policy RFCs  
**Resolution plan:** determine whether FunctionDescriptor owns only the hash hooks or also the full policy schema.

### Concern

FunctionDescriptor references capability and effect policy hashes. Full policy
semantics may belong to policy/tool RFCs. Over-owning policy here could create
conflicting contracts later.

### Required outcome

Clarify ownership: FunctionDescriptor v1 may define hash boundaries and minimum
mandatory attenuation, while detailed policy semantics may remain in dedicated
policy/capability RFCs.

---

## FUNC-09 — Forbidden identity source list must be checked against current Python/runtime call paths before implementation

**Severity:** MINOR  
**Status:** OPEN  
**Target RFC area:** forbidden identity sources  
**Related IDs:** actor_runtime.py, builtins.py  
**Resolution plan:** before runtime implementation, audit current callable construction paths and ensure none use forbidden sources.

### Concern

The RFC bans Python bytecode, source, closure cells, repr, host paths, and other
host artifacts. Runtime code may still rely on some of these operationally; the
implementation must keep them in the Runtime Envelope only.

### Required outcome

Implementation planning should include a code audit checklist.

---

## FUNC-10 — Runtime exception taxonomy is not yet defined for descriptor registry failures

**Severity:** MINOR  
**Status:** OPEN  
**Target RFC area:** runtime error taxonomy  
**Related IDs:** exception taxonomy, AGENT-11  
**Resolution plan:** align future descriptor registry errors with existing fail-closed naming conventions.

### Concern

The RFC defines fail-closed behavior, but runtime names are not yet specified.
Possible future errors include `FunctionDescriptorNotFoundError`,
`FunctionDescriptorVersionMismatch`, `FunctionEffectPolicyViolation`, and
`CapturedEnvironmentRejectedError`.

### Required outcome

Not required for draft approval, but implementation patches should define a
stable taxonomy before exposing runtime APIs.


---

## P0.5.2.2 verification summary

P0.5.2.2 performs independent verification of the author-level blocker resolutions from P0.5.2.1.

```text
FUNC-01: VERIFIED — captured environment manifest is explicit, fail-closed, and stable-canonical.v1 serializable.
FUNC-02: VERIFIED — effect policy schema uses explicit barrier enums and fail-closed enforcement semantics aligned with Integrate/Dream nondeterminism barriers.
Runtime changes: none.
Spec-content changes beyond process status: none.
Next gate: CLOSED by P0.5.2.3 final vote; RFC is APPROVED v1.0.
```
