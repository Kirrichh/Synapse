# RFC-AGENT-SNAPSHOT-ADAPTER Structured Review Notes

**Status:** APPROVED — AS2 RFC v1.0 approved by Alpha3g P0.6.3 structured team vote  
**Review patch:** Alpha3g P0.6.3  
**Target RFC:** `docs/RFC-AGENT-SNAPSHOT-ADAPTER.md`  
**Process:** governed by `docs/RFC-PROCESS.md`  
**Runtime scope authorized:** none — documentation only  
**Finding prefix:** `AS2-XX`  
**Related inputs:** `AGENTRUNTIME-TODICT-DRIFT-REPORT.md`, `AGENTSNAPSHOT-RUNTIME-PLAN.md`, `RFC-AGENT-CANONICALIZATION.md`, `RFC-FUNCTION-DESCRIPTOR.md`

This file is the process artifact for the AS2 flagged adapter RFC. The RFC is
the product source of truth; this file tracks review findings, blocker status,
and approval gates.

Finding lifecycle follows:

```text
OPEN -> RESOLVED -> VERIFIED
```

Self-verification is forbidden. P0.6.1 is an author/team-resolution patch.
P0.6.2 records independent verification through `AS2-INDEPENDENT-VERIFICATION-MATRIX.md`.

---

## Severity legend

| Severity | Meaning |
|---|---|
| BLOCKER | RFC cannot be approved until resolved. Adapter design would be unsafe or undefined. |
| MAJOR | Required before adapter implementation or must be explicitly deferred as an implementation gate. |
| MINOR | Clarification or forward-compatibility issue. |

## Status legend

| Status | Meaning |
|---|---|
| OPEN | Finding requires action or review. |
| RESOLVED | RFC text was revised and awaits independent verification. |
| VERIFIED | Independent review accepted the resolution. |
| DEFERRED | Accepted future implementation gate. |
| ACKNOWLEDGED | Known v1 boundary; not blocking. |

---

## Finding summary

| ID | Finding | Severity | Status | Related |
|---|---|---:|---|---|
| AS2-01 | Identity source contract must be verified: `agent_id` and identity state cannot be inferred from legacy `to_dict()` | BLOCKER | VERIFIED | R1, R5, AGENT-01 |
| AS2-02 | Model resolver behavior must be verified against `model_ref.v1` and provider allowlist | BLOCKER | VERIFIED | R2, AGENT-06 |
| AS2-03 | Memory mapping strategy is not yet approved: inline memory dump is forbidden, resolver/fail-closed behavior needs decision | BLOCKER | VERIFIED | R3, AGENT-03 |
| AS2-04 | Capability grant sourcing from live tool registry needs policy linkage and fail-closed rule | BLOCKER | VERIFIED | R4, AGENT-04 |
| AS2-05 | Canonical envelope separation from legacy `__type__` marker must be verified | BLOCKER | VERIFIED | R7 |
| AS2-06 | Schema/profile registry behavior remains local-only for design and must be separated from deployment | MAJOR | OPEN | R6, AGENT-11, FUNC-04 |
| AS2-07 | `memory.capacity` / `memory_config` mapping requires explicit AS2 decision | MAJOR | OPEN | P0.5.10 field drift |
| AS2-08 | Subagent exclusion must remain explicit until a future subagent RFC/gate | MINOR | ACKNOWLEDGED | AGENT-08 |
| AS2-09 | Future adapter error taxonomy must align with AgentSnapshot error family | MINOR | RESOLVED | P0.5.8/P0.5.9 |
| AS2-10 | Dual-emission strategy for Environment boundary remains future work | MAJOR | OPEN | Environment._json_safe |

---

## Approval gate

`RFC-AGENT-SNAPSHOT-ADAPTER.md` cannot move to `APPROVAL-CANDIDATE` until:

```text
AS2-01 through AS2-05 are independently VERIFIED, or explicitly split/deferred by team decision.
P0.6.2 satisfies this gate for AS2-01..AS2-05.
No finding permits adapter implementation before RFC approval.
No finding permits changes to legacy AgentRuntime.to_dict() default behavior.
No finding permits reuse of the legacy `__type__` envelope as canonical AgentSnapshot output.
```

P0.6.1 resolves AS2-01..AS2-05. P0.6.2 independently verifies those
resolutions. Runtime implementation remains blocked until a separate scoped
implementation patch is authorized after RFC approval.

---

## AS2-01 — Identity source contract

**Severity:** BLOCKER  
**Status:** VERIFIED  
**Related:** R1, R5, AGENT-01

**Verification:** P0.6.2 independent document review — PASS; see `docs/AS2-INDEPENDENT-VERIFICATION-MATRIX.md`.

**Resolution:** P0.6.1 hardens the identity contract. `AdapterIdentityContext` is
the only canonical identity source. It is complete-or-absent; partial context is
forbidden. Legacy `name` is alias / routing hint only and never an `agent_id`
source. The adapter must not read runtime/interpreter state directly for
identity.

`AdapterIdentityContext` separates:

```text
identity_seed  -> affects agent_id
audit_context  -> provenance metadata, excluded from AgentSnapshot state_hash
```

**Verification criteria for P0.6.2:**

```text
RFC forbids deriving agent_id from to_dict(), name, UUID, id(), process id, or wall-clock.
RFC defines complete-or-absent AdapterIdentityContext semantics.
RFC separates identity_seed from audit_context.
RFC states audit_context does not alter canonical state hash.
```

---

## AS2-02 — Model resolver and provider allowlist

**Severity:** BLOCKER  
**Status:** VERIFIED  
**Related:** R2, AGENT-06

**Verification:** P0.6.2 independent document review — PASS; see `docs/AS2-INDEPENDENT-VERIFICATION-MATRIX.md`.

**Resolution:** P0.6.1 replaces soft allowlist language with a
`StaticModelRegistry` contract. Model mapping requires a local immutable,
append-only, content-addressed registry snapshot. Unknown model/provider strings
raise `ModelRefUnknownError`. `custom` requires an explicit registry entry and is
not a wildcard fallback. Live provider probing and heuristic parsing are
forbidden.

**Verification criteria for P0.6.2:**

```text
RFC requires model_registry_snapshot_hash in AdapterDerivationRecord.
RFC forbids dynamic provider lookup and heuristic parsing.
RFC preserves provider namespace enum from P0.5.11.
RFC states registry updates do not retroactively affect existing replay artifacts.
```

---

## AS2-03 — Memory mapping strategy

**Severity:** BLOCKER  
**Status:** VERIFIED  
**Related:** R3, AGENT-03

**Verification:** P0.6.2 independent document review — PASS; see `docs/AS2-INDEPENDENT-VERIFICATION-MATRIX.md`.

**Resolution:** P0.6.1 selects strict two-phase memory externalization for the
canonical path. The host externalizes legacy memory before adapter invocation and
provides `MemoryRefSource`. The adapter consumes it read-only and validates every
`memory_ref` against a recomputed expected memory space.

Hard requirements:

```text
inline memory dumps are forbidden;
MemoryRefSource missing/null fails closed;
empty memory is explicit [];
derive_memory_space_id is pure over exactly agent_id + memory_space_policy_version;
every memory_ref.memory_space_id must equal expected_memory_space_id;
rewrite/filter/repair of refs is forbidden;
mixed memory spaces fail closed with AdapterMemorySpaceMismatchError;
cross-agent memory sharing is out of AS2 v1 scope.
```

**Verification criteria for P0.6.2:**

```text
RFC describes Phase 1 host preparation and Phase 2 adapter validation.
RFC includes memory_space_policy_version and expected_memory_space_id in derivation record.
RFC distinguishes empty MemoryRefSource [] from missing/null MemoryRefSource.
RFC introduces AdapterMemorySpaceMismatchError.
RFC forbids mixed memory spaces in AS2 v1.
```

---

## AS2-04 — Capability grant sourcing

**Severity:** BLOCKER  
**Status:** VERIFIED  
**Related:** R4, AGENT-04

**Verification:** P0.6.2 independent document review — PASS; see `docs/AS2-INDEPENDENT-VERIFICATION-MATRIX.md`.

**Resolution:** P0.6.1 removes the P0.6.0 permission to inspect live tool
namespaces. `CapabilityGrantSource` is the only source of capability grants.
The adapter must not inspect live tools, `tools.keys()`, callables, decorators,
Python signatures, or runtime registries. Grants must be declarative and
compatible with FunctionDescriptor v1.0 references.

FunctionDescriptor runtime registry enforcement remains deferred under
FUNC-03/FUNC-04 and AGENT-11. AS2 v1 validates descriptor-compatible shape; it
does not prove current runtime authority.

**Verification criteria for P0.6.2:**

```text
RFC contains no "MAY inspect live tools" or equivalent language.
RFC requires CapabilityGrantSource for grants.
RFC defines CapabilityGrantSourceMissingError and CapabilityGrantInvalidRefError.
RFC documents authority verification as a later runtime gate.
```

---

## AS2-05 — Envelope separation and identity/audit state boundary

**Severity:** BLOCKER  
**Status:** VERIFIED  
**Related:** R7

**Verification:** P0.6.2 independent document review — PASS; see `docs/AS2-INDEPENDENT-VERIFICATION-MATRIX.md`.

**Resolution:** P0.6.1 preserves envelope separation and hardens audit handling.
AS2 canonical output must not reuse or merge with the legacy
`{"__type__": "agent", "data": ...}` envelope. `audit_context` lives outside
canonical `AgentSnapshot` state hash and may be represented in derivation/audit
metadata instead.

**Verification criteria for P0.6.2:**

```text
RFC forbids the legacy envelope in AS2 canonical payloads.
RFC defines AdapterEnvelopeConflictError.
RFC states audit_context does not affect AgentSnapshot state hash.
RFC documents AdapterDerivationRecord as provenance, not logical state.
```

---

## AS2-06 — Schema/profile registry boundary

**Severity:** MAJOR  
**Status:** OPEN  
**Related:** R6, AGENT-11, FUNC-04

P0.5.7/P0.5.11 local allowlists are sufficient for design and unit scope but not
runtime deployment. Review must ensure the RFC does not accidentally authorize
central registry behavior. This remains an implementation/deployment gate.

---

## AS2-07 — `memory_config` and capacity mapping

**Severity:** MAJOR  
**Status:** OPEN  
**Related:** P0.5.10 drift report

`memory_config` currently has no effect on `memory.capacity` in legacy
serialization. Future AS2 implementation must decide whether capacity migrates as
config, is ignored, or fails closed when memory settings are ambiguous.

P0.6.1 does not close this because the hardening patch is limited to the five
blocking adapter-safety findings.

---

## AS2-08 — Subagent exclusion

**Severity:** MINOR  
**Status:** ACKNOWLEDGED  
**Related:** AGENT-08

Subagents are out of AS2 v1. P0.6.1 reaffirms that no `subagent_snapshot_ref` is
reserved and that fracture runtime graphs fail closed under AS2 v1 with
`AdapterSubagentOutOfScopeError`.

---

## AS2-09 — Adapter error taxonomy alignment

**Severity:** MINOR  
**Status:** RESOLVED  
**Related:** AgentSnapshot error family

P0.6.1 defines the minimum typed fail-closed error taxonomy for AS2 and forbids
generic catch-all adapter failures. Runtime implementation remains future work,
but the design-level taxonomy is now explicit.

---

## AS2-10 — Environment dual-emission boundary

**Severity:** MAJOR  
**Status:** OPEN  
**Related:** Environment._json_safe

The AS2 RFC describes AgentRuntime adapter behavior, but Environment-level
emission remains future work. P0.6.1 does not authorize Environment
serialization changes.

---

## P0.6.1 vote / resolution record

```text
Vote result: APPROVED TO REVISE
Patch type: doc-only
Runtime code: locked
Tests: locked
Result: AS2-01..AS2-05 moved from OPEN to RESOLVED
Independent verification: completed in P0.6.2
```

P0.6.1 is not a final approval. It is the blocker-closure revision. The next
process step is P0.6.2 independent verification.

---

## P0.6.2 independent verification record

```text
Patch type: doc-only independent verification
Runtime code: locked
Tests: locked
Verification artifact: docs/AS2-INDEPENDENT-VERIFICATION-MATRIX.md
Result: AS2-01..AS2-05 moved from RESOLVED to VERIFIED
Next process step: P0.6.3 final approval
```

P0.6.2 does not authorize adapter implementation. It confirms that the P0.6.1
AS2 RFC is internally consistent and ready for final approval review.

---

## P0.6.3 final approval vote record

```text
Patch type: doc-only final approval
Runtime code: locked
Tests: locked
Vote type: structured team approval
Vote model: role-based quorum
Quorum: met
Blocking objections: none
Result: APPROVED
Approved artifact: RFC-AGENT-SNAPSHOT-ADAPTER.md
Approved version: v1.0
Final verifier role: RFC Process Reviewer / Architecture Review Group
```

### Scope of approval

The P0.6.3 vote approves AS2 RFC v1.0 as a frozen design contract for:

```text
pure deterministic projection model
explicit AdapterIdentityContext input boundary
immutable StaticModelRegistry boundary
two-phase memory externalization protocol
per-ref memory-space validation
explicit CapabilityGrantSource boundary
canonical envelope isolation from legacy __type__ marker
typed fail-closed error taxonomy
AdapterDerivationRecord concept for provenance metadata
```

The P0.6.3 vote explicitly does **not** approve or authorize:

```text
adapter implementation
runtime profile selector
AgentRuntime.to_dict() migration
Environment._json_safe() migration
FunctionDescriptor runtime registry
central schema/profile registry deployment
subagent canonicalization
cross-agent memory sharing
legacy golden fixture rewrites
```

### Known limitations accepted for AS2 v1.0

The following limitations are accepted as non-blocking for AS2 RFC v1.0 approval
and remain implementation-planning or future-RFC concerns:

```text
No AS2 adapter implementation exists yet.
WATCH-01: AdapterIdentityContext presence markers remain a v1 implementation-planning concern.
WATCH-02: historical drift/planning documents may contain superseded wording; RFC-AGENT-SNAPSHOT-ADAPTER v1.0 is authoritative for AS2 canonical semantics.
Authority inflation checks for CapabilityGrantSource remain a future runtime gate.
Cross-agent memory sharing is outside AS2 v1.
AdapterDerivationRecord serialization format remains an implementation-planning concern.
```

### Future gates registry

| Deferred / locked topic | Gate before implementation | Notes |
|---|---|---|
| AS2 adapter code | P0.6.4 implementation plan accepted + explicit P0.6.5 vote | No `synapse/agent_snapshot_adapter.py` before vote. |
| Runtime profile selector | Separate implementation authorization | Not implied by RFC approval. |
| `AgentRuntime.to_dict()` migration | Separate migration RFC/patch | Legacy default remains unchanged. |
| `Environment._json_safe()` migration | Environment dual-emission gate | AS2 v1 does not alter legacy envelope emission. |
| FunctionDescriptor runtime registry | FUNC-03/FUNC-04 runtime gates | AS2 v1 uses schema-compatible refs only. |
| Central schema/profile registry | AGENT-11 / schema registry implementation gate | Local/static boundaries remain design-only. |
| Subagent canonicalization | AGENT-08 future RFC/gate | AS2 v1 excludes subagent snapshots. |
| Cross-agent memory sharing | Future capability-delegation gate | AS2 v1 forbids mixed memory spaces. |
| AdapterDerivationRecord serialization | P0.6.4 implementation planning | Concept approved; concrete serialization TBD. |

### Decision

AS2 RFC v1.0 is approved as the design baseline. Runtime implementation remains
locked. The next authorized process step is P0.6.4 implementation planning /
drift harness design. Adapter implementation requires an explicit later team vote
(P0.6.5 or later) and must remain feature-flagged / opt-in.

