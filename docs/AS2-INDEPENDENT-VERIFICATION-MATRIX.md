# AS2 Independent Verification Matrix

**Status:** VERIFIED — Alpha3g P0.6.2 independent verification complete  
**Patch:** P0.6.2 AS2 Independent Verification Matrix  
**Scope:** doc-only verification of `RFC-AGENT-SNAPSHOT-ADAPTER.md` after P0.6.1 hardening  
**Runtime scope authorized:** none  
**Tests authorized:** none  
**Process:** governed by `docs/RFC-PROCESS.md`  
**Verification target:** AS2-01..AS2-05 blocker resolutions  
**Result:** PASS — AS2-01..AS2-05 may move from `RESOLVED` to `VERIFIED`

---

## 1. Verification scope

P0.6.2 performs independent document verification. It does not implement the AS2
adapter, does not define runtime APIs, and does not add tests. The adapter does
not yet exist in code, so runtime tests against `to_agent_snapshot()` would be
premature API design and are explicitly out of scope.

Verification is limited to checking that the P0.6.1 AS2 RFC is internally
consistent, aligned with approved upstream contracts, and consistent with the
legacy runtime boundaries discovered in earlier audits.

Locked for P0.6.2:

```text
synapse/
tests/
AgentRuntime.to_dict()
Environment._json_safe()
adapter implementation
profile selector
golden fixtures
FunctionDescriptor runtime registry
central schema registry
```

---

## 2. Document authority stack

P0.6.2 establishes the following AS2 document authority stack.

### 2.1 Normative for AS2 v1 canonical semantics

```text
docs/RFC-AGENT-SNAPSHOT-ADAPTER.md
```

The P0.6.1 revision of this RFC is the authoritative source for AS2 v1 semantics.
Any wording in older documents that conflicts with this RFC is superseded.

### 2.2 Process authority for blocker state

```text
docs/RFC-AGENT-SNAPSHOT-ADAPTER-REVIEW-NOTES.md
docs/AS2-INDEPENDENT-VERIFICATION-MATRIX.md
```

The review notes track finding lifecycle. This matrix records independent
verification evidence for AS2-01..AS2-05.

### 2.3 Informational / historical inputs

```text
docs/AGENTRUNTIME-TODICT-DRIFT-REPORT.md
docs/AGENTSNAPSHOT-RUNTIME-PLAN.md
docs/ALPHA3F_PLANNING_GATE.md prior to P0.6.1 entries
```

These documents remain valuable audit history. They are not normative for AS2 v1
where they contain superseded wording.

### 2.4 Superseded wording

Any pre-P0.6.1 wording that authorizes a dedicated runtime/interpreter identity
source is superseded by the `AdapterIdentityContext` contract. Any pre-P0.6.1
wording that allows inspection of live tool namespaces, `tools.keys()`, callable
objects, decorators, or runtime registries is superseded by the P0.6.1
zero-introspection rule.

---

## 3. Verification matrix

| ID | Verification check | RFC reference | Method | Result |
|---|---|---|---|---|
| **AS2-V-01** | `AdapterIdentityContext` is the sole complete-or-absent canonical identity source. Legacy `name`, UUID, process id, wall-clock, and `to_dict()` are not identity sources. | RFC §5.1, §8, §9 | Independent doc review against Agent RFC AGENT-01 and P0.5.10 drift report. | **PASS** |
| **AS2-V-02** | `audit_context` (`soulprint`, `identity_version`) is excluded from canonical `AgentSnapshot` state hash and belongs to derivation/audit metadata only. | RFC §5.1, §10, §11 | Independent doc review against P0.6.1 audit-context hardening. | **PASS** |
| **AS2-V-03** | `StaticModelRegistry` is immutable, append-only, content-addressed, and forbids heuristic parsing, wildcard `custom`, and live provider lookup. | RFC §5.2, §10, §13 | Independent doc review against AGENT-06 partial closure. | **PASS** |
| **AS2-V-04** | Memory uses strict two-phase externalization with per-ref validation against `expected_memory_space_id`. Rewrite/filter/repair and mixed memory spaces are forbidden. | RFC §5.3, §7, §10 | Independent doc review against AGENT-03 and P0.6.1 memory invariant. | **PASS** |
| **AS2-V-05** | Empty memory is represented by explicit `[]`; missing or `null` `MemoryRefSource` is a fail-closed error. Empty and missing are not equivalent. | RFC §5.3, §7.5, §10 | Independent doc review for omission/null/empty distinction. | **PASS** |
| **AS2-V-06** | `CapabilityGrantSource` is the sole grant source. The RFC forbids `tools.keys()`, callable/signature/decorator introspection, and runtime tool registry inspection. | RFC §5.4, §8, §10 | Independent doc review against FunctionDescriptor v1.0 dependency boundary. | **PASS** |
| **AS2-V-07** | AS2 canonical output must not reuse the legacy `{"__type__": "agent", "data": ...}` envelope. | RFC §6, §9, §10 | Independent doc review against `Environment._json_safe()` legacy boundary. | **PASS** |
| **AS2-V-08** | Typed error taxonomy covers fail-closed paths for AS2-01..AS2-05 and forbids generic catch-all behavior. | RFC §12 | Independent doc review against review notes AS2-09 and P0.6.1 taxonomy. | **PASS** |
| **AS2-V-09** | Canonical hash stability across future RFC version boundaries is addressed through explicit schema/profile boundaries and derivation metadata, not silent mutation. | RFC §4, §10, §11, §13 | Independent doc review for v1/v2 boundary and derivation record separation. | **PASS** |
| **AS2-V-10** | Every blocker-level AS2 `MUST`/`MUST NOT` violation has a typed failure path or an explicit future implementation gate; no blocker path is left as unspecified behavior. | RFC §5-§13, Review Notes AS2-01..10 | Normative statement/error taxonomy cross-check. | **PASS** |
| **AS2-V-11** | Integrate/replay compatibility is preserved at the design level: AS2 uses `stable-canonical.v1`, avoids legacy envelope collision, and keeps derivation metadata outside logical state hash. | RFC §6, §9, §11 | Cross-track doc review against stable canonical and Integrate profile discipline. | **PASS** |
| **AS2-V-12** | `parent_anchor` and subagent boundaries do not imply recursive AS2 support. Parent anchors are opaque canonical hashes; subagent/fracture runtime graphs remain out of AS2 v1 scope. | RFC §5.1, §8, §13 | Independent doc review against AGENT-08 partial closure. | **PASS** |

---

## 4. Watch items

The following items do not block P0.6.2. They must remain visible before
implementation planning.

| ID | Watch item | P0.6.2 assessment | Required follow-up |
|---|---|---|---|
| **WATCH-01** | AdapterIdentityContext explicit presence markers are documented as future/`SHOULD`, not a normative `MUST`. | Accepted v1 limitation because implementation is still locked and v1 schema is versioned. However, ambiguity between omission, `null`, and empty object must not enter implementation. | P0.6.4 implementation planning must decide whether to upgrade presence markers to `MUST` or enforce equivalent strict schema validation with unknown-field rejection and no null/omission equivalence. |
| **WATCH-02** | Historical planning/drift documents contain superseded wording such as dedicated runtime/interpreter identity source. | Non-blocking. Document authority stack makes P0.6.1 RFC authoritative. Historical docs remain audit artifacts. | Future implementation planning must cite the P0.6.1 RFC, not superseded historical wording. |

---

## 5. Positive path trace (document-only)

This trace is not executable code. It verifies that the RFC describes a complete
projection path without requiring ambient runtime authority.

```text
Given:
  AdapterIdentityContext with complete identity_seed
  StaticModelRegistry snapshot with registry_snapshot_hash
  MemoryRefSource with refs in the expected memory_space_id
  CapabilityGrantSource with FunctionDescriptor-compatible declarative refs
  legacy AgentRuntime read surface {name, model, trust_level, trust_scope, memory}

The RFC specifies:
  agent_id derived from AdapterIdentityContext.identity_seed
  legacy model mapped only through StaticModelRegistry
  memory_refs consumed only from MemoryRefSource
  capability_grants consumed only from CapabilityGrantSource
  audit_context recorded outside AgentSnapshot state hash
  AdapterDerivationRecord emitted for provenance/audit
  canonical AgentSnapshot v1 output using stable-canonical.v1-compatible values

Expected verification outcome:
  deterministic projection is fully specified at the design level;
  no I/O, wall-clock, UUID, live provider lookup, callable inspection,
  runtime registry lookup, or memory inline dump is authorized.
```

Result: **PASS**.

---

## 6. Negative path trace / error coverage

| Invalid condition | Required error / gate | Verification result |
|---|---|---|
| Identity context missing when canonical emission requires identity | `AdapterIdentityContextMissingError` | PASS |
| Identity context partially specified | `AdapterIdentityContextIncompleteError` | PASS |
| Legacy model string absent from `StaticModelRegistry` | `ModelRefUnknownError` | PASS |
| Memory source missing/null while memory handling is required | `MemoryRefSourceMissingError` | PASS |
| Inline memory content provided as canonical memory payload | `AdapterInlineMemoryRejectedError` | PASS |
| Memory refs contain missing, mixed, foreign, or policy-mismatched memory spaces | `AdapterMemorySpaceMismatchError` | PASS |
| Capability grants missing while live tools exist | `CapabilityGrantSourceMissingError` | PASS |
| Capability grant references malformed or incompatible with FunctionDescriptor v1.0 shape | `CapabilityGrantInvalidRefError` | PASS |
| AS2 output attempts to reuse legacy `__type__` / `data` envelope | `AdapterEnvelopeConflictError` | PASS |
| Adapter attempts to read ambient runtime state, wall-clock, UUID, global state, callable signatures, or provider clients | `AdapterAmbientAuthorityError` | PASS |
| Adapter encounters subagent/fracture runtime graph under AS2 v1 | `AdapterSubagentOutOfScopeError` | PASS |

Result: **PASS**.

---

## 7. Cross-track compatibility matrix

| Track | Compatibility check | Result |
|---|---|---|
| Stable Canonical Identity | AS2 values must remain `stable-canonical.v1` serializable; derivation metadata does not redefine canonical profile rules. | PASS |
| Agent Canonicalization | AS2 relies on approved `agent_id`, `memory_ref`, `model_ref`, and capability grant design boundaries; subagents remain out of v1. | PASS |
| FunctionDescriptor | AS2 grants are descriptor-compatible by shape only; runtime registry and authority enforcement remain deferred under FUNC-03/FUNC-04/AGENT-11. | PASS |
| AgentSnapshot standalone core | AS2 output must target valid `AgentSnapshot v1`; audit metadata and derivation records are not mixed into logical state hash. | PASS |
| Integrate / replay profile discipline | AS2 avoids hard-switch behavior and legacy envelope collision; `stable-canonical.v1` profile remains the canonical value profile. | PASS |
| Legacy AgentRuntime boundary | AS2 does not mutate `AgentRuntime.to_dict()` or `Environment._json_safe()` and treats historical legacy surfaces as inputs/boundaries only. | PASS |

---

## 8. Verification conclusion

P0.6.2 verifies that the P0.6.1 AS2 RFC closes the AS2-01..AS2-05 blockers at
the specification level. The RFC is internally consistent, aligned with the
approved AgentSnapshot/FunctionDescriptor/Stable Canonical contracts, and
consistent with the legacy runtime boundaries established by P0.5.10.

Decision:

```text
AS2-01 -> VERIFIED
AS2-02 -> VERIFIED
AS2-03 -> VERIFIED
AS2-04 -> VERIFIED
AS2-05 -> VERIFIED
```

Remaining open or deferred items continue to block implementation as recorded in
review notes and readiness checklists.

Next process gate:

```text
P0.6.3 — AS2 RFC Final Approval
```

Runtime adapter implementation remains **NOT AUTHORIZED**.
